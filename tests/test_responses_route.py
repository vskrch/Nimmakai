"""Tests for OpenAI Responses API compatibility route (POST /v1/responses).

Verifies the Responses↔Chat translator fixes the upstream 405 Method Not
Allowed error that occurred when /v1/responses was forwarded verbatim to
OpenAI-compatible providers that don't implement the Responses endpoint.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from potato.config import Settings
from potato.main import create_app
from potato.routes.responses import (
    transform_chat_to_responses_json,
    transform_responses_to_chat,
)

# ── Pure transform tests (no app/HTTP) ──────────────────────────────


def test_transform_responses_string_input_to_messages() -> None:
    body = {
        "model": "gpt-4o",
        "input": "Hello, world!",
        "instructions": "Be concise.",
        "max_output_tokens": 100,
        "temperature": 0.5,
    }
    out = transform_responses_to_chat(body)
    assert out["model"] == "gpt-4o"
    assert out["max_tokens"] == 100
    assert out["temperature"] == 0.5
    # instructions → leading system message; input → user message.
    assert out["messages"][0] == {"role": "system", "content": "Be concise."}
    assert out["messages"][1] == {"role": "user", "content": "Hello, world!"}


def test_transform_responses_message_items_to_messages() -> None:
    body = {
        "model": "gpt-4o",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye"},
        ],
    }
    out = transform_responses_to_chat(body)
    assert out["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ]


def test_transform_responses_image_url_passthrough() -> None:
    """input_image_url must become a native OpenAI image_url part, not a text placeholder."""
    body = {
        "model": "gpt-4o",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "what is this?"},
                    {
                        "type": "input_image_url",
                        "image_url": {"url": "https://x/img.png"},
                    },
                ],
            }
        ],
    }
    out = transform_responses_to_chat(body)
    msg = out["messages"][0]
    assert msg["role"] == "user"
    parts = msg["content"]
    assert isinstance(parts, list)
    assert {"type": "text", "text": "what is this?"} in parts
    assert {"type": "image_url", "image_url": {"url": "https://x/img.png"}} in parts


def test_transform_responses_audio_passthrough() -> None:
    """input_audio must become a native OpenAI input_audio part."""
    body = {
        "model": "gpt-4o-audio-preview",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "transcribe"},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": "UklGRiQ=", "format": "wav"},
                    },
                ],
            }
        ],
    }
    out = transform_responses_to_chat(body)
    parts = out["messages"][0]["content"]
    audio_part = next(p for p in parts if p.get("type") == "input_audio")
    assert audio_part["input_audio"]["data"] == "UklGRiQ="
    assert audio_part["input_audio"]["format"] == "wav"


def test_transform_responses_video_passthrough() -> None:
    """input_video must become a native OpenAI video_url part."""
    body = {
        "model": "gpt-4o",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "describe"},
                    {"type": "input_video", "video_url": {"url": "https://x/clip.mp4"}},
                ],
            }
        ],
    }
    out = transform_responses_to_chat(body)
    parts = out["messages"][0]["content"]
    assert {"type": "video_url", "video_url": {"url": "https://x/clip.mp4"}} in parts


def test_transform_responses_internally_tagged_tools() -> None:
    body = {
        "model": "gpt-4o",
        "input": "What's the weather?",
        "tools": [
            {
                "type": "function",
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        "tool_choice": "auto",
    }
    out = transform_responses_to_chat(body)
    assert out["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert out["tool_choice"] == "auto"


def test_transform_responses_drops_unsupported_builtin_tools() -> None:
    body = {
        "model": "gpt-4o",
        "input": "search the web",
        "tools": [{"type": "web_search_preview"}],
    }
    out = transform_responses_to_chat(body)
    # web_search has no Chat equivalent — tools dropped entirely.
    assert "tools" not in out


def test_transform_responses_text_format_to_response_format() -> None:
    body = {
        "model": "gpt-4o",
        "input": "Jane, 54",
        "text": {
            "format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "person",
                    "strict": True,
                    "schema": {"type": "object", "properties": {}},
                },
            }
        },
    }
    out = transform_responses_to_chat(body)
    assert out["response_format"]["type"] == "json_schema"
    assert out["response_format"]["json_schema"]["name"] == "person"
    assert out["response_format"]["json_schema"]["strict"] is True


def test_transform_chat_to_responses_json_basic() -> None:
    chat_resp = {
        "id": "chatcmpl-1",
        "model": "gpt-4o",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Hello!"},
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    out = transform_chat_to_responses_json(chat_resp, "gpt-4o")
    assert out["object"] == "response"
    assert out["model"] == "gpt-4o"
    assert out["status"] == "completed"
    assert out["output_text"] == "Hello!"
    # Last output item is a message with output_text content.
    msg_item = out["output"][-1]
    assert msg_item["type"] == "message"
    assert msg_item["content"][0]["text"] == "Hello!"
    assert out["usage"]["input_tokens"] == 5
    assert out["usage"]["output_tokens"] == 3
    assert out["usage"]["total_tokens"] == 8


def test_transform_chat_to_responses_json_with_tool_calls() -> None:
    chat_resp = {
        "id": "chatcmpl-2",
        "model": "gpt-4o",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"city":"SF"}'},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    out = transform_chat_to_responses_json(chat_resp, "gpt-4o")
    fc_items = [i for i in out["output"] if i["type"] == "function_call"]
    assert len(fc_items) == 1
    assert fc_items[0]["call_id"] == "call_1"
    assert fc_items[0]["name"] == "get_weather"
    assert fc_items[0]["arguments"] == '{"city":"SF"}'


def test_transform_responses_function_call_output_item_becomes_tool_message() -> None:
    body = {
        "model": "gpt-4o",
        "input": [
            {"role": "user", "content": "weather?"},
            {"type": "function_call", "call_id": "c1", "name": "get_weather", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "sunny"},
        ],
    }
    out = transform_responses_to_chat(body)
    # function_call → assistant tool_calls; function_call_output → tool message.
    assert out["messages"][1]["role"] == "assistant"
    assert out["messages"][1]["tool_calls"][0]["id"] == "c1"
    assert out["messages"][2]["role"] == "tool"
    assert out["messages"][2]["tool_call_id"] == "c1"
    assert out["messages"][2]["content"] == "sunny"


def test_transform_responses_carries_through_reasoning_effort_top_level() -> None:
    """Top-level reasoning_effort must survive the Responses→Chat translation."""
    body = {"model": "potato/coding", "input": "hi", "reasoning_effort": "high"}
    out = transform_responses_to_chat(body)
    assert out["reasoning_effort"] == "high"


def test_transform_responses_carries_through_reasoning_effort_nested() -> None:
    """Nested reasoning.effort (OpenAI Responses shape) must map to reasoning_effort."""
    body = {"model": "potato/coding", "input": "hi", "reasoning": {"effort": "high"}}
    out = transform_responses_to_chat(body)
    assert out["reasoning_effort"] == "high"


def test_transform_responses_no_reasoning_effort_when_absent() -> None:
    """No reasoning_effort key when the client didn't send one."""
    body = {"model": "potato/coding", "input": "hi"}
    out = transform_responses_to_chat(body)
    assert "reasoning_effort" not in out


# ── End-to-end HTTP tests (proves no upstream 405) ──────────────────


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        proxy_api_keys=["sk-test"],
        nim_api_keys=["nvapi-test-key"],
        allow_insecure_auth=True,
        catalog_fetch_docs=False,
        catalog_run_probes=False,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_v1_responses_non_stream(client, monkeypatch) -> None:
    """Non-streaming /v1/responses routes to /chat/completions upstream.

    Before the fix, this returned upstream_error 405 because /responses
    was forwarded verbatim to providers that don't implement it.
    """
    captured: dict = {}

    async def fake_request_json(*args, **kwargs):
        # request_json(self, method, path, *, json_body=...) — self is args[0]
        captured["path"] = args[2] if len(args) > 2 else kwargs.get("path")
        captured["body"] = kwargs.get("json_body")
        return (
            200,
            {
                "id": "chatcmpl-1",
                "model": "gpt-4o",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Hello from Responses!"},
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 4},
            },
            {},
            None,
        )

    from potato.upstream import UpstreamClient

    monkeypatch.setattr(UpstreamClient, "request_json", fake_request_json)

    res = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "gpt-4o",
            "input": "Hi",
            "instructions": "Be nice",
            "max_output_tokens": 50,
        },
    )
    assert res.status_code == 200
    data = res.json()
    # Responses-shaped output, not chat-shaped.
    assert data["object"] == "response"
    assert data["output_text"] == "Hello from Responses!"
    assert data["status"] == "completed"
    # Proves routing went to /chat/completions, NOT /responses (the 405 cause).
    assert captured["path"] == "/chat/completions"
    # instructions → leading system message (merged with universal system prompt).
    sys0 = captured["body"]["messages"][0]
    assert sys0["role"] == "system"
    assert "Be nice" in sys0["content"]
    assert captured["body"]["messages"][1] == {"role": "user", "content": "Hi"}
    assert captured["body"]["max_tokens"] == 50


def test_v1_responses_x_api_key_auth(client, monkeypatch) -> None:
    """x-api-key header (Anthropic SDK / some OpenAI SDK configs) is accepted."""

    async def fake_request_json(*args, **kwargs):
        return (
            200,
            {"id": "c", "model": "m", "choices": [{"message": {"content": "ok"}}], "usage": {}},
            {},
            None,
        )

    from potato.upstream import UpstreamClient

    monkeypatch.setattr(UpstreamClient, "request_json", fake_request_json)

    res = client.post(
        "/v1/responses",
        headers={"x-api-key": "sk-test"},
        json={"model": "gpt-4o", "input": "hi"},
    )
    assert res.status_code == 200


def test_v1_responses_stream(client, monkeypatch) -> None:
    """Streaming /v1/responses emits Responses SSE events, not Chat deltas."""

    async def fake_stream(*args, **kwargs):
        async def gen():
            yield b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
            yield b'data: {"choices":[{"finish_reason":"stop","delta":{}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return 200, gen(), {"content-type": "text/event-stream"}, None

    from potato.upstream import UpstreamClient

    monkeypatch.setattr(UpstreamClient, "stream", fake_stream)

    res = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={"model": "gpt-4o", "input": "hi", "stream": True},
    )
    assert res.status_code == 200
    body = res.text
    # Must emit Responses semantic events, not raw Chat deltas.
    assert "event: response.created" in body
    assert "event: response.output_item.added" in body
    assert "event: response.output_text.delta" in body
    assert "event: response.completed" in body
    # The translated text must be present.
    assert "Hello" in body
    assert "world" in body
