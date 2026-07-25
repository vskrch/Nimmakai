"""ModelLadderStore + ModelSelector custom-ladder tests."""

from __future__ import annotations

import pytest

from nimmakai.catalog.db import NimmakaiDB
from nimmakai.catalog.model_ladders import ModelLadder, ModelLadderStore


def _store(tmp_path) -> ModelLadderStore:
    db = NimmakaiDB(tmp_path / "test_ladders.db")
    s = ModelLadderStore(db=db)
    s.load()
    return s


def test_set_and_get(tmp_path) -> None:
    s = _store(tmp_path)
    s.set("nimmakai/coding", ["groq/llama-3.3-70b", "nim/qwen3.5"], note="speed")
    assert s.has_ladder("nimmakai/coding")
    lad = s.get("nimmakai/coding")
    assert lad is not None
    assert lad.chain == ["groq/llama-3.3-70b", "nim/qwen3.5"]
    assert lad.note == "speed"


def test_persistence_across_instances(tmp_path) -> None:
    s1 = _store(tmp_path)
    s1.set("nimmakai/coding", ["a", "b"])
    # New store backed by same DB must see the saved ladder
    s2 = ModelLadderStore(db=NimmakaiDB(tmp_path / "test_ladders.db"))
    s2.load()
    assert s2.has_ladder("nimmakai/coding")
    assert s2.get("nimmakai/coding").chain == ["a", "b"]


def test_clear_single(tmp_path) -> None:
    s = _store(tmp_path)
    s.set("nimmakai/coding", ["a"])
    s.set("nimmakai/auto-fast", ["b"])
    assert s.clear("nimmakai/coding") is True
    assert not s.has_ladder("nimmakai/coding")
    assert s.has_ladder("nimmakai/auto-fast")


def test_clear_nonexistent(tmp_path) -> None:
    s = _store(tmp_path)
    assert s.clear("nimmakai/coding") is False


def test_clear_all(tmp_path) -> None:
    s = _store(tmp_path)
    s.set("nimmakai/coding", ["a"])
    s.set("nimmakai/auto-fast", ["b"])
    s.clear_all()
    assert s.list_all() == []


def test_list_all_sorted(tmp_path) -> None:
    s = _store(tmp_path)
    s.set("nimmakai/auto-fast", ["b"])
    s.set("nimmakai/coding", ["a"])
    assert [l["model_id"] for l in s.list_all()] == ["nimmakai/auto-fast", "nimmakai/coding"]


def test_empty_store_no_ladder(tmp_path) -> None:
    s = _store(tmp_path)
    assert not s.has_ladder("nimmakai/coding")
    assert s.get("nimmakai/coding") is None


def test_ladder_roundtrip() -> None:
    lad = ModelLadder(
        model_id="nimmakai/coding",
        chain=["a", "b"],
        note="x",
        updated_at=123.0,
    )
    d = lad.to_dict()
    lad2 = ModelLadder.from_dict(d)
    assert lad2.model_id == "nimmakai/coding"
    assert lad2.chain == ["a", "b"]
    assert lad2.note == "x"
    assert lad2.updated_at == 123.0


def test_set_requires_model_id(tmp_path) -> None:
    s = _store(tmp_path)
    with pytest.raises(ValueError):
        s.set("", ["a"])