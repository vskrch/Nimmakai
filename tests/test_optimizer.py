"""Continuous intelligence × speed optimizer."""

from __future__ import annotations

from nimmakai.catalog.health import ModelHealthStore
from nimmakai.catalog.ladder import LadderService
from nimmakai.routing.optimizer import optimize_chain, score_model_live


class _FakeReg:
    def __init__(self) -> None:
        self.health = ModelHealthStore()
        self.ladder = LadderService(health=self.health)
        self.ladder.provider_ids = {"nim", "zen", "groq"}
        live = {
            "nim/deepseek-ai/deepseek-v4-pro",
            "nim/deepseek-ai/deepseek-v4-flash",
            "nim/google/gemma-2-2b-it",
            "groq/llama-3.3-70b-versatile",
        }
        self.ladder.rebuild(live, freeze=True)
        self.live_ids = live


def test_optimizer_prefers_intelligent_over_tiny() -> None:
    reg = _FakeReg()
    chain = [
        "nim/google/gemma-2-2b-it",
        "nim/deepseek-ai/deepseek-v4-pro",
        "nim/deepseek-ai/deepseek-v4-flash",
    ]
    out = optimize_chain(chain, reg, intent="coding_agentic")  # type: ignore[arg-type]
    assert "deepseek" in out[0]


def test_optimizer_promotes_fast_responder_among_peers() -> None:
    reg = _FakeReg()
    # Both deepseek models: make flash much faster + reliable
    for _ in range(4):
        reg.health.record_outcome(
            "nim/deepseek-ai/deepseek-v4-flash",
            success=True,
            latency=0.15,
            tokens=80,
        )
    for _ in range(2):
        reg.health.record_outcome(
            "nim/deepseek-ai/deepseek-v4-pro",
            success=True,
            latency=2.5,
            tokens=40,
        )
    chain = [
        "nim/deepseek-ai/deepseek-v4-pro",
        "nim/deepseek-ai/deepseek-v4-flash",
    ]
    out = optimize_chain(chain, reg, intent="coding_agentic")  # type: ignore[arg-type]
    # Flash should win or be very close; with big speed gap it leads
    assert out[0] == "nim/deepseek-ai/deepseek-v4-flash"


def test_optimizer_demotes_dead_model() -> None:
    reg = _FakeReg()
    for _ in range(3):
        reg.health.record_outcome(
            "nim/deepseek-ai/deepseek-v4-pro",
            success=False,
            status_code=503,
        )
    chain = [
        "nim/deepseek-ai/deepseek-v4-pro",
        "nim/deepseek-ai/deepseek-v4-flash",
    ]
    out = optimize_chain(chain, reg, intent="coding_agentic")  # type: ignore[arg-type]
    assert out[0] == "nim/deepseek-ai/deepseek-v4-flash"
    assert "deepseek-v4-pro" in out[-1]


def test_score_live_positive() -> None:
    reg = _FakeReg()
    s = score_model_live(
        "nim/deepseek-ai/deepseek-v4-pro",
        ladder_scores=None,
        health=reg.health,
        provider_ids=reg.ladder.provider_ids,
    )
    assert s > 0
