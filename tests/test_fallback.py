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
async def test_selector_integration_auto() -> None:
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
