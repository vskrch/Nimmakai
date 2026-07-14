"""Cursor / OpenAI compatibility transforms."""

from __future__ import annotations

import json

import pytest

from nimmakai.compat import (
    normalize_completion_json,
    normalize_sse_stream,
    sanitize_chat_body,
    transform_sse_bytes,
)


def test_sanitize_maps_max_completion_tokens() -> None:
    body = sanitize_chat_body(
        {"model": "auto", "max_completion_tokens": 100, "messages": []}
    )
    assert body["max_tokens"] == 100
    assert "max_completion_tokens" not in body


def test_normalize_json_fills_content_from_reasoning() -> None:
    body = {
        "model": "x",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "hello world",
                }
            }
        ],
    }
    out = normalize_completion_json(body, routed_model="nim/foo")
    assert out["model"] == "nim/foo"
    assert out["choices"][0]["message"]["content"] == "hello world"


def test_transform_sse_mirrors_reasoning_to_content() -> None:
    raw = (
        b'data: {"choices":[{"delta":{"reasoning_content":"The","role":"assistant"}}],'
        b'"model":"nvidia/x","object":"chat.completion.chunk"}\n'
    )
    out = transform_sse_bytes(raw, routed_model="nim/nvidia/x")
    assert b"data: " in out
    payload = json.loads(out.split(b"data: ", 1)[1].strip())
    assert payload["choices"][0]["delta"]["content"] == "The"
    assert payload["model"] == "nim/nvidia/x"


@pytest.mark.asyncio
async def test_normalize_sse_stream_async() -> None:
    async def src():
        yield (
            b'data: {"choices":[{"delta":{"reasoning_content":"Hi"}}],'
            b'"object":"chat.completion.chunk"}\n\n'
        )
        yield b"data: [DONE]\n\n"

    chunks = [c async for c in normalize_sse_stream(src(), routed_model="nim/m")]
    joined = b"".join(chunks)
    assert b'"content":"Hi"' in joined or b'"content": "Hi"' in joined
    assert b"[DONE]" in joined
