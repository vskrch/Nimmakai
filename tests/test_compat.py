"""Cursor / OpenAI compatibility transforms."""

from __future__ import annotations

import json

import pytest

from potato.compat import (
    inject_system_prompt,
    normalize_completion_json,
    normalize_reasoning_effort,
    normalize_sse_stream,
    sanitize_chat_body,
    transform_sse_bytes,
)


class _FakeLadder:
    def __init__(self, caps: dict[str, dict[str, bool]] | None = None):
        self.capabilities = caps or {}


class _FakeRegistry:
    def __init__(self, caps: dict[str, dict[str, bool]] | None = None):
        self.ladder = _FakeLadder(caps)


def test_sanitize_maps_max_completion_tokens() -> None:
    body = sanitize_chat_body({"model": "auto", "max_completion_tokens": 100, "messages": []})
    assert body["max_tokens"] == 100
    assert "max_completion_tokens" not in body


def test_normalize_json_keeps_reasoning_separate_from_content() -> None:
    """RC-1: non-streaming must NOT mirror reasoning_content into content."""
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
    msg = out["choices"][0]["message"]
    assert msg["content"] == ""
    assert msg["reasoning_content"] == "hello world"


def test_transform_sse_does_not_mirror_reasoning_to_content() -> None:
    """RC-1/RC-3: streaming must NOT mirror reasoning into content.

    Thinking-phase deltas keep content absent and reasoning_content populated.
    """
    raw = (
        b'data: {"choices":[{"delta":{"reasoning_content":"The","role":"assistant"}}],'
        b'"model":"nvidia/x","object":"chat.completion.chunk"}\n'
    )
    out = transform_sse_bytes(raw, routed_model="nim/nvidia/x")
    assert b"data: " in out
    payload = json.loads(out.split(b"data: ", 1)[1].strip())
    delta = payload["choices"][0]["delta"]
    assert delta.get("content") in (None, "")
    assert delta["reasoning_content"] == "The"
    assert payload["model"] == "nim/nvidia/x"


@pytest.mark.asyncio
async def test_normalize_sse_stream_keeps_reasoning_separate() -> None:
    """RC-1: SSE stream must NOT mirror reasoning_content into content."""
    async def src():
        yield (
            b'data: {"choices":[{"delta":{"reasoning_content":"Hi"}}],'
            b'"object":"chat.completion.chunk"}\n\n'
        )
        yield b"data: [DONE]\n\n"

    chunks = [c async for c in normalize_sse_stream(src(), routed_model="nim/m")]
    joined = b"".join(chunks)
    assert b'"reasoning_content":"Hi"' in joined or b'"reasoning_content": "Hi"' in joined
    # content must NOT be mirrored from reasoning
    assert b'"content":"Hi"' not in joined and b'"content": "Hi"' not in joined
    assert b"[DONE]" in joined


def test_normalize_json_keeps_tool_calls_empty_content() -> None:
    body = {
        "model": "x",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "thinking...",
                    "tool_calls": [
                        {"id": "t1", "type": "function", "function": {"name": "read_file"}}
                    ],
                }
            }
        ],
    }
    out = normalize_completion_json(body)
    msg = out["choices"][0]["message"]
    assert msg["content"] == ""
    assert "tool_calls" in msg


def test_transform_sse_keeps_tool_calls_empty_content() -> None:
    raw = (
        b'data: {"choices":[{"delta":{"content":"","reasoning_content":"thinking...",'
        b'"tool_calls":[{"id":"t1","type":"function","function":{"name":"read_file"}}]}}]}\n'
    )
    out = transform_sse_bytes(raw, routed_model="nim/m")
    payload = json.loads(out.split(b"data: ", 1)[1].strip())
    delta = payload["choices"][0]["delta"]
    assert delta["content"] == ""
    assert "tool_calls" in delta


# ── Universal system prompt injection ────────────────────────────────


def test_inject_system_prompt_inserts_when_absent() -> None:
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    out = inject_system_prompt(body, "RULES")
    assert out["messages"][0] == {"role": "system", "content": "RULES"}
    assert out["messages"][1]["role"] == "user"


def test_inject_system_prompt_merges_with_existing_string_system() -> None:
    body = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "BE NICE"},
            {"role": "user", "content": "hi"},
        ],
    }
    out = inject_system_prompt(body, "RULES")
    assert len(out["messages"]) == 2
    assert out["messages"][0]["role"] == "system"
    assert out["messages"][0]["content"].startswith("RULES")
    assert "BE NICE" in out["messages"][0]["content"]


def test_inject_system_prompt_skips_empty_prompt() -> None:
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    assert inject_system_prompt(body, "") is body
    assert inject_system_prompt(body, None) is body


def test_inject_system_prompt_skips_when_no_messages() -> None:
    # embeddings-style body — no messages key; leave untouched
    body = {"model": "m", "input": "x"}
    assert inject_system_prompt(body, "RULES") is body


def test_inject_system_prompt_inserts_before_multimodal_system() -> None:
    body = {
        "model": "m",
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": "img-rules"}]},
            {"role": "user", "content": "hi"},
        ],
    }
    out = inject_system_prompt(body, "RULES")
    assert out["messages"][0] == {"role": "system", "content": "RULES"}
    assert out["messages"][1]["content"] == [{"type": "text", "text": "img-rules"}]


# ── Reasoning conformance: RC-1 / RC-2 / RC-3 regression tests ─────────


def _sse_delta_content(raw: bytes) -> dict | None:
    """Parse a single data: SSE line and return the delta dict, or None."""
    if b"data: " not in raw:
        return None
    payload = raw.split(b"data: ", 1)[1].strip()
    if payload == b"[DONE]":
        return None
    obj = json.loads(payload)
    choices = obj.get("choices", [])
    if not choices:
        return None
    return choices[0].get("delta")


def test_rc1_streaming_never_mirrors_reasoning_into_content() -> None:
    """RC-1: thinking-phase deltas must NOT duplicate reasoning into content."""
    # Upstream sends reasoning_content with no content (thinking phase)
    raw = (
        b'data: {"choices":[{"delta":{"reasoning_content":"The user wants"}}],'
        b'"object":"chat.completion.chunk"}\n'
    )
    out = transform_sse_bytes(raw, routed_model="nim/m")
    delta = _sse_delta_content(out)
    assert delta is not None
    assert delta.get("content") in (None, ""), "content must not be mirrored from reasoning"
    assert delta["reasoning_content"] == "The user wants"


def test_rc1_streaming_answer_phase_content_not_duplicated() -> None:
    """RC-1: answer-phase deltas keep content and reasoning separate."""
    # Upstream sends content with reasoning_content null (answer phase)
    raw = (
        b'data: {"choices":[{"delta":{"content":"Hello there","reasoning_content":null}}],'
        b'"object":"chat.completion.chunk"}\n'
    )
    out = transform_sse_bytes(raw, routed_model="nim/m")
    delta = _sse_delta_content(out)
    assert delta is not None
    assert delta["content"] == "Hello there"
    # reasoning_content should not be set to the answer text
    assert delta.get("reasoning_content") != "Hello there"


def test_rc1_nonstreaming_never_mirrors_reasoning_into_content() -> None:
    """RC-1: non-streaming must NOT duplicate reasoning_content into content."""
    body = {
        "model": "x",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "The user wants me to say hello.",
                }
            }
        ],
    }
    out = normalize_completion_json(body)
    msg = out["choices"][0]["message"]
    assert msg["content"] in (None, "")
    assert msg["reasoning_content"] == "The user wants me to say hello."
    # content and reasoning_content must never be equal
    assert msg.get("content") != msg.get("reasoning_content")


def test_rc1_nonstreaming_keeps_separated_when_both_present() -> None:
    """RC-1: when upstream sends distinct content + reasoning, keep both."""
    body = {
        "model": "x",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello there, nice to meet you!",
                    "reasoning_content": "The user wants a six-word greeting.",
                }
            }
        ],
    }
    out = normalize_completion_json(body)
    msg = out["choices"][0]["message"]
    assert msg["content"] == "Hello there, nice to meet you!"
    assert msg["reasoning_content"] == "The user wants a six-word greeting."
    assert msg["content"] != msg["reasoning_content"]


def test_rc2_streaming_canonicalizes_reasoning_field_name() -> None:
    """RC-2: upstream 'reasoning' must be emitted as 'reasoning_content'."""
    raw = (
        b'data: {"choices":[{"delta":{"reasoning":"thinking text"}}],'
        b'"object":"chat.completion.chunk"}\n'
    )
    out = transform_sse_bytes(raw, routed_model="ollama/m")
    delta = _sse_delta_content(out)
    assert delta is not None
    assert "reasoning_content" in delta
    assert delta["reasoning_content"] == "thinking text"
    assert "reasoning" not in delta, "raw 'reasoning' field must be canonicalized away"


def test_rc2_nonstreaming_canonicalizes_reasoning_field_name() -> None:
    """RC-2: non-streaming 'reasoning' must be emitted as 'reasoning_content'."""
    body = {
        "model": "x",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "answer",
                    "reasoning": "thinking text",
                }
            }
        ],
    }
    out = normalize_completion_json(body)
    msg = out["choices"][0]["message"]
    assert "reasoning_content" in msg
    assert msg["reasoning_content"] == "thinking text"
    assert "reasoning" not in msg


def test_rc3_streaming_phase_segregation() -> None:
    """RC-3: full thinking→answer stream must be phase-segregated.

    Thinking chunks: content absent, reasoning_content populated.
    Answer chunks: content populated, reasoning_content absent.
    """
    thinking_chunks = [
        b'data: {"choices":[{"delta":{"reasoning_content":"The user wants"}}]}\n',
        b'data: {"choices":[{"delta":{"reasoning_content":" a greeting."}}]}\n',
    ]
    answer_chunks = [
        b'data: {"choices":[{"delta":{"content":"Hello there"}}]}\n',
        b'data: {"choices":[{"delta":{"content":", world!"}}]}\n',
    ]
    for raw in thinking_chunks:
        out = transform_sse_bytes(raw, routed_model="nim/m")
        delta = _sse_delta_content(out)
        assert delta is not None
        assert delta.get("content") in (None, ""), f"thinking phase must not have content: {delta}"
        assert delta.get("reasoning_content"), "thinking phase must have reasoning_content"
    for raw in answer_chunks:
        out = transform_sse_bytes(raw, routed_model="nim/m")
        delta = _sse_delta_content(out)
        assert delta is not None
        assert delta.get("content"), "answer phase must have content"
        # reasoning_content should not carry the answer text
        rc = delta.get("reasoning_content")
        if rc is not None:
            assert rc != delta["content"], "answer must not be duplicated into reasoning"


def test_conformance_no_chunk_has_equal_content_and_reasoning() -> None:
    """RC-1 conformance: across all upstream field-name variants, content and
    reasoning_content must never be equal in any emitted chunk."""
    cases = [
        # (upstream field, label)
        ({"reasoning_content": "thinking"}, "reasoning_content field"),
        ({"reasoning": "thinking"}, "reasoning field"),
        ({"content": "answer", "reasoning_content": None}, "answer only"),
        ({"content": "answer"}, "answer only no rc key"),
    ]
    for upstream_delta, label in cases:
        raw = (
            b'data: {"choices":[{"delta":' + json.dumps(upstream_delta).encode() + b'}]}\n'
        )
        out = transform_sse_bytes(raw, routed_model="nim/m")
        delta = _sse_delta_content(out)
        assert delta is not None, f"no delta for case: {label}"
        c = delta.get("content")
        rc = delta.get("reasoning_content")
        if c is not None and rc is not None and c != "":
            assert c != rc, f"content == reasoning_content in case: {label} (delta={delta})"
        # RC-2: field must always be reasoning_content, never raw 'reasoning'
        assert "reasoning" not in delta or delta.get("reasoning") is None, (
            f"raw 'reasoning' field leaked in case: {label} (delta={delta})"
        )


def test_rc1_streaming_strips_upstream_duplicate_content() -> None:
    """RC-1 (RCA 3.3 nemotron): when the upstream itself mirrors thinking into
    both content and reasoning_content, drop the content copy so the two fields
    are never equal."""
    raw = (
        b'data: {"choices":[{"delta":{"reasoning_content":"The","content":"The"}}]}\n'
    )
    out = transform_sse_bytes(raw, routed_model="nim/nvidia/nemotron")
    delta = _sse_delta_content(out)
    assert delta is not None
    assert delta["reasoning_content"] == "The"
    assert delta.get("content") in (None, ""), "duplicated content must be dropped"
    assert delta.get("content") != "The"


def test_rc1_nonstreaming_strips_upstream_duplicate_content() -> None:
    """RC-1 (RCA 3.4 nemotron): non-streaming must drop content when the
    upstream returned content == reasoning_content."""
    body = {
        "model": "x",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "The user wants me to say hello in exactly six words.",
                    "reasoning_content": "The user wants me to say hello in exactly six words.",
                }
            }
        ],
    }
    out = normalize_completion_json(body)
    msg = out["choices"][0]["message"]
    assert msg["reasoning_content"] == "The user wants me to say hello in exactly six words."
    assert msg["content"] in (None, ""), "duplicated content must be dropped"
    assert msg.get("content") != msg.get("reasoning_content")


def test_rc1_nonstreaming_preserves_distinct_content_and_reasoning() -> None:
    """RC-1: when content and reasoning are genuinely different, keep both.
    Regression guard against the duplicate-strip being over-eager."""
    body = {
        "model": "x",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello there, nice to meet you!",
                    "reasoning_content": "The user wants a six-word greeting.",
                }
            }
        ],
    }
    out = normalize_completion_json(body)
    msg = out["choices"][0]["message"]
    assert msg["content"] == "Hello there, nice to meet you!"
    assert msg["reasoning_content"] == "The user wants a six-word greeting."


def test_rc1_duplicate_strip_preserves_tool_calls() -> None:
    """RC-1: never strip content when tool_calls are present — agent clients
    need the tool delta intact even if it happens to equal reasoning text."""
    raw = (
        b'data: {"choices":[{"delta":{"content":"call","reasoning_content":"call",'
        b'"tool_calls":[{"id":"t1","type":"function","function":{"name":"f"}}]}}]}\n'
    )
    out = transform_sse_bytes(raw, routed_model="nim/m")
    delta = _sse_delta_content(out)
    assert delta is not None
    assert "tool_calls" in delta
    assert delta["content"] == "call"  # preserved because tools present


# ── Per-model reasoning_effort normalization ──────────────────────────


def test_reasoning_effort_injected_for_reasoning_model() -> None:
    """Reasoning-capable model gets default_effort when unset."""
    reg = _FakeRegistry({"qwen/qwen3.5-397b-a17b": {"supports_reasoning": True}})
    body = {"model": "potato/coding", "messages": []}
    out = normalize_reasoning_effort(
        body, routed_model="qwen/qwen3.5-397b-a17b", registry=reg, default_effort="medium"
    )
    assert out["reasoning_effort"] == "medium"


def test_reasoning_effort_not_injected_when_default_empty() -> None:
    """Empty default_effort → no injection (config-driven off switch)."""
    reg = _FakeRegistry({"qwen/qwen3.5-397b-a17b": {"supports_reasoning": True}})
    body = {"model": "potato/coding", "messages": []}
    out = normalize_reasoning_effort(
        body, routed_model="qwen/qwen3.5-397b-a17b", registry=reg, default_effort=""
    )
    assert "reasoning_effort" not in out


def test_reasoning_effort_not_injected_for_non_reasoning_model() -> None:
    """Non-reasoning model: no default injected."""
    reg = _FakeRegistry({"nvidia/nemotron-3-super": {"supports_reasoning": False}})
    body = {"model": "potato/best", "messages": []}
    out = normalize_reasoning_effort(
        body, routed_model="nvidia/nemotron-3-super", registry=reg, default_effort="medium"
    )
    assert "reasoning_effort" not in out


def test_reasoning_effort_strips_explicit_for_non_reasoning_model() -> None:
    """Non-reasoning model: client-sent reasoning_effort is stripped to avoid upstream 400.

    This is the resilience fix: a reasoning head failing over to a non-reasoning
    model must not forward reasoning_effort and 400 the fallback upstream.
    """
    reg = _FakeRegistry({"nvidia/nemotron-3-super": {"supports_reasoning": False}})
    body = {"model": "potato/best", "messages": [], "reasoning_effort": "high"}
    out = normalize_reasoning_effort(
        body, routed_model="nvidia/nemotron-3-super", registry=reg, default_effort="medium"
    )
    assert "reasoning_effort" not in out, "must strip reasoning_effort for non-reasoning model"


def test_reasoning_effort_respects_explicit_client_value() -> None:
    """Client-set reasoning_effort is never overridden, including 'high'/'max'/'none'."""
    reg = _FakeRegistry({"qwen/qwen3.5-397b-a17b": {"supports_reasoning": True}})
    for explicit in ("low", "medium", "high", "max", "none", None):
        body = {"model": "potato/coding", "messages": [], "reasoning_effort": explicit}
        out = normalize_reasoning_effort(
            body, routed_model="qwen/qwen3.5-397b-a17b", registry=reg, default_effort="medium"
        )
        assert out["reasoning_effort"] == explicit, f"overrode explicit {explicit!r}"


def test_reasoning_effort_no_registry_no_change() -> None:
    """Missing registry or routed_model → passthrough (safe no-op)."""
    body = {"model": "potato/coding", "messages": []}
    assert normalize_reasoning_effort(
        body, routed_model=None, registry=None, default_effort="medium"
    ) == body
    reg = _FakeRegistry({"m": {"supports_reasoning": True}})
    assert normalize_reasoning_effort(
        body, routed_model=None, registry=reg, default_effort="medium"
    ) == body


def test_reasoning_effort_unknown_model_passthrough() -> None:
    """Model not in capabilities dict → passthrough (forward-compatible)."""
    reg = _FakeRegistry({"qwen/qwen3.5-397b-a17b": {"supports_reasoning": True}})
    body = {"model": "potato/coding", "messages": [], "reasoning_effort": "high"}
    out = normalize_reasoning_effort(
        body, routed_model="unknown/model", registry=reg, default_effort="medium"
    )
    # Unknown model: explicit client value passes through (we don't guess)
    assert out.get("reasoning_effort") == "high"


def test_reasoning_effort_fallback_chain_reasoning_to_non_reasoning() -> None:
    """Resilience: reasoning head fails → non-reasoning fallback strips reasoning_effort.

    Simulates the fallback chain: the same body is normalized for two models.
    The reasoning head keeps reasoning_effort=high; the non-reasoning fallback
    gets it stripped so it doesn't 400.
    """
    reg = _FakeRegistry({
        "qwen/qwen3.5-397b-a17b": {"supports_reasoning": True},
        "nvidia/nemotron-3-super": {"supports_reasoning": False},
    })
    body = {"model": "potato/coding", "messages": [], "reasoning_effort": "high"}
    # Chain head (reasoning) — keeps high
    head_body = normalize_reasoning_effort(
        body, routed_model="qwen/qwen3.5-397b-a17b", registry=reg, default_effort="medium"
    )
    assert head_body["reasoning_effort"] == "high"
    # Fallback (non-reasoning) — strips it
    fb_body = normalize_reasoning_effort(
        body, routed_model="nvidia/nemotron-3-super", registry=reg, default_effort="medium"
    )
    assert "reasoning_effort" not in fb_body


def test_reasoning_effort_fallback_chain_default_injected_on_reasoning_only() -> None:
    """Resilience: no client value → default injected on reasoning head, not on fallback."""
    reg = _FakeRegistry({
        "qwen/qwen3.5-397b-a17b": {"supports_reasoning": True},
        "nvidia/nemotron-3-super": {"supports_reasoning": False},
    })
    body = {"model": "potato/coding", "messages": []}
    head_body = normalize_reasoning_effort(
        body, routed_model="qwen/qwen3.5-397b-a17b", registry=reg, default_effort="medium"
    )
    assert head_body["reasoning_effort"] == "medium"
    fb_body = normalize_reasoning_effort(
        body, routed_model="nvidia/nemotron-3-super", registry=reg, default_effort="medium"
    )
    assert "reasoning_effort" not in fb_body

