"""Coding-first scoring + emergency chain resilience."""

from __future__ import annotations

from potato.catalog.health import ModelHealthStore
from potato.catalog.intel_fetcher import IntelBundle
from potato.catalog.ladder import LadderService
from potato.catalog.learning import LearningStore
from potato.catalog.presets import get_preset, list_presets
from potato.catalog.score_cache import ModelScoreCache, recompute
from potato.resilience import emergency_coding_chain


def test_zen_preset_present() -> None:
    z = get_preset("zen")
    assert z is not None
    assert z["base_url"] == "https://opencode.ai/zen/v1"
    assert z["free_tier"] is True
    ids = {p["id"] for p in list_presets()}
    assert "zen" in ids


def test_coding_prefers_mimo_and_deepseek_v4() -> None:
    live = {
        "zen/mimo-v2.5-free",
        "zen/deepseek-v4-flash-free",
        "zen/big-pickle",
        "nim/deepseek-ai/deepseek-v4-pro",
        "nim/google/gemma-2-2b-it",
        "nim/nvidia/nemotron-3-nano-30b-a3b",
    }
    # Install score cache: coding-capable models get tools affinity boost
    bundles = {
        "mimo-v2.5-free": IntelBundle(model_slug="mimo-v2.5-free", aa_intelligence_idx=99.0, supports_tools=True),
        "deepseek-v4-flash-free": IntelBundle(model_slug="deepseek-v4-flash-free", aa_intelligence_idx=98.0, supports_tools=True),
        "big-pickle": IntelBundle(model_slug="big-pickle", aa_intelligence_idx=96.0, supports_tools=True),
        "deepseek-v4-pro": IntelBundle(model_slug="deepseek-v4-pro", aa_intelligence_idx=98.0, supports_tools=True),
        "gemma-2-2b-it": IntelBundle(model_slug="gemma-2-2b-it", aa_intelligence_idx=50.0, supports_tools=False),
        "nemotron-3-nano-30b-a3b": IntelBundle(model_slug="nemotron-3-nano-30b-a3b", aa_intelligence_idx=60.0, supports_tools=False),
    }
    cache = recompute(live_ids=live, intel_bundles=bundles, health=ModelHealthStore(), learning=LearningStore(), yaml_cfg={})
    ModelScoreCache.install(cache)
    try:
        svc = LadderService()
        svc.provider_ids = {"zen", "nim"}
        svc.rebuild(live)
        ladder = svc.ladder_for("coding_agentic")
        assert ladder[0] in {
            "zen/mimo-v2.5-free",
            "zen/deepseek-v4-flash-free",
            "nim/deepseek-ai/deepseek-v4-pro",
        }
        # Tiny gemma should not lead coding
        assert ladder[0] != "nim/google/gemma-2-2b-it"
        top3 = ladder[:3]
        assert any("mimo" in m or "deepseek" in m for m in top3)
    finally:
        ModelScoreCache._current = None


def test_emergency_chain_from_registry() -> None:
    class FakeReg:
        live_ids = {"nim/a", "nim/b"}
        ladder = LadderService()
        health = LadderService().health

        def health_reorder(self, chain):
            return chain

    FakeReg.ladder.rebuild(FakeReg.live_ids)
    chain = emergency_coding_chain(FakeReg(), max_n=5)
    assert len(chain) >= 1
