"""Coding-first scoring + emergency chain resilience."""

from __future__ import annotations

from nimmakai.catalog.ladder import LadderService
from nimmakai.catalog.presets import get_preset, list_presets
from nimmakai.resilience import emergency_coding_chain


def test_zen_preset_present() -> None:
    z = get_preset("zen")
    assert z is not None
    assert z["base_url"] == "https://opencode.ai/zen/v1"
    assert z["free_tier"] is True
    ids = {p["id"] for p in list_presets()}
    assert "zen" in ids


def test_coding_prefers_mimo_and_deepseek_v4() -> None:
    svc = LadderService()
    live = {
        "zen/mimo-v2.5-free",
        "zen/deepseek-v4-flash-free",
        "zen/big-pickle",
        "nim/deepseek-ai/deepseek-v4-pro",
        "nim/google/gemma-2-2b-it",
        "nim/nvidia/nemotron-3-nano-30b-a3b",
    }
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
