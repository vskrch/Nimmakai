"""Sticky best-model ranking cache."""

from __future__ import annotations

from pathlib import Path

from nimmakai.catalog.db import NimmakaiDB
from nimmakai.catalog.ladder import LadderService
from nimmakai.catalog.registry import ModelRegistry


def test_ladder_freeze_no_rescore() -> None:
    svc = LadderService()
    live = {
        "nim/deepseek-ai/deepseek-v4-pro",
        "nim/google/gemma-2-2b-it",
        "nim/qwen/qwen3.5-122b-a10b",
    }
    svc.rebuild(live, freeze=True)
    first = svc.ladder_for("coding_agentic")
    # Mutate health heavily — frozen cache must ignore re-score path
    for _ in range(5):
        svc.health.record_outcome(
            "nim/deepseek-ai/deepseek-v4-pro",
            success=False,
            status_code=500,
        )
    second = svc.ladder_for("coding_agentic")
    assert first == second
    assert first[0] == "nim/deepseek-ai/deepseek-v4-pro"


def test_ranking_cache_roundtrip(tmp_path: Path) -> None:
    db = NimmakaiDB(tmp_path / "r.db")
    svc = LadderService()
    live = {
        "nim/deepseek-ai/deepseek-v4-pro",
        "nim/deepseek-ai/deepseek-v4-flash",
        "nim/moonshotai/kimi-k2.6",
    }
    svc.rebuild(live, freeze=True)
    payload = svc.export_cache()
    assert payload["best_coding"]
    db.set_ranking_cache(payload)

    svc2 = LadderService()
    loaded = db.get_ranking_cache()
    assert loaded is not None
    assert svc2.import_cache(loaded, freeze=True)
    assert svc2.frozen is True
    assert svc2.ladder_for("coding_agentic")[:2] == payload["best_coding"][:2]


def test_registry_recompute_and_persist(tmp_path: Path) -> None:
    yaml = Path(__file__).resolve().parents[1] / "config" / "models.yaml"
    reg = ModelRegistry.from_yaml(yaml, snapshot_path=tmp_path / "snap.json")
    reg.live_ids = {
        "nim/deepseek-ai/deepseek-v4-pro",
        "nim/qwen/qwen3.5-397b-a17b",
        "nim/google/gemma-2-2b-it",
    }
    db = NimmakaiDB(tmp_path / "reg.db")
    reg.bind_db(db)
    best = reg.recompute_rankings(persist=True)
    assert best["coding_agentic"]
    assert reg.ladder.frozen is True
    # Reload into fresh registry
    reg2 = ModelRegistry.from_yaml(yaml, snapshot_path=tmp_path / "snap2.json")
    reg2.bind_db(db)
    assert reg2.ladder.frozen is True
    assert reg2.chain_for_intent("coding_agentic")[:1] == best["coding_agentic"][:1]
