"""Fallback executor with mocked upstream."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nimmakai.balancer import KeyStats
from nimmakai.catalog import ModelRegistry
from nimmakai.config import Settings
from nimmakai.routing import (
    FallbackExecutor,
    Intent,
    IntentResult,
    ModelSelector,
    RouteDecision,
)

YAML = Path(__file__).resolve().parents[1] / "config" / "models.yaml"


def _key(i: int = 0) -> KeyStats:
    return KeyStats(key_id=f"key-{i}", api_key=f"k{i}")


@pytest.mark.asyncio
async def test_fallback_advances_on_404() -> None:
    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=3)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a", "model-b"}

    calls: list[str] = []

    async def fake_json(method, path, **kwargs):
        body = kwargs.get("json_body") or {}
        model = body.get("model")
        calls.append(model)
        if model == "model-a":
            return 404, {"error": {"message": "model not found"}}, {}, _key()
        return 200, {"id": "ok", "model": model, "choices": []}, {}, _key(1)

    upstream = AsyncMock()
    upstream.request_json = fake_json

    decision = RouteDecision(
        chain=["model-a", "model-b"],
        mode="auto",
        intent=Intent.CODING_AGENTIC,
        rule_id="test",
        requested_model="auto",
    )
    ex = FallbackExecutor(upstream, reg, settings)
    result = await ex.execute_json("/chat/completions", {"messages": []}, decision)
    assert result.status_code == 200
    assert result.model == "model-b"
    assert result.fallback_index == 1
    assert calls == ["model-a", "model-b"]
    assert result.body["model"] == "model-b"


@pytest.mark.asyncio
async def test_soft_fail_empty_reply_advances() -> None:
    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=3)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a", "model-b"}

    async def fake_json(method, path, **kwargs):
        body = kwargs.get("json_body") or {}
        model = body.get("model")
        if model == "model-a":
            return (
                200,
                {"id": "empty", "model": model, "choices": [{"message": {"content": ""}}]},
                {},
                _key(),
            )
        return (
            200,
            {
                "id": "ok",
                "model": model,
                "choices": [{"message": {"content": "hello"}}],
            },
            {},
            _key(1),
        )

    upstream = AsyncMock()
    upstream.request_json = fake_json
    decision = RouteDecision(
        chain=["model-a", "model-b"],
        mode="auto",
        intent=Intent.CHAT_FAST,
        rule_id="test",
        requested_model="auto",
    )
    ex = FallbackExecutor(upstream, reg, settings)
    result = await ex.execute_json(
        "/chat/completions",
        {"messages": [{"role": "user", "content": "hi"}]},
        decision,
    )
    assert result.status_code == 200
    assert result.model == "model-b"
    assert result.fallback_index == 1


@pytest.mark.asyncio
async def test_non_retryable_400_stops() -> None:
    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=3)
    reg = ModelRegistry.from_yaml(YAML)

    async def fake_json(method, path, **kwargs):
        return 400, {"error": {"message": "invalid json schema"}}, {}, _key()

    upstream = AsyncMock()
    upstream.request_json = fake_json
    decision = RouteDecision(
        chain=["a", "b"],
        mode="auto",
        intent=Intent.CHAT_FAST,
        rule_id="test",
        requested_model="auto",
    )
    ex = FallbackExecutor(upstream, reg, settings)
    result = await ex.execute_json("/chat/completions", {}, decision)
    assert result.status_code == 400
    assert result.model == "a"


@pytest.mark.asyncio
async def test_context_overflow_advances() -> None:
    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=3)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a", "model-b"}
    reg.context_by_model = {"model-a": 8192, "model-b": 131072}

    async def fake_json(method, path, **kwargs):
        body = kwargs.get("json_body") or {}
        model = body.get("model")
        if model == "model-a":
            return (
                400,
                {"error": {"message": "This model's maximum context length is 8192 tokens"}},
                {},
                _key(),
            )
        return (
            200,
            {
                "id": "ok",
                "model": model,
                "choices": [{"message": {"content": "ok"}}],
            },
            {},
            _key(1),
        )

    upstream = AsyncMock()
    upstream.request_json = fake_json
    decision = RouteDecision(
        chain=["model-a", "model-b"],
        mode="auto",
        intent=Intent.CODING_AGENTIC,
        rule_id="test",
        requested_model="auto",
    )
    ex = FallbackExecutor(upstream, reg, settings)
    result = await ex.execute_json("/chat/completions", {"messages": []}, decision)
    assert result.status_code == 200
    assert result.model == "model-b"
    headers = ex.routing_headers(
        decision, model=result.model, key_id="key-1", fallback_index=1
    )
    assert headers.get("X-Nimmakai-Context-Length") == "131072"

    settings = Settings(nim_api_keys=["k"])
    reg = ModelRegistry.from_yaml(YAML)
    all_ids: set[str] = set()
    for ic in reg.catalog.intents.values():
        all_ids.update(ic.chain)
    reg.live_ids = all_ids
    sel = ModelSelector(reg, settings)
    intent = IntentResult(intent=Intent.CHAT_FAST, confidence=0.7, rule_id="short_chat")
    d = sel.resolve("nimmakai/auto", intent)
    assert d.mode == "auto"
    assert d.chain

@pytest.mark.asyncio
async def test_streaming_watchdog_ttft_stall(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=3)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a", "model-b"}

    async def fake_stream(method, path, **kwargs):
        model = kwargs["json_body"]["model"]
        if model == "model-a":
            # Simulate a stream that connects (returns 200) but never yields chunks
            import asyncio
            async def stalled_iter():
                await asyncio.sleep(2.0)
                yield b"never reached"
            return 200, stalled_iter(), {}, _key()
        else:
            # Model B succeeds immediately
            async def ok_iter():
                yield b"ok"
            return 200, ok_iter(), {}, _key(1)

    upstream = AsyncMock()
    upstream.stream = fake_stream

    decision = RouteDecision(
        chain=["model-a", "model-b"],
        mode="auto",
        intent=Intent.CHAT_FAST,
        rule_id="test",
        requested_model="auto",
    )
    ex = FallbackExecutor(upstream, reg, settings)
    
    import asyncio
    original_wait_for = asyncio.wait_for
    
    call_count = 0
    async def mock_wait_for(fut, timeout):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise asyncio.TimeoutError()
        return await original_wait_for(fut, timeout)
        
    monkeypatch.setattr(asyncio, "wait_for", mock_wait_for)
    
    result = await original_wait_for(
        ex.execute_stream("/chat/completions", {"messages": []}, decision),
        timeout=2.0
    )
    
    assert result.status_code == 200
    assert result.model == "model-b"
    assert result.fallback_index == 1
    
    # ensure we can consume it, restoring wait_for so the inner logic works
    monkeypatch.setattr(asyncio, "wait_for", original_wait_for)
    chunks = [c async for c in result.byte_iter]
    assert chunks == [b"ok"]

@pytest.mark.asyncio
async def test_token_accounting_json() -> None:
    settings = Settings(nim_api_keys=["k"])
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a"}

    async def fake_json(method, path, **kwargs):
        return 200, {"id": "ok", "model": "model-a", "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, {}, _key()

    upstream = AsyncMock()
    upstream.request_json = fake_json

    decision = RouteDecision(
        chain=["model-a"],
        mode="auto",
        intent=Intent.CHAT_FAST,
        rule_id="test",
        requested_model="auto",
    )
    ex = FallbackExecutor(upstream, reg, settings)
    await ex.execute_json("/chat/completions", {"messages": []}, decision)
    
    assert ex.stats.model_tokens["model-a"].prompt_tokens == 10
    assert ex.stats.model_tokens["model-a"].completion_tokens == 5

@pytest.mark.asyncio
async def test_token_accounting_stream() -> None:
    settings = Settings(nim_api_keys=["k"])
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a"}

    async def fake_stream(method, path, **kwargs):
        async def ok_iter():
            yield b'data: {"choices": [{"delta": {"content": "hello"}}]}\n\n'
            yield b'data: {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 10}}\n\n'
        return 200, ok_iter(), {}, _key()

    upstream = AsyncMock()
    upstream.stream = fake_stream

    decision = RouteDecision(
        chain=["model-a"],
        mode="auto",
        intent=Intent.CHAT_FAST,
        rule_id="test",
        requested_model="auto",
    )
    ex = FallbackExecutor(upstream, reg, settings)
    result = await ex.execute_stream("/chat/completions", {"messages": []}, decision)
    
    # consume
    chunks = [c async for c in result.byte_iter]
    
    assert ex.stats.model_tokens["model-a"].prompt_tokens == 20
    assert ex.stats.model_tokens["model-a"].completion_tokens == 10
