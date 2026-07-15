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


def test_auto_mode_coding_uses_qwen(monkeypatch) -> None:
    monkeypatch.setattr("random.betavariate", lambda a, b: 0.5)
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
    # Best coding model always leads; user's model is in the chain
    assert d.chain[0] != "org/my-model"
    assert "org/my-model" in d.chain


def test_passthrough_with_fallback() -> None:
    s = _selector(enable_fallback_on_explicit=True)
    d = s.resolve("org/my-model", _intent())
    assert d.mode == "passthrough_with_fallback"
    # Best coding model always leads; user's model is in the chain
    assert d.chain[0] != "org/my-model"
    assert "org/my-model" in d.chain
    assert len(d.chain) > 1


def test_auto_cheap_mode() -> None:
    s = _selector()
    s.registry.live_ids.add("nim/llama-3.1-8b-instruct")
    s.registry.live_ids.add("nim/llama-3.1-405b-instruct")
    s.registry._rebuild_all_chains()
    d = s.resolve("nimmakai/auto-cheap", _intent())
    assert d.mode == "auto"
    # 8B is massively boosted by cheap mode vs 405B
    assert d.chain[0] == "nim/llama-3.1-8b-instruct"


def test_horizontal_fallback() -> None:
    s = _selector(enable_fallback_on_explicit=True)
    s.registry.live_ids.add("groq/llama-3.3-70b-versatile")
    s.registry.live_ids.add("cerebras/llama-3.3-70b-versatile")
    s.registry._rebuild_all_chains()

    d = s.resolve("groq/llama-3.3-70b-versatile", _intent())
    assert d.mode == "passthrough_with_fallback"
    # Best coding model always leads; requested model is still in the chain
    assert d.chain[0] != "groq/llama-3.3-70b-versatile"
    assert "groq/llama-3.3-70b-versatile" in d.chain
    assert "cerebras/llama-3.3-70b-versatile" in d.chain
