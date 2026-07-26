"""ModelLadderStore + ModelSelector custom-ladder tests."""

from __future__ import annotations

import pytest

from potato.catalog.db import PotatoDB
from potato.catalog.model_ladders import ModelLadder, ModelLadderStore


def _store(tmp_path) -> ModelLadderStore:
    db = PotatoDB(tmp_path / "test_ladders.db")
    s = ModelLadderStore(db=db)
    s.load()
    return s


def test_set_and_get(tmp_path) -> None:
    s = _store(tmp_path)
    s.set("potato/coding", ["groq/llama-3.3-70b", "nim/qwen3.5"], note="speed")
    assert s.has_ladder("potato/coding")
    lad = s.get("potato/coding")
    assert lad is not None
    assert lad.chain == ["groq/llama-3.3-70b", "nim/qwen3.5"]
    assert lad.note == "speed"


def test_persistence_across_instances(tmp_path) -> None:
    s1 = _store(tmp_path)
    s1.set("potato/coding", ["a", "b"])
    # New store backed by same DB must see the saved ladder
    s2 = ModelLadderStore(db=PotatoDB(tmp_path / "test_ladders.db"))
    s2.load()
    assert s2.has_ladder("potato/coding")
    assert s2.get("potato/coding").chain == ["a", "b"]


def test_clear_single(tmp_path) -> None:
    s = _store(tmp_path)
    s.set("potato/coding", ["a"])
    s.set("potato/auto-fast", ["b"])
    assert s.clear("potato/coding") is True
    assert not s.has_ladder("potato/coding")
    assert s.has_ladder("potato/auto-fast")


def test_clear_nonexistent(tmp_path) -> None:
    s = _store(tmp_path)
    assert s.clear("potato/coding") is False


def test_clear_all(tmp_path) -> None:
    s = _store(tmp_path)
    s.set("potato/coding", ["a"])
    s.set("potato/auto-fast", ["b"])
    s.clear_all()
    assert s.list_all() == []


def test_list_all_sorted(tmp_path) -> None:
    s = _store(tmp_path)
    s.set("potato/auto-fast", ["b"])
    s.set("potato/coding", ["a"])
    assert [l["model_id"] for l in s.list_all()] == ["potato/auto-fast", "potato/coding"]


def test_empty_store_no_ladder(tmp_path) -> None:
    s = _store(tmp_path)
    assert not s.has_ladder("potato/coding")
    assert s.get("potato/coding") is None


def test_ladder_roundtrip() -> None:
    lad = ModelLadder(
        model_id="potato/coding",
        chain=["a", "b"],
        note="x",
        updated_at=123.0,
    )
    d = lad.to_dict()
    lad2 = ModelLadder.from_dict(d)
    assert lad2.model_id == "potato/coding"
    assert lad2.chain == ["a", "b"]
    assert lad2.note == "x"
    assert lad2.updated_at == 123.0


def test_set_requires_model_id(tmp_path) -> None:
    s = _store(tmp_path)
    with pytest.raises(ValueError):
        s.set("", ["a"])


def test_potato_coding_custom_ladder_routing(tmp_path) -> None:
    """ModelSelector resolves potato/coding custom ladder without falling through to auto."""
    from potato.catalog.health import ModelHealthStore
    from potato.catalog.registry import ModelRegistry
    from potato.catalog.schema import catalog_from_dict
    from potato.config import Settings
    from potato.routing.intents import Intent, IntentResult
    from potato.routing.selector import ModelSelector

    cat = catalog_from_dict({"version": "1", "updated": "2026-01-01", "models": {}})
    reg = ModelRegistry(catalog=cat, health=ModelHealthStore())
    reg.live_ids = {"groq/llama-3.3-70b", "nim/qwen3.5"}
    s = _store(tmp_path)
    s.set("potato/coding", ["groq/llama-3.3-70b", "nim/qwen3.5"])

    sel = ModelSelector(reg, Settings(), model_ladders=s)
    res = sel.resolve(
        "potato/coding",
        IntentResult(intent=Intent.CODING_AGENTIC, confidence=1.0, rule_id="tools_present"),
    )
    assert res.mode == "passthrough_with_fallback"
    assert res.rule_id == "custom_ladder:potato/coding"
    assert res.chain[0] == "groq/llama-3.3-70b"
