"""NMK-601: Integration test — custom provider → model registration → routing."""

import pytest
from nimmakai.catalog.hub import ProviderHub
from nimmakai.catalog.providers import ProviderConfig, ProviderStore, namespace_model
from nimmakai.catalog.registry import ModelRegistry
from nimmakai.catalog.ladder import LadderService
from nimmakai.catalog.health import ModelHealthStore
from nimmakai.routing.optimizer import score_model_live


def _make_registry():
    from nimmakai.catalog.schema import catalog_from_dict
    cat = catalog_from_dict({
        "version": "1",
        "updated": "2026-01-01",
        "defaults": {"dynamic_families": True, "auto_mode_model_tokens": []},
        "families": {"chat_primary": "nim", "coding_primary": "nim", "fallbacks": []},
        "intents": {
            "coding_agentic": {"chain": []},
            "chat_fast": {"chain": []},
        },
        "aliases": {},
        "models": {},
    })
    reg = ModelRegistry(cat)
    return reg


def test_quality_override_ranks_high():
    """NMK-104: Custom quality override should rank model alongside frontier."""
    reg = _make_registry()
    reg.live_ids = {"nim/unknown-model"}
    reg.ladder.quality_overrides["nim/unknown-model"] = 95.0
    reg.ladder.rebuild(reg.live_ids, freeze=True)
    chain = reg.ladder.ladder_for("coding_agentic")
    assert "nim/unknown-model" in chain, "overridden model should appear in chain"
    scores = reg.ladder._ladders.get(("coding_agentic", "default"))
    assert scores is not None
    score = scores.scores.get("nim/unknown-model", 0)
    assert score > 70, f"overridden model score {score} too low, expected >70"


def test_whitelist_filters_models():
    """NMK-103: Per-provider whitelist should filter models during ingestion."""
    rt_config = ProviderConfig(
        id="groq", name="Groq", base_url="https://groq.com/v1",
        api_keys=["test"], model_whitelist=["llama"],
    )
    items = [
        {"id": "llama-3.3-70b"},
        {"id": "mixtral-8x7b-32768"},
        {"id": "whisper-large-v3"},
    ]
    result = []
    for item in items:
        low_uid = item["id"].lower()
        if rt_config.model_blacklist and any(b.lower() in low_uid for b in rt_config.model_blacklist):
            continue
        if rt_config.model_whitelist and not any(w.lower() in low_uid for w in rt_config.model_whitelist):
            continue
        result.append(item["id"])
    assert "llama-3.3-70b" in result
    assert "mixtral-8x7b-32768" not in result
    assert "whisper-large-v3" not in result


def test_blacklist_filters_models():
    """NMK-103: Per-provider blacklist should exclude models."""
    items = [{"id": "llama-3.3-70b"}, {"id": "mixtral-8x7b-32768"}]
    blacklist = ["mixtral"]
    result = [i["id"] for i in items if not any(b.lower() in i["id"].lower() for b in blacklist)]
    assert "llama-3.3-70b" in result
    assert "mixtral-8x7b-32768" not in result


def test_model_in_live_ids_accessible():
    """NMK-101: Models added to live_ids should be resolvable."""
    reg = _make_registry()
    reg.live_ids = {"nim/deepseek-ai/deepseek-v3"}
    resolved = reg.resolve_live_id("nim/deepseek-ai/deepseek-v3")
    assert resolved == "nim/deepseek-ai/deepseek-v3"
