"""ModelScoreCache — atomic, versionable, internet-primary model scoring."""

from __future__ import annotations

from potato.catalog.health import ModelHealthStore
from potato.catalog.intel_fetcher import IntelBundle
from potato.catalog.learning import LearningStore
from potato.catalog.score_cache import ModelScore, ModelScoreCache, recompute


def test_recompute_empty_bundles():
    """Cold-start: param estimate gives sane quality for known param sizes."""
    cache = recompute(
        live_ids={"nim/llama-3.1-70b"},
        intel_bundles={},
        health=ModelHealthStore(),
        learning=LearningStore(),
        yaml_cfg={},
    )
    ms = cache.scores["nim/llama-3.1-70b"]
    assert 55.0 <= ms.quality <= 85.0


def test_recompute_aa_priority():
    """AA intelligence_index is the highest-priority quality signal (weight 0.40)."""
    bundle = IntelBundle(model_slug="llama-3.1-70b", aa_intelligence_idx=82.0)
    cache = recompute(
        live_ids={"nim/llama-3.1-70b"},
        intel_bundles={"llama-3.1-70b": bundle},
        health=ModelHealthStore(),
        learning=LearningStore(),
        yaml_cfg={},
    )
    ms = cache.scores["nim/llama-3.1-70b"]
    assert abs(ms.quality - 82.0) < 5.0


def test_tools_affinity_boost():
    """Tools-capable model must score higher for coding_agentic than tools-incapable."""
    b_tools = IntelBundle(model_slug="model-a", supports_tools=True)
    b_none = IntelBundle(model_slug="model-b", supports_tools=False)
    cache = recompute(
        live_ids={"p/model-a", "p/model-b"},
        intel_bundles={"model-a": b_tools, "model-b": b_none},
        health=ModelHealthStore(),
        learning=LearningStore(),
        yaml_cfg={},
    )
    a = cache.scores["p/model-a"].intent_affinity["coding_agentic"]
    b = cache.scores["p/model-b"].intent_affinity["coding_agentic"]
    assert a > b, "tools-capable model must score higher for coding_agentic"


def test_reasoning_affinity_boost():
    """Reasoning-capable model must score higher for reasoning intent."""
    b_reas = IntelBundle(model_slug="r1-model", supports_reasoning=True)
    cache = recompute(
        live_ids={"p/r1-model"},
        intel_bundles={"r1-model": b_reas},
        health=ModelHealthStore(),
        learning=LearningStore(),
        yaml_cfg={},
    )
    r_aff = cache.scores["p/r1-model"].intent_affinity["reasoning"]
    c_aff = cache.scores["p/r1-model"].intent_affinity["chat_fast"]
    assert r_aff > c_aff, "reasoning model should score higher on reasoning than chat"


def test_vision_exclusion():
    """Non-vision model should have near-zero vision affinity."""
    b_text = IntelBundle(model_slug="text-model", supports_vision=False)
    cache = recompute(
        live_ids={"p/text-model"},
        intel_bundles={"text-model": b_text},
        health=ModelHealthStore(),
        learning=LearningStore(),
        yaml_cfg={},
    )
    v_aff = cache.scores["p/text-model"].intent_affinity["vision"]
    assert v_aff <= 0.1, f"non-vision model should have ~0 vision affinity, got {v_aff}"


def test_atomic_install():
    """ModelScoreCache.install swaps atomically; version increments."""
    c1 = recompute(
        live_ids=set(), intel_bundles={},
        health=ModelHealthStore(), learning=LearningStore(), yaml_cfg={},
    )
    ModelScoreCache.install(c1)
    assert ModelScoreCache.current() is c1
    c2 = recompute(
        live_ids=set(), intel_bundles={},
        health=ModelHealthStore(), learning=LearningStore(), yaml_cfg={},
    )
    ModelScoreCache.install(c2)
    assert ModelScoreCache.current() is c2
    assert c2.version == c1.version + 1
    # Cleanup
    ModelScoreCache._current = None


def test_cache_resilience_no_bundles():
    """Recompute with no intel bundles at all still produces valid scores."""
    cache = recompute(
        live_ids={"nim/unknown-model", "nim/llama-70b"},
        intel_bundles={},
        health=ModelHealthStore(),
        learning=LearningStore(),
        yaml_cfg={},
    )
    assert len(cache.scores) == 2
    for ms in cache.scores.values():
        assert ms.quality > 0
        assert ms.quality <= 100.0


def test_model_score_dataclass():
    """ModelScore has all required fields."""
    ms = ModelScore(
        model_id="test/model",
        quality=85.0,
        intent_affinity={"coding_agentic": 1.2},
        modalities=frozenset({"text", "tools"}),
        context_k=32.0,
        measured_tps=40.0,
        provider_id="test",
        sources=["param_estimate"],
        computed_at=1234567890.0,
    )
    assert ms.model_id == "test/model"
    assert "tools" in ms.modalities
    assert ms.intent_affinity["coding_agentic"] == 1.2