"""LadderService — quality-first scoring with UCB1 + Thompson Sampling."""

from __future__ import annotations

from nimmakai.catalog.docs_fetcher import DocModel
from nimmakai.catalog.health import ModelHealthStore
from nimmakai.catalog.intel_fetcher import IntelBundle
from nimmakai.catalog.ladder import LadderService
from nimmakai.catalog.learning import LearningStore
from nimmakai.catalog.score_cache import ModelScoreCache, recompute


def _install_score_cache(live: set[str], bundles: dict | None = None) -> None:
    """Helper: install a ModelScoreCache so ladder reads internet-primary quality."""
    bundles = bundles or {}
    cache = recompute(
        live_ids=live,
        intel_bundles=bundles,
        health=ModelHealthStore(),
        learning=LearningStore(),
        yaml_cfg={},
    )
    ModelScoreCache.install(cache)


def _clear_score_cache() -> None:
    ModelScoreCache._current = None


def test_coding_ladder_quality_first() -> None:
    """Highest benchmark-quality model with best coding affinity leads."""
    svc = LadderService()
    live = {
        "qwen/qwen3.5-397b-a17b",
        "qwen/qwen3.5-122b-a10b",
        "qwen/qwen-image",
        "nvidia/nemotron-3-nano-30b-a3b",
        "zai/glm-5.2",
        "stepfun/step-3.7-flash",
        "minimaxai/minimax-m3",
        "google/gemma-4-31b-it",
    }
    svc.set_docs(
        [
            DocModel(
                slug="qwen3.5-397b-a17b",
                path="/x/qwen.md",
                description="Next-gen Qwen for coding, agentic, multimodal",
            ),
            DocModel(
                slug="glm-5.2",
                path="/x/glm.md",
                description="Flagship LLM for agentic workflows and coding",
            ),
        ]
    )
    svc.rebuild(live)
    ladder = svc.ladder_for("coding_agentic")
    # Top benchmark quality models (qwen3.5-397b, qwen3.5-122b, or glm-5.2) lead coding
    assert ladder[0] in ("qwen/qwen3.5-397b-a17b", "qwen/qwen3.5-122b-a10b", "zai/glm-5.2")
    assert "qwen-image" not in ladder
    # glm-5.2, nemotron, etc. all present
    assert any("glm" in m for m in ladder)
    assert any("step-3.7" in m for m in ladder)


def test_chat_ladder_quality_first() -> None:
    """Nemotron ultra (quality=93 × affinity=1.25 ≈ 116) leads chat."""
    svc = LadderService()
    live = {
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/llama-nemotron-embed-1b-v2",
        "qwen/qwen3.5-122b-a10b",
        "zai/glm-5.2",
    }
    svc.rebuild(live)
    ladder = svc.ladder_for("chat_fast")
    # Ultra nemotron should be in top 2 (Thompson noise may occasionally flip)
    top2 = ladder[:2]
    assert any("nemotron" in m for m in top2)
    assert all("embed" not in m for m in top2)


def test_ladder_skips_unhealthy_head() -> None:
    health = ModelHealthStore()
    svc = LadderService(health=health)
    live = {
        "qwen/qwen3.5-397b-a17b",
        "zai/glm-5.2",
        "stepfun/step-3.7-flash",
    }
    # Unfrozen rebuild so health is reflected when re-scoring (production freezes)
    svc.rebuild(live, freeze=False)
    health.record_outcome(
        "qwen/qwen3.5-397b-a17b",
        success=False,
        status_code=404,
        unavailable=True,
    )
    ladder = svc.ladder_for("coding_agentic")
    # qwen3.5-397b has health=0.01, so its score ≈ 123*0.01 ≈ 1.2
    # glm-5.2 (quality=87 × affinity=1.15 ≈ 100) should now lead
    assert ladder[0] != "qwen/qwen3.5-397b-a17b"
    assert "glm" in ladder[0] or "step" in ladder[0]


def test_score_excludes_embeddings_from_chat() -> None:
    svc = LadderService()
    s = svc.score_model("nvidia/llama-nemotron-embed-1b-v2", "chat_fast")
    assert s.score < 0


def test_quality_tiers_benchmark_based() -> None:
    """Verify quality from ModelScoreCache produces expected rankings."""
    live = {"qwen/qwen3.5-397b-a17b", "qwen/qwen3.5-122b-a10b"}
    # Install score cache with AA intelligence data (internet-primary)
    _install_score_cache(live, {
        "qwen3.5-397b-a17b": IntelBundle(model_slug="qwen3.5-397b-a17b", aa_intelligence_idx=95.0),
        "qwen3.5-122b-a10b": IntelBundle(model_slug="qwen3.5-122b-a10b", aa_intelligence_idx=88.0),
    })
    try:
        svc = LadderService()
        svc.rebuild(live)
        s397 = svc.score_model("qwen/qwen3.5-397b-a17b", "coding_agentic")
        s122 = svc.score_model("qwen/qwen3.5-122b-a10b", "coding_agentic")
        # Quality (from AA intel) must reflect benchmark rankings
        assert s397.quality > s122.quality  # 95 > 88
        assert s397.quality == 95.0
        assert s122.quality == 88.0
        # Deterministic composite (quality × affinity × capability × health) must be higher
        det_397 = s397.quality * s397.affinity * s397.capability * s397.health
        det_122 = s122.quality * s122.affinity * s122.capability * s122.health
        assert det_397 > det_122
    finally:
        _clear_score_cache()


def test_ucb_bonus_decreases_with_samples() -> None:
    """UCB1 exploration bonus should decrease as a model gets more requests."""
    learning = LearningStore()
    svc = LadderService(learning=learning)
    live = {"model-a", "model-b"}
    svc.rebuild(live)

    # Cold start: both get generous bonus
    ucb_a_cold = svc._ucb_bonus("model-a", "coding_agentic")
    assert ucb_a_cold > 5.0  # generous cold-start bonus

    # Record several requests for model-a
    for _ in range(10):
        learning.record(
            intent="coding_agentic", model_id="model-a", success=True
        )
    ucb_a_warm = svc._ucb_bonus("model-a", "coding_agentic")
    ucb_b_warm = svc._ucb_bonus("model-b", "coding_agentic")

    # model-a (sampled 10 times) should have lower UCB than model-b (0 times)
    assert ucb_a_warm < ucb_b_warm


def test_thompson_bonus_range() -> None:
    """Thompson bonus should be bounded in [-10, +10]."""
    svc = LadderService()
    bonuses = [svc._thompson_bonus("some-model", "chat_fast") for _ in range(100)]
    assert all(-10.1 <= b <= 10.1 for b in bonuses)


def test_capability_gate_blocks_no_tools() -> None:
    """Model confirmed to not support tools should score near-zero for coding."""
    svc = LadderService()
    svc.set_capability("model-x", supports_tools=False)
    s = svc.score_model("model-x", "coding_agentic")
    # capability=0.1 should heavily penalize but not fully exclude
    assert s.capability < 0.2


def test_coding_ladder_mimo_priority(monkeypatch) -> None:
    """MiMo and DeepSeek priority for coding intent (via score cache affinity)."""
    monkeypatch.setattr("random.betavariate", lambda a, b: 0.5)
    live = {
        "opencode/mimo-v2.5",
        "deepseek/deepseek-v4-pro",
        "qwen/qwen3.5-397b",
        "kimi/kimi-k2.6",
    }
    # Install score cache: mimo gets highest quality + tools confirmed (coding affinity boost)
    _install_score_cache(live, {
        "mimo-v2.5": IntelBundle(model_slug="mimo-v2.5", aa_intelligence_idx=99.0, supports_tools=True),
        "deepseek-v4-pro": IntelBundle(model_slug="deepseek-v4-pro", aa_intelligence_idx=98.0, supports_tools=True),
        "qwen3.5-397b": IntelBundle(model_slug="qwen3.5-397b", aa_intelligence_idx=90.0, supports_tools=False),
        "kimi-k2.6": IntelBundle(model_slug="kimi-k2.6", aa_intelligence_idx=90.0, supports_tools=False),
    })
    try:
        svc = LadderService()
        svc.rebuild(live)
        ladder = svc.ladder_for("coding_agentic")
        # mimo (quality=97 × tools affinity=1.25) must be first
        # deepseek (quality=98 × tools affinity=1.25) must be second
        assert ladder[0] == "opencode/mimo-v2.5"
        assert ladder[1] == "deepseek/deepseek-v4-pro"
    finally:
        _clear_score_cache()
