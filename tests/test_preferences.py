"""UserPreferences tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from nimmakai.catalog.preferences import (
    VALID_INTENTS,
    IntentPreference,
    UserPreferences,
)


def test_set_and_get() -> None:
    prefs = UserPreferences(path=Path("/tmp/test_prefs.json"))
    prefs.set("coding_agentic", ["groq/llama-3.3-70b", "nim/qwen/qwen3.5"], note="test")
    assert prefs.has_preference("coding_agentic")
    p = prefs.get("coding_agentic")
    assert p is not None
    assert p.chain == ["groq/llama-3.3-70b", "nim/qwen/qwen3.5"]
    assert p.note == "test"
    assert p.strict is False


def test_invalid_intent_raises() -> None:
    prefs = UserPreferences(path=Path("/tmp/test_prefs.json"))
    with pytest.raises(ValueError, match="Invalid intent"):
        prefs.set("invalid_intent", ["model"])


def test_clear_single() -> None:
    prefs = UserPreferences(path=Path("/tmp/test_prefs.json"))
    prefs.set("coding_agentic", ["a"])
    prefs.set("chat_fast", ["b"])
    assert prefs.clear("coding_agentic") is True
    assert not prefs.has_preference("coding_agentic")
    assert prefs.has_preference("chat_fast")


def test_clear_nonexistent() -> None:
    prefs = UserPreferences(path=Path("/tmp/test_prefs.json"))
    assert prefs.clear("coding_agentic") is False


def test_clear_all() -> None:
    prefs = UserPreferences(path=Path("/tmp/test_prefs.json"))
    prefs.set("coding_agentic", ["a"])
    prefs.set("chat_fast", ["b"])
    prefs.clear_all()
    assert prefs.list_all() == []


def test_strict_mode() -> None:
    prefs = UserPreferences(path=Path("/tmp/test_prefs.json"))
    prefs.set("reasoning", ["model-a"], strict=True)
    p = prefs.get("reasoning")
    assert p is not None
    assert p.strict is True


def test_list_all_sorted() -> None:
    prefs = UserPreferences(path=Path("/tmp/test_prefs.json"))
    prefs.set("chat_fast", ["b"])
    prefs.set("coding_agentic", ["a"])
    prefs.set("reasoning", ["c"])
    all_prefs = prefs.list_all()
    assert [p["intent"] for p in all_prefs] == [
        "chat_fast",
        "coding_agentic",
        "reasoning",
    ]


def test_save_and_load(tmp_path: Path) -> None:
    p = tmp_path / "prefs.json"
    prefs = UserPreferences(path=p)
    prefs.set("coding_agentic", ["groq/llama"], note="saved")
    prefs.save()

    prefs2 = UserPreferences(path=p)
    prefs2.load()
    assert prefs2.has_preference("coding_agentic")
    loaded = prefs2.get("coding_agentic")
    assert loaded is not None
    assert loaded.chain == ["groq/llama"]
    assert loaded.note == "saved"


def test_load_nonexistent_file() -> None:
    prefs = UserPreferences(path=Path("/tmp/nonexistent.json"))
    prefs.load()
    assert prefs.list_all() == []


def test_preference_to_dict_roundtrip() -> None:
    pref = IntentPreference(
        intent="vision",
        chain=["nim/qwen-vl", "groq/llava"],
        strict=False,
        note="vision models",
        updated_at=1234567890.0,
    )
    d = pref.to_dict()
    pref2 = IntentPreference.from_dict(d)
    assert pref2.intent == "vision"
    assert pref2.chain == ["nim/qwen-vl", "groq/llava"]
    assert pref2.note == "vision models"
    assert pref2.updated_at == 1234567890.0


def test_all_valid_intents() -> None:
    prefs = UserPreferences(path=Path("/tmp/test_prefs.json"))
    for intent in VALID_INTENTS:
        prefs.set(intent, ["test-model"])
        assert prefs.has_preference(intent)
