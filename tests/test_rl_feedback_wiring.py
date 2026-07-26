"""End-to-end RL feedback loop test: FallbackExecutor → record_feedback → bandit update.

Verifies the wiring added in NMK-RL-101: every execution outcome feeds the
LinUCB engine a (model, x, reward) sample, so the dashboard UI shows data and
the bandit actually learns from real traffic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from potato.balancer import KeyStats
from potato.catalog import ModelRegistry
from potato.config import Settings
from potato.routing import (
    FallbackExecutor,
    Intent,
    RouteDecision,
)
from potato.routing.rl_engine import LinUCBPolicyEngine
from potato.routing.rl_features import FEATURE_DIM, extract_feature_vector

YAML = Path(__file__).resolve().parents[1] / "config" / "models.yaml"


def _key(i: int = 0) -> KeyStats:
    return KeyStats(key_id=f"key-{i}", api_key=f"k{i}")


@pytest.mark.asyncio
async def test_rl_feedback_recorded_on_success() -> None:
    """A successful execution must increment the bandit's request_count."""
    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=3)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a", "model-b"}
    rl = LinUCBPolicyEngine()

    async def fake_json(method, path, **kwargs):
        body = kwargs.get("json_body") or {}
        model = body.get("model", "model-a")
        return (
            200,
            {"id": "ok", "model": model, "choices": [{"message": {"content": "hi"}}]},
            {},
            _key(),
        )

    upstream = AsyncMock()
    upstream.request_json = fake_json

    x = extract_feature_vector({"messages": [{"role": "user", "content": "hi"}]})
    assert len(x) == FEATURE_DIM
    decision = RouteDecision(
        chain=["model-a"],
        mode="auto",
        intent=Intent.CHAT_FAST,
        rule_id="test",
        requested_model="auto",
        feature_vector=x,
    )
    ex = FallbackExecutor(upstream, reg, settings, rl_engine=rl)
    await ex.execute_json("/chat/completions", {"messages": []}, decision)

    stats = rl.get_all_stats()
    assert any(s["model_id"] == "model-a" for s in stats), "RL bandit was not fed"
    model_a = next(s for s in stats if s["model_id"] == "model-a")
    assert model_a["request_count"] == 1
    assert model_a["avg_reward"] > 0.0  # success → positive reward


@pytest.mark.asyncio
async def test_rl_feedback_recorded_on_503_failure() -> None:
    """A 5xx failure must record a negative reward."""
    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=3)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a", "model-b"}
    rl = LinUCBPolicyEngine()

    async def fake_json(method, path, **kwargs):
        body = kwargs.get("json_body") or {}
        model = body.get("model")
        if model == "model-a":
            return 503, {"error": "boom"}, {}, _key()
        return (
            200,
            {"id": "ok", "model": model, "choices": [{"message": {"content": "ok"}}]},
            {},
            _key(1),
        )

    upstream = AsyncMock()
    upstream.request_json = fake_json

    x = extract_feature_vector({"messages": [{"role": "user", "content": "hi"}]})
    decision = RouteDecision(
        chain=["model-a", "model-b"],
        mode="auto",
        intent=Intent.CHAT_FAST,
        rule_id="test",
        requested_model="auto",
        feature_vector=x,
    )
    ex = FallbackExecutor(upstream, reg, settings, rl_engine=rl)
    await ex.execute_json("/chat/completions", {"messages": []}, decision)

    stats = rl.get_all_stats()
    model_a = next(s for s in stats if s["model_id"] == "model-a")
    assert model_a["request_count"] == 1
    assert model_a["avg_reward"] < 0.0  # 503 → negative reward


@pytest.mark.asyncio
async def test_rl_noop_without_engine() -> None:
    """When rl_engine is None the executor must still work (no crash, no learning)."""
    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=3)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a"}

    async def fake_json(method, path, **kwargs):
        body = kwargs.get("json_body") or {}
        model = body.get("model", "model-a")
        return (
            200,
            {"id": "ok", "model": model, "choices": [{"message": {"content": "ok"}}]},
            {},
            _key(),
        )

    upstream = AsyncMock()
    upstream.request_json = fake_json

    decision = RouteDecision(
        chain=["model-a"],
        mode="auto",
        intent=Intent.CHAT_FAST,
        rule_id="test",
        requested_model="auto",
        feature_vector=None,  # no feature vector
    )
    ex = FallbackExecutor(upstream, reg, settings)  # no rl_engine
    result = await ex.execute_json("/chat/completions", {"messages": []}, decision)
    assert result.status_code == 200


def test_rl_chain_re_rank_uses_feature_vector() -> None:
    """rank_chain_with_rl must re-order the chain using the bandit's learned scores."""
    rl = LinUCBPolicyEngine(default_alpha=0.5)
    # Teach the bandit that model-b is great for python tasks, model-a is bad.
    x_py = [0.0] * FEATURE_DIM
    x_py[2] = 1.0  # code syntax
    x_py[3] = 1.0  # python
    for _ in range(20):
        rl.record_feedback("model-a", x_py, reward=-1.0)
        rl.record_feedback("model-b", x_py, reward=1.0)

    settings = Settings(nim_api_keys=["k"], max_model_fallbacks=3)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {"model-a", "model-b"}
    ex = FallbackExecutor(AsyncMock(), reg, settings, rl_engine=rl)

    # The _chain method should re-rank model-b above model-a given x_py.
    decision = RouteDecision(
        chain=["model-a", "model-b"],
        mode="auto",
        intent=Intent.CODING_AGENTIC,
        rule_id="test",
        requested_model="auto",
        feature_vector=x_py,
    )
    chain = ex._chain(decision)
    assert chain[0] == "model-b", "RL bandit should promote model-b over model-a"