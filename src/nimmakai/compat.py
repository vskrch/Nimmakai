"""OpenAI / Cursor client compatibility helpers.

Cursor and many OpenAI SDKs only read ``delta.content`` / ``message.content``.
NVIDIA Nemotron-style models often stream text in ``reasoning_content`` first
(or only), which makes Cursor look broken or hang. We normalize payloads so
clients always see standard OpenAI fields.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

# Fields some upstreams reject. OpenRouter client-only fields are stripped by
# strip_router_client_fields AFTER parse_auto_router_options — not here.
# prompt_cache_key / user are forwarded (providers ignore unknown fields).
_STRIP_BODY_KEYS = {
    "service_tier",
    "safety_identifier",
    "store",
    "metadata",
}


def openai_error(
    message: str,
    *,
    code: str | None = None,
    type_: str = "invalid_request_error",
    param: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """OpenAI-compatible error envelope (top-level ``error`` object)."""
    err: dict[str, Any] = {
        "message": message,
        "type": type_,
        "code": code,
        "param": param,
    }
    if metadata:
        err["metadata"] = metadata
    return {"error": err}


def wrap_upstream_error(body: Any, *, status: int = 502) -> dict[str, Any]:
    """Normalize non-OpenAI upstream error bodies into the OpenAI envelope."""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body
    raw: Any = body
    if isinstance(body, dict) and "detail" in body and "error" not in body:
        raw = body.get("detail")
    return openai_error(
        str(raw)[:2000] if raw is not None else f"Upstream error HTTP {status}",
        code="upstream_error",
        type_="server_error",
        metadata={"raw": raw if not isinstance(raw, str) or len(raw) < 500 else raw[:500]},
    )


def sanitize_chat_body(body: dict[str, Any]) -> dict[str, Any]:
    """Normalize client request for OpenAI-compatible upstreams (Cursor-safe).

    Raises ValueError with code ``n_not_supported`` when ``n > 1``.
    """
    out = dict(body)

    # max_completion_tokens (newer OpenAI / Cursor) → max_tokens
    if "max_tokens" not in out and out.get("max_completion_tokens") is not None:
        out["max_tokens"] = out.pop("max_completion_tokens")
    else:
        out.pop("max_completion_tokens", None)

    # stream_options is OpenAI-only; keep if stream else drop
    if not out.get("stream"):
        out.pop("stream_options", None)

    for k in list(_STRIP_BODY_KEYS):
        out.pop(k, None)

    n = out.get("n")
    if n not in (None, 1):
        raise ValueError("n_not_supported")

    # Empty tools → drop (some providers 400)
    tools = out.get("tools")
    if tools is not None and not tools:
        out.pop("tools", None)
        if out.get("tool_choice") in (None, "auto", "none"):
            out.pop("tool_choice", None)

    return out


def normalize_message_dict(msg: dict[str, Any]) -> dict[str, Any]:
    """Ensure assistant message has ``content`` when only reasoning was returned.

    Skip mirroring when the message already carries tool_calls / function_call
    so agent clients see a clean tool delta instead of reasoning text mixed in.
    """
    if not isinstance(msg, dict):
        return msg
    content = msg.get("content")
    reasoning = msg.get("reasoning_content") or msg.get("reasoning")
    empty_content = content in (None, "", [])
    has_tools = msg.get("tool_calls") or msg.get("function_call")
    if empty_content and not has_tools and isinstance(reasoning, str) and reasoning:
        msg = {**msg, "content": reasoning}
    # Keep reasoning_content for advanced clients; Cursor ignores it
    return msg


def normalize_completion_json(body: Any, *, routed_model: str | None = None) -> Any:
    """Rewrite non-stream chat.completion JSON for OpenAI clients."""
    if not isinstance(body, dict):
        return body
    out = dict(body)
    if routed_model:
        out["model"] = routed_model
    choices = out.get("choices")
    if isinstance(choices, list):
        new_choices = []
        for ch in choices:
            if not isinstance(ch, dict):
                new_choices.append(ch)
                continue
            ch2 = dict(ch)
            msg = ch2.get("message")
            if isinstance(msg, dict):
                ch2["message"] = normalize_message_dict(dict(msg))
            # text completions style
            if ch2.get("text") in (None, "") and isinstance(ch2.get("message"), dict):
                pass
            new_choices.append(ch2)
        out["choices"] = new_choices
    return out


def _normalize_delta(delta: dict[str, Any]) -> dict[str, Any]:
    d = dict(delta)
    content = d.get("content")
    reasoning = d.get("reasoning_content") or d.get("reasoning")
    empty = content in (None, "")
    has_tools = d.get("tool_calls") or d.get("function_call")
    if empty and not has_tools and isinstance(reasoning, str) and reasoning:
        # Cursor only renders content — mirror reasoning into content
        d["content"] = reasoning
    # Ensure role on first useful delta (Cursor OpenAI client)
    if (d.get("content") or d.get("tool_calls") or d.get("function_call")) and "role" not in d:
        d["role"] = "assistant"
    # tool_calls must stay intact (Cursor agent mode)
    return d


def normalize_sse_chunk_json(
    data: dict[str, Any], *, routed_model: str | None = None
) -> dict[str, Any]:
    out = dict(data)
    if routed_model:
        out["model"] = routed_model
    choices = out.get("choices")
    if isinstance(choices, list):
        new_ch = []
        for ch in choices:
            if not isinstance(ch, dict):
                new_ch.append(ch)
                continue
            ch2 = dict(ch)
            delta = ch2.get("delta")
            if isinstance(delta, dict):
                ch2["delta"] = _normalize_delta(delta)
            msg = ch2.get("message")
            if isinstance(msg, dict):
                ch2["message"] = normalize_message_dict(dict(msg))
            new_ch.append(ch2)
        out["choices"] = new_ch
    return out


_DATA_RE = re.compile(rb"^(data:\s*)(.*)$", re.I)


def transform_sse_bytes(
    chunk: bytes, *, routed_model: str | None = None
) -> bytes:
    """
    Transform one or more SSE lines in a raw chunk.
    Safe to call on partial buffers only if caller splits by lines first.
    """
    if not chunk or chunk.strip() in (b"[DONE]", b"data: [DONE]"):
        return chunk
    # Fast path: no reasoning — only rewrite model via cheap substitution
    if b"reasoning" not in chunk:
        if routed_model is None or b'"model"' not in chunk:
            return chunk
        # Rewrite "model":"..." without full JSON parse
        return _rewrite_model_bytes(chunk, routed_model)

    lines = chunk.split(b"\n")
    out_lines: list[bytes] = []
    for line in lines:
        if not line.startswith(b"data:"):
            out_lines.append(line)
            continue
        payload = line[5:].strip()
        if payload == b"[DONE]" or not payload:
            out_lines.append(line)
            continue
        try:
            obj = json.loads(payload.decode("utf-8", errors="replace"))
        except Exception:
            out_lines.append(line)
            continue
        if isinstance(obj, dict):
            obj = normalize_sse_chunk_json(obj, routed_model=routed_model)
            new_payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            out_lines.append(b"data: " + new_payload.encode("utf-8"))
        else:
            out_lines.append(line)
    # Preserve trailing newline behavior
    joined = b"\n".join(out_lines)
    if chunk.endswith(b"\n") and not joined.endswith(b"\n"):
        joined += b"\n"
    return joined


_MODEL_FIELD_RE = re.compile(rb'"model"\s*:\s*"(?:\\.|[^"\\])*"')


def _rewrite_model_bytes(chunk: bytes, routed_model: str) -> bytes:
    replacement = b'"model":' + json.dumps(routed_model, ensure_ascii=False).encode(
        "utf-8"
    )
    return _MODEL_FIELD_RE.sub(replacement, chunk, count=1)


async def normalize_sse_stream(
    source: AsyncIterator[bytes],
    *,
    routed_model: str | None = None,
) -> AsyncIterator[bytes]:
    """Line-buffer SSE stream and normalize each data: JSON event for Cursor."""
    buffer = b""
    async for raw in source:
        buffer += raw
        while True:
            nl = buffer.find(b"\n")
            if nl < 0:
                break
            line = buffer[: nl + 1]
            buffer = buffer[nl + 1 :]
            yield transform_sse_bytes(line, routed_model=routed_model)
    if buffer:
        yield transform_sse_bytes(buffer, routed_model=routed_model)
