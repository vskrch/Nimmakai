"""NMK-602: Scoring algorithm property tests — invariants that must always hold."""

import pytest
from nimmakai.catalog.health import ModelHealthStore, ModelHealth
from nimmakai.routing.optimizer import (
    score_model_live,
    _quality_prior,
    _speed_factor,
    _availability_factor,
    optimize_chain,
)


def test_quality_95_above_quality_65():
    """A 95-quality model must always score above a 65-quality model when both are healthy."""
    scores = {"m_high": 95.0, "m_low": 65.0}
    q_high = _quality_prior("m_high", ladder_scores=scores)
    q_low = _quality_prior("m_low", ladder_scores=scores)
    assert q_high > q_low, f"high quality {q_high} should exceed low quality {q_low}"


def test_dead_model_never_leads():
    """A model in cooldown (dead) must never lead the chain."""
    health = ModelHealthStore(model_cooldown_seconds=30.0)
    health.record_outcome("dead_model", success=False, status_code=404, unavailable=True)
    scores = {"dead_model": 90.0, "live_model": 80.0}
    s_dead = score_model_live(
        "dead_model", ladder_scores=scores, health=health, provider_ids={"nim"}
    )
    s_live = score_model_live(
        "live_model", ladder_scores=scores, health=health, provider_ids={"nim"}
    )
    assert s_dead < s_live, f"dead model {s_dead} should score below live {s_live}"


def test_cooldown_model_availability_near_zero():
    """In-cooldown model availability factor should be near zero."""
    health = ModelHealthStore()
    health.record_outcome("m1", success=False, status_code=404, unavailable=True)
    avail = _availability_factor(health, "m1")
    assert avail < 0.05, f"cooldown availability {avail} should be near zero"


def test_healthy_model_availability_high():
    """Healthy model availability should be near 1.0."""
    health = ModelHealthStore()
    health.record_outcome("m1", success=True, latency=0.5)
    health.record_outcome("m1", success=True, latency=0.3)
    avail = _availability_factor(health, "m1")
    assert avail > 0.8, f"healthy availability {avail} should be high"


def test_custom_override_precedence():
    """Quality override should take precedence over regex tier matching."""
    from nimmakai.catalog.ladder import LadderService
    from nimmakai.catalog.health import ModelHealthStore
    ls = LadderService(health=ModelHealthStore())
    ls.quality_overrides["nim/custom-model"] = 99.0
    score = ls._base_quality("custom-model", "nim/custom-model")
    assert score == 99.0, f"override {score} should be 99.0"


def test_frozen_ladder_deterministic():
    """Two rebuilds with same data should produce identical frozen ladders."""
    from nimmakai.catalog.ladder import LadderService
    from nimmakai.catalog.health import ModelHealthStore
    ls1 = LadderService(health=ModelHealthStore())
    ls2 = LadderService(health=ModelHealthStore())
    ids = {"nim/deepseek-v4-pro", "nim/gpt-4o", "nim/claude-sonnet-4"}
    ls1.rebuild(ids, freeze=True)
    ls2.rebuild(ids, freeze=True)
    chain1 = ls1.ladder_for("coding_agentic")
    chain2 = ls2.ladder_for("coding_agentic")
    assert chain1 == chain2, "frozen ladders should be deterministic"


def test_optimize_chain_preserves_order_with_one_model():
    """Single model chain should be unchanged."""
    from nimmakai.catalog.schema import catalog_from_dict
    from nimmakai.catalog.registry import ModelRegistry
    cat = catalog_from_dict({
        "version": "1", "updated": "2026-01-01",
        "defaults": {"dynamic_families": True, "auto_mode_model_tokens": []},
        "families": {"chat_primary": "nim", "coding_primary": "nim", "fallbacks": []},
        "intents": {"coding_agentic": {"chain": []}},
        "aliases": {}, "models": {},
    })
    reg = ModelRegistry(cat)
    out = optimize_chain(["m1"], reg, intent="coding_agentic")
    assert out == ["m1"]
