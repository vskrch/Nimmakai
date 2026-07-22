"""Regression tests for fable_audit Critical/High tickets."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nimmakai.balancer import KeyStats
from nimmakai.catalog import ModelRegistry
from nimmakai.compat import openai_error, sanitize_chat_body, wrap_upstream_error
from nimmakai.config import Settings
from nimmakai.routing import (
    FallbackExecutor,
    Intent,
    RouteDecision,
    parse_auto_router_options,
)
from nimmakai.routing.fallback import _analyze_success_body

YAML = Path(__file__).resolve().parents[1] / "config" / "models.yaml"


def _key(i: int = 0) -> KeyStats:
    return KeyStats(key_id=f"key-{i}", api_key=f"k{i}")


def test_missing_vite_assets_does_not_crash_app_startup(tmp_path: Path) -> None:
    from nimmakai.main import _mount_vite_assets

    dist = tmp_path / "dist"
    dist.mkdir()
    app = FastAPI()

    assert _mount_vite_assets(app, dist) is False
    assert not any(getattr(route, "path", None) == "/assets" for route in app.routes)


def test_fallback_chain_never_readds_unavailable_provider_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(nim_api_keys=["k"])
    registry = ModelRegistry.from_yaml(YAML)
    registry.live_ids = set()
    executor = FallbackExecutor(MagicMock(), registry, settings)
    monkeypatch.setattr(executor, "_provider_available", lambda _model: False)
    decision = RouteDecision(
        chain=["dead-provider/model"],
        mode="auto",
        intent=Intent.CHAT_FAST,
        rule_id="test",
        requested_model="auto",
    )

    assert executor._chain(decision) == []


# ── TICKET-1: schema-aware success analysis ─────────────────────────


def test_analyze_text_completion_not_empty() -> None:
    empty, tool_ok = _analyze_success_body(
        {"choices": [{"text": "hello", "index": 0}]},
        had_tools=False,
        path="/completions",
    )
    assert empty is False
    assert tool_ok is None


def test_analyze_responses_output_not_empty() -> None:
    empty, tool_ok = _analyze_success_body(
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}]},
        had_tools=False,
        path="/responses",
    )
    assert empty is False


def test_analyze_chat_empty_still_empty() -> None:
    empty, tool_ok = _analyze_success_body(
        {"choices": [{"message": {"content": ""}}]},
        had_tools=False,
        path="/chat/completions",
    )
    assert empty is True


@pytest.mark.asyncio
async def test_completions_path_does_not_fanout_on_text_success() -> None:
    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=3)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a", "model-b"}
    calls: list[str] = []

    async def fake_json(method, path, **kwargs):
        body = kwargs.get("json_body") or {}
        model = body.get("model")
        calls.append(model)
        return (
            200,
            {"id": "ok", "model": model, "choices": [{"text": "hi", "index": 0}]},
            {},
            _key(),
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
        "/completions", {"prompt": "hi"}, decision
    )
    assert result.status_code == 200
    assert result.model == "model-a"
    assert result.fallback_index == 0
    assert calls == ["model-a"]


# ── TICKET-3: transport errors advance chain ────────────────────────


@pytest.mark.asyncio
async def test_transport_error_advances_to_next_model() -> None:
    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=3)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a", "model-b"}

    async def fake_json(method, path, **kwargs):
        body = kwargs.get("json_body") or {}
        model = body.get("model")
        if model == "model-a":
            raise httpx.ConnectError("connection refused")
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
        intent=Intent.CHAT_FAST,
        rule_id="test",
        requested_model="auto",
    )
    ex = FallbackExecutor(upstream, reg, settings)
    result = await ex.execute_json("/chat/completions", {"messages": []}, decision)
    assert result.status_code == 200
    assert result.model == "model-b"
    assert result.fallback_index == 1


# ── TICKET-4: OpenAI error envelope ─────────────────────────────────


def test_openai_error_shape() -> None:
    body = openai_error("Missing API key", code="missing_api_key", type_="invalid_request_error")
    assert "error" in body
    assert "detail" not in body
    assert body["error"]["code"] == "missing_api_key"


def test_wrap_upstream_error_string() -> None:
    wrapped = wrap_upstream_error("<html>bad</html>", status=502)
    assert isinstance(wrapped, dict)
    assert wrapped["error"]["code"] == "upstream_error"


def test_http_exception_handler_unwraps_detail() -> None:
    from nimmakai.main import create_app

    app = create_app(
        Settings(
            allow_insecure_auth=True,
            routing_enabled=False,
            nim_api_keys=["k"],
            proxy_api_keys=["pk"],
        )
    )
    client = TestClient(app, raise_server_exceptions=False)
    # Wrong key → 401 with top-level error (not nested under detail)
    r = client.get("/v1/models", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    data = r.json()
    assert "error" in data
    assert "detail" not in data


# ── TICKET-5: all-models TTFT stall → 504 ───────────────────────────


@pytest.mark.asyncio
async def test_all_models_ttft_stall_returns_504(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=2)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a", "model-b"}

    async def fake_stream(method, path, **kwargs):
        async def stalled():
            import asyncio

            await asyncio.sleep(10)
            yield b"x"

        return 200, stalled(), {}, _key()

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

    async def mock_wait_for(fut, timeout):
        return await original_wait_for(fut, 0.0)

    monkeypatch.setattr(asyncio, "wait_for", mock_wait_for)
    result = await ex.execute_stream("/chat/completions", {"messages": []}, decision)
    assert result.status_code == 504
    chunks = [c async for c in result.byte_iter]
    joined = b"".join(chunks)
    assert b"error" in joined
    assert b"upstream_timeout" in joined or b"timeout" in joined.lower()


# ── TICKET-7: routing_headers never raises ──────────────────────────


def test_routing_headers_non_raising_with_broken_hub() -> None:
    settings = Settings(nim_api_keys=["k"])
    reg = ModelRegistry.from_yaml(YAML)
    hub = MagicMock()
    hub.client_for_model.side_effect = RuntimeError("circuit open")
    upstream = AsyncMock()
    ex = FallbackExecutor(upstream, reg, settings, hub=hub)
    decision = RouteDecision(
        chain=["model-a"],
        mode="auto",
        intent=Intent.CHAT_FAST,
        rule_id="test",
        requested_model="auto",
    )
    h = ex.routing_headers(
        decision,
        model="model-a",
        key_id="key-0",
        fallback_index=0,
        provider_id="nim",
    )
    assert h["X-Nimmakai-Provider"] == "nim"
    assert h["X-Nimmakai-Model"] == "model-a"


# ── TICKET-2: sanitize does not strip before parse ──────────────────


def test_parse_auto_router_before_sanitize() -> None:
    raw = {
        "model": "nimmakai/auto",
        "session_id": "sess-1",
        "plugins": [
            {
                "id": "auto-router",
                "allowed_models": ["deepseek/*"],
                "cost_quality_tradeoff": 8,
            }
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }
    opts = parse_auto_router_options(raw)
    assert opts.session_id == "sess-1"
    assert opts.allowed_models == ["deepseek/*"]
    # sanitize must NOT strip session_id/plugins — strip_router is the stripper
    cleaned = sanitize_chat_body(raw)
    assert cleaned.get("session_id") == "sess-1"
    assert cleaned.get("plugins") is not None
    from nimmakai.routing.auto_router import strip_router_client_fields

    stripped = strip_router_client_fields(cleaned)
    assert "session_id" not in stripped
    assert "plugins" not in stripped


# ── TICKET-8: pinned_head survives _chain ───────────────────────────


def test_pinned_head_stays_first_when_healthy() -> None:
    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=5)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a", "model-b", "model-c"}
    # Make model-b look hotter so optimize_chain would prefer it without pin
    reg.health.record_outcome("model-b", success=True, latency=0.1, tokens=100)
    reg.health.record_outcome("model-b", success=True, latency=0.1, tokens=100)
    reg.health.record_outcome("model-b", success=True, latency=0.1, tokens=100)
    upstream = AsyncMock()
    ex = FallbackExecutor(upstream, reg, settings)
    decision = RouteDecision(
        chain=["model-a", "model-b", "model-c"],
        mode="auto",
        intent=Intent.CODING_AGENTIC,
        rule_id="test",
        requested_model="auto",
        pinned_head="model-a",
        sticky_model="model-a",
    )
    chain = ex._chain(decision)
    assert chain[0] == "model-a"


# ── TICKET-20: n>1 rejected; prompt_cache_key kept ──────────────────


def test_sanitize_rejects_n_gt_1() -> None:
    with pytest.raises(ValueError, match="n_not_supported"):
        sanitize_chat_body({"model": "auto", "n": 2, "messages": []})


def test_sanitize_keeps_prompt_cache_key() -> None:
    body = sanitize_chat_body(
        {
            "model": "auto",
            "prompt_cache_key": "abc",
            "user": "u1",
            "messages": [],
        }
    )
    assert body.get("prompt_cache_key") == "abc"
    assert body.get("user") == "u1"
