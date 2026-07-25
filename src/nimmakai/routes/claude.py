"""Claude UI / Anthropic Messages API compatible routes.

Endpoints:
- POST /chat
- POST /v1/messages
- POST /messages
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from nimmakai.routes.openai import _chat_like

logger = logging.getLogger(__name__)

router = APIRouter(tags=["claude"])


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif "text" in block:
                    parts.append(str(block["text"]))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content or "")


def is_anthropic_request(body: dict[str, Any], path: str) -> bool:
    """Return True if request matches Anthropic Messages API format or endpoint."""
    if "messages" in path or "system" in body:
        return True
    # Check if messages contain Anthropic content array format
    msgs = body.get("messages", [])
    if isinstance(msgs, list) and msgs:
        first = msgs[0]
        if isinstance(first, dict) and isinstance(first.get("content"), list):
            return True
    return False


def transform_anthropic_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Convert Anthropic Messages API payload to OpenAI Chat Completions payload."""
    openai_body: dict[str, Any] = {}

    # Copy parameters
    for key in ("temperature", "top_p", "stream", "stop"):
        if key in body:
            openai_body[key] = body[key]

    if "max_tokens" in body:
        openai_body["max_tokens"] = body["max_tokens"]

    # Model default
    raw_model = str(body.get("model") or "").strip()
    if not raw_model or raw_model.startswith("claude") or raw_model == "auto":
        openai_body["model"] = "nimmakai/auto"
    else:
        openai_body["model"] = raw_model

    # Construct messages array
    openai_msgs: list[dict[str, Any]] = []

    # Handle system prompt
    system_prompt = body.get("system")
    if system_prompt:
        sys_text = _extract_text_content(system_prompt)
        if sys_text:
            openai_msgs.append({"role": "system", "content": sys_text})

    # Convert Anthropic messages
    anthropic_msgs = body.get("messages", [])
    if isinstance(anthropic_msgs, list):
        for msg in anthropic_msgs:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            content = _extract_text_content(msg.get("content"))
            openai_msgs.append({"role": role, "content": content})

    openai_body["messages"] = openai_msgs
    return openai_body


def transform_openai_to_anthropic_json(openai_resp: dict[str, Any], requested_model: str) -> dict[str, Any]:
    """Convert OpenAI Chat Completion JSON response into Anthropic Message JSON response."""
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    choices = openai_resp.get("choices", [])
    text_content = ""
    stop_reason = "end_turn"

    if choices and isinstance(choices, list):
        first = choices[0]
        if isinstance(first, dict):
            msg_obj = first.get("message", {})
            text_content = msg_obj.get("content", "") or ""
            reason = first.get("finish_reason")
            if reason == "length":
                stop_reason = "max_tokens"
            elif reason == "stop":
                stop_reason = "end_turn"

    usage = openai_resp.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    model_name = openai_resp.get("model") or requested_model or "nimmakai/auto"

    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": [
            {
                "type": "text",
                "text": text_content,
            }
        ],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
        },
    }


async def transform_openai_to_anthropic_sse_stream(
    openai_stream: StreamingResponse, requested_model: str
) -> AsyncGenerator[bytes, None]:
    """Convert OpenAI SSE stream chunks into Anthropic SSE event frames."""
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    model_name = requested_model or "nimmakai/auto"

    # Emit message_start
    msg_start_event = {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": model_name,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }
    yield f"event: message_start\ndata: {json.dumps(msg_start_event)}\n\n".encode("utf-8")

    # Emit content_block_start
    block_start_event = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    }
    yield f"event: content_block_start\ndata: {json.dumps(block_start_event)}\n\n".encode("utf-8")

    out_tokens = 0
    buffer = ""

    async for chunk in openai_stream.body_iterator:
        if isinstance(chunk, bytes):
            buffer += chunk.decode("utf-8", errors="replace")
        else:
            buffer += str(chunk)

        lines = buffer.split("\n")
        buffer = lines.pop()  # Keep incomplete tail line in buffer

        for line in lines:
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    continue
                try:
                    payload = json.loads(data_str)
                    choices = payload.get("choices", [])
                    if choices and isinstance(choices, list):
                        delta = choices[0].get("delta", {})
                        content_piece = delta.get("content")
                        if content_piece:
                            out_tokens += 1
                            delta_event = {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": content_piece},
                            }
                            yield f"event: content_block_delta\ndata: {json.dumps(delta_event)}\n\n".encode("utf-8")
                except Exception:
                    pass

    # Emit content_block_stop
    block_stop_event = {"type": "content_block_stop", "index": 0}
    yield f"event: content_block_stop\ndata: {json.dumps(block_stop_event)}\n\n".encode("utf-8")

    # Emit message_delta
    msg_delta_event = {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": max(1, out_tokens)},
    }
    yield f"event: message_delta\ndata: {json.dumps(msg_delta_event)}\n\n".encode("utf-8")

    # Emit message_stop
    msg_stop_event = {"type": "message_stop"}
    yield f"event: message_stop\ndata: {json.dumps(msg_stop_event)}\n\n".encode("utf-8")


async def _handle_claude_or_chat(request: Request) -> JSONResponse | StreamingResponse:
    """Unified handler for /chat, /v1/messages, and /messages."""
    path = request.url.path
    try:
        body = await request.json()
    except Exception:
        body = {}

    anthropic_format = is_anthropic_request(body, path)
    requested_model = body.get("model", "nimmakai/auto")

    if anthropic_format:
        openai_payload = transform_anthropic_to_openai(body)
    else:
        openai_payload = body
        if not openai_payload.get("model"):
            openai_payload["model"] = "nimmakai/auto"

    async def _custom_receive():
        return {
            "type": "http.request",
            "body": json.dumps(openai_payload).encode("utf-8"),
            "more_body": False,
        }

    # Support x-api-key header for Claude Code CLI and Anthropic SDKs
    scope = dict(request.scope)
    headers = list(scope.get("headers", []))
    x_api_key = request.headers.get("x-api-key") or request.headers.get("X-Api-Key")
    if x_api_key and not request.headers.get("authorization"):
        headers.append((b"authorization", f"Bearer {x_api_key}".encode("utf-8")))
        scope["headers"] = headers

    # Replace receive method on request
    req_clone = Request(scope, _custom_receive)

    resp = await _chat_like(req_clone, upstream_path="/chat/completions")

    if not anthropic_format:
        return resp

    if isinstance(resp, JSONResponse):
        try:
            resp_bytes = resp.body
            resp_json = json.loads(resp_bytes.decode("utf-8"))
            anthropic_json = transform_openai_to_anthropic_json(resp_json, requested_model)
            return JSONResponse(content=anthropic_json, status_code=resp.status_code)
        except Exception as exc:
            logger.warning("Failed to convert OpenAI JSON to Anthropic JSON: %s", exc)
            return resp

    if isinstance(resp, StreamingResponse):
        return StreamingResponse(
            transform_openai_to_anthropic_sse_stream(resp, requested_model),
            status_code=resp.status_code,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return resp


@router.post("/chat", response_model=None)
async def chat_endpoint(request: Request) -> JSONResponse | StreamingResponse:
    return await _handle_claude_or_chat(request)


@router.post("/v1/messages", response_model=None)
async def v1_messages_endpoint(request: Request) -> JSONResponse | StreamingResponse:
    return await _handle_claude_or_chat(request)


@router.post("/messages", response_model=None)
async def messages_endpoint(request: Request) -> JSONResponse | StreamingResponse:
    return await _handle_claude_or_chat(request)
