"""Model selector resolution modes."""

from __future__ import annotations

from pathlib import Path

from nimmakai.catalog import ModelRegistry
from nimmakai.config import Settings
from nimmakai.routing import Intent, IntentResult, ModelSelector

YAML = Path(__file__).resolve().parents[1] / "config" / "models.yaml"

LIVE = {
    "qwen/qwen3.5-122b-a10b",
    "nvidia/nemotron-3-super-120b-a12b",
    "zai/glm-5.2",
    "stepfun/step-3.7-flash",
    "minimaxai/minimax-m3",
}


def _selector(**kwargs) -> ModelSelector:
    settings = Settings(nim_api_keys=["k"], **kwargs)
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = set(LIVE)
    reg._rebuild_all_chains()
    return ModelSelector(reg, settings)


def _intent(intent: Intent = Intent.CODING_AGENTIC) -> IntentResult:
    return IntentResult(intent=intent, confidence=0.9, rule_id="test")


def test_auto_mode_coding_uses_qwen() -> None:
    s = _selector()
    d = s.resolve("auto", _intent())
    assert d.mode == "auto"
    assert d.chain[0].startswith("qwen/")


def test_auto_mode_chat_uses_nemotron() -> None:
    s = _selector()
    d = s.resolve("nimmakai/auto", _intent(Intent.CHAT_FAST))
    assert d.mode == "auto"
    # Nemotron super (quality=86 × affinity=1.25 ≈ 107) should be in top 2
    # (Thompson Sampling may occasionally promote another model — by design)
    top2 = d.chain[:2]
    assert any("nemotron" in m for m in top2)


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
