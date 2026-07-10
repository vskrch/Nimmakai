"""Model selector resolution modes."""

from __future__ import annotations

from pathlib import Path

from nimmakai.catalog import ModelRegistry
from nimmakai.config import Settings
from nimmakai.routing import Intent, IntentResult, ModelSelector

YAML = Path(__file__).resolve().parents[1] / "config" / "models.yaml"


def _selector(**kwargs) -> ModelSelector:
    settings = Settings(nim_api_keys=["k"], **kwargs)
    reg = ModelRegistry.from_yaml(YAML)
    # Pretend all YAML models are live so chains aren't empty-filtered oddly
    all_ids: set[str] = set()
    for ic in reg.catalog.intents.values():
        all_ids.update(ic.chain)
    reg.live_ids = all_ids
    return ModelSelector(reg, settings)


def _intent(intent: Intent = Intent.CODING_AGENTIC) -> IntentResult:
    return IntentResult(intent=intent, confidence=0.9, rule_id="test")


def test_auto_mode() -> None:
    s = _selector()
    d = s.resolve("auto", _intent())
    assert d.mode == "auto"
    assert d.chain[0].startswith("minimaxai/") or "/" in d.chain[0]


def test_alias_to_chain() -> None:
    s = _selector()
    d = s.resolve("gpt-4o", _intent(Intent.CHAT_FAST))
    assert d.mode == "alias"
    assert len(d.chain) >= 1


def test_passthrough_explicit() -> None:
    s = _selector(enable_fallback_on_explicit=False)
    d = s.resolve("org/my-model", _intent())
    assert d.mode == "passthrough"
    assert d.chain == ["org/my-model"]


def test_passthrough_with_fallback() -> None:
    s = _selector(enable_fallback_on_explicit=True)
    d = s.resolve("org/my-model", _intent())
    assert d.mode == "passthrough_with_fallback"
    assert d.chain[0] == "org/my-model"
    assert len(d.chain) > 1


def test_unknown_alias_as_auto() -> None:
    s = _selector()
    d = s.resolve("gpt-4o-2024-xx-not-listed", _intent())
    assert d.mode == "unknown_alias_as_auto"
    assert len(d.chain) >= 1
