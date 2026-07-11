"""Catalog YAML loading, family resolution, dynamic chains."""

from __future__ import annotations

from pathlib import Path

import pytest

from nimmakai.catalog import ModelRegistry, latest_in_family
from nimmakai.catalog.families import matches_family
from nimmakai.catalog.health import ModelHealthStore
from nimmakai.catalog.schema import parse_alias_value

ROOT = Path(__file__).resolve().parents[1]
YAML = ROOT / "config" / "models.yaml"


def test_parse_alias_chain() -> None:
    t = parse_alias_value("chain:coding_agentic")
    assert t.kind == "chain"
    assert t.value == "coding_agentic"


def test_load_yaml_family_policy() -> None:
    reg = ModelRegistry.from_yaml(YAML)
    assert reg.catalog.defaults.dynamic_families is True
    assert reg.catalog.families.coding_primary == "qwen"
    assert reg.catalog.families.chat_primary == "nemotron"
    assert reg.catalog.families.fallbacks[0] == "glm_5_2"


def test_latest_nemotron_excludes_embed() -> None:
    ids = {
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/llama-nemotron-embed-1b-v2",
        "nvidia/nemotron-3-nano-30b-a3b",
    }
    latest = latest_in_family(ids, "nemotron")
    assert latest is not None
    assert "embed" not in latest
    assert "super" in latest or "nano" in latest


def test_latest_qwen_excludes_image() -> None:
    ids = {
        "qwen/qwen3.5-397b-a17b",
        "qwen/qwen3.5-122b-a10b",
        "qwen/qwen-image",
    }
    latest = latest_in_family(ids, "qwen")
    assert latest is not None
    assert "image" not in latest
    assert "397b" in latest or "122b" in latest


def test_registry_dynamic_chain_quality_first() -> None:
    """Quality-first scoring: best model wins regardless of family."""
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = {
        "qwen/qwen3.5-122b-a10b",
        "nvidia/nemotron-3-super-120b-a12b",
        "zai/glm-5.2",
        "stepfun/step-3.7-flash",
        "minimaxai/minimax-m3",
    }
    reg._rebuild_all_chains()
    coding = reg.chain_for_intent("coding_agentic")
    chat = reg.chain_for_intent("chat_fast")
    # Coding: qwen 3.5 122b (quality=88 × affinity=1.3 ≈ 114) should be in top 2
    assert any("qwen" in m for m in coding[:2])
    # Chat: all models present; nemotron super leads due to higher affinity
    assert len(chat) >= 3


def test_health_keeps_powerful_head_despite_slowness() -> None:
    """Slowness must NOT demote the strongest model — only errors/unavailability."""
    store = ModelHealthStore(min_samples=3)
    chain = ["powerful-slow", "weaker-fast"]
    for _ in range(3):
        store.record_outcome("powerful-slow", success=True, latency=9.0)
        store.record_outcome("weaker-fast", success=True, latency=1.0)
    reordered = store.health_reorder(chain)
    assert reordered[0] == "powerful-slow"


def test_health_demotes_unavailable_only() -> None:
    store = ModelHealthStore(min_samples=2)
    chain = ["primary", "fallback"]
    store.record_outcome("primary", success=False, status_code=404, unavailable=True)
    store.record_outcome("fallback", success=True, latency=0.5)
    reordered = store.health_reorder(chain)
    assert reordered[0] == "fallback"
    assert reordered[-1] == "primary"


def test_health_score_continuous() -> None:
    """New continuous health_score returns float in [0, 1]."""
    store = ModelHealthStore(min_samples=2)
    # Unknown model: optimistic
    assert store.health_score("unknown-model") == 1.0
    # Healthy model
    store.record_outcome("healthy", success=True, latency=0.5)
    store.record_outcome("healthy", success=True, latency=0.5)
    assert store.health_score("healthy") == 1.0
    # Failing model
    store.record_outcome("failing", success=False, status_code=500)
    store.record_outcome("failing", success=False, status_code=500)
    store.record_outcome("failing", success=False, status_code=500)
    assert store.health_score("failing") < 0.5
    # Cooldown model
    store.record_outcome("cooled", success=False, status_code=404, unavailable=True)
    assert store.health_score("cooled") == 0.01


def test_matches_family_glm() -> None:
    assert matches_family("zai/glm-5.2", "glm_5_2")
    assert matches_family("stepfun/step-3.7-flash", "step_3_7")
    assert matches_family("minimaxai/minimax-m3", "minimax_m3")


@pytest.mark.asyncio
async def test_refresh_intersects_live_ids() -> None:
    reg = ModelRegistry.from_yaml(YAML, probe_budget_per_hour=0)
    reg.enrich_doc_details = False

    class FakeUpstream:
        async def request_json(self, method, path, **kwargs):
            return (
                200,
                {
                    "data": [
                        {"id": "qwen/qwen3.5-122b-a10b"},
                        {"id": "nvidia/nemotron-3-super-120b-a12b"},
                        {"id": "zai/glm-5.2"},
                    ]
                },
                {},
                None,
            )

    ok = await reg.refresh_from_upstream(
        FakeUpstream(),  # type: ignore[arg-type]
        fetch_docs=False,
        run_probes=False,
    )
    assert ok
    coding = reg.chain_for_intent("coding_agentic")
    # Quality-first: qwen 3.5 should lead for coding
    assert any("qwen" in m for m in coding[:2])
    chat = reg.chain_for_intent("chat_fast")
    assert len(chat) >= 2
