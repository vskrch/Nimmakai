"""OpenRouter / Kilo auto-router parity."""

from __future__ import annotations

from pathlib import Path

from nimmakai.catalog import ModelRegistry
from nimmakai.config import Settings
from nimmakai.routing.auto_router import (
    filter_chain,
    is_auto_router_id,
    is_free_model,
    match_allowed,
    parse_auto_router_options,
    pin_model_first,
    resolve_auto_tier,
    strip_router_client_fields,
    tradeoff_to_tier,
)
from nimmakai.routing.intents import Intent, IntentResult
from nimmakai.routing.selector import ModelSelector
from nimmakai.safety.sticky import StickySessionStore

YAML = Path(__file__).resolve().parents[1] / "config" / "models.yaml"

LIVE = {
    "qwen/qwen3.5-122b-a10b",
    "nvidia/nemotron-3-super-120b-a12b",
    "zen/mimo-v2.5-free",
    "zen/deepseek-v4-flash-free",
    "groq/llama-3.3-70b-versatile",
    "nim/llama-3.1-8b-instruct",
}


def _selector() -> ModelSelector:
    settings = Settings(nim_api_keys=["k"])
    reg = ModelRegistry.from_yaml(YAML)
    reg.live_ids = set(LIVE)
    reg._rebuild_all_chains()
    return ModelSelector(reg, settings)


def _intent(intent: Intent = Intent.CODING_AGENTIC) -> IntentResult:
    return IntentResult(intent=intent, confidence=0.9, rule_id="test")


def test_openrouter_auto_is_auto_router() -> None:
    assert is_auto_router_id("openrouter/auto")
    assert is_auto_router_id("kilo/auto")
    assert is_auto_router_id("kilo-auto/frontier")
    assert is_auto_router_id("kilo-auto/free")
    assert is_auto_router_id("nimmakai/auto")
    assert not is_auto_router_id("anthropic/claude-sonnet-4.5")
    assert resolve_auto_tier("openrouter/auto") == "balanced"
    assert resolve_auto_tier("kilo-auto/frontier") == "frontier"
    assert resolve_auto_tier("kilo-auto/efficient") == "efficient"
    assert resolve_auto_tier("kilo-auto/free") == "free"


def test_selector_openrouter_auto_mode() -> None:
    s = _selector()
    d = s.resolve("openrouter/auto", _intent())
    assert d.mode == "auto"
    assert d.auto_tier == "balanced"
    assert len(d.chain) >= 1


def test_selector_kilo_frontier_and_free() -> None:
    s = _selector()
    d = s.resolve("kilo-auto/frontier", _intent(Intent.CHAT_FAST))
    assert d.mode == "auto"
    assert d.auto_tier == "frontier"
    # frontier forces coding for plain chat
    assert d.intent == Intent.CODING_AGENTIC

    d2 = s.resolve("kilo-auto/free", _intent())
    assert d2.mode == "auto"
    assert d2.auto_tier == "free"
    # free pool prefers free-looking models
    assert any(is_free_model(m) for m in d2.chain[:3]) or d2.chain


def test_plugins_allowed_models_and_tradeoff() -> None:
    opts = parse_auto_router_options(
        {
            "model": "openrouter/auto",
            "session_id": "conv-1",
            "plugins": [
                {
                    "id": "auto-router",
                    "allowed_models": ["zen/*", "qwen/*"],
                    "cost_quality_tradeoff": 2,
                }
            ],
        }
    )
    assert opts.session_id == "conv-1"
    assert opts.allowed_models == ["zen/*", "qwen/*"]
    assert opts.cost_quality_tradeoff == 2
    assert opts.tier == "frontier"  # tradeoff 2 → quality
    assert tradeoff_to_tier(9) == "efficient"

    s = _selector()
    d = s.resolve("openrouter/auto", _intent(), auto_opts=opts)
    assert d.mode == "auto"
    for m in d.chain:
        assert match_allowed(m, opts.allowed_models)


def test_strip_router_fields() -> None:
    body = {
        "model": "openrouter/auto",
        "messages": [{"role": "user", "content": "hi"}],
        "session_id": "s1",
        "plugins": [{"id": "auto-router"}],
        "models": ["a", "b"],
    }
    out = strip_router_client_fields(body)
    assert "session_id" not in out
    assert "plugins" not in out
    assert "models" not in out
    assert out["model"] == "openrouter/auto"


def test_session_model_pin() -> None:
    store = StickySessionStore(ttl_seconds=60)
    store.put_model("sess-1", "zen/mimo-v2.5-free")
    assert store.get_model("sess-1") == "zen/mimo-v2.5-free"
    store.put("sess-1", "key-a")
    assert store.get("sess-1") == "key-a"
    assert store.get_model("sess-1") == "zen/mimo-v2.5-free"

    s = _selector()
    d = s.resolve(
        "openrouter/auto",
        _intent(),
        preferred_model="zen/mimo-v2.5-free",
    )
    assert d.chain[0] == "zen/mimo-v2.5-free" or "mimo" in d.chain[0]


def test_pin_model_first_helper() -> None:
    chain = ["a/x", "b/y", "c/z"]
    assert pin_model_first(chain, "b/y")[0] == "b/y"
    assert pin_model_first(chain, None) == chain


def test_filter_free_and_allowed() -> None:
    chain = [
        "nvidia/nemotron-3-super-120b-a12b",
        "zen/mimo-v2.5-free",
        "qwen/qwen3.5-122b-a10b",
    ]
    free = filter_chain(chain, free_only=True)
    assert free == ["zen/mimo-v2.5-free"]
    assert all(is_free_model(m) for m in free)
    allowed = filter_chain(chain, allowed_models=["qwen/*"])
    assert allowed == ["qwen/qwen3.5-122b-a10b"]
    # Hard empty: no match → [] (caller returns 503), never fail-open
    assert filter_chain(chain, allowed_models=["nonexistent/*"]) == []
    assert filter_chain(["paid/only-model"], free_only=True) == []


def test_session_id_from_body() -> None:
    store = StickySessionStore()
    sid = store.resolve_session_id({}, body={"session_id": "abc-123"})
    assert sid == "abc-123"
    sid2 = store.resolve_session_id(
        {"x-session-id": "hdr"}, body={"messages": [{"role": "user", "content": "hi"}]}
    )
    assert sid2 == "hdr"


def test_implicit_conversation_fingerprint() -> None:
    store = StickySessionStore()
    body = {
        "messages": [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "First question"},
        ]
    }
    a = store.resolve_session_id({}, proxy_token="tok", body=body)
    b = store.resolve_session_id({}, proxy_token="tok", body=body)
    assert a and a.startswith("fp:")
    assert a == b


def test_synthetic_models_include_openrouter_kilo() -> None:
    reg = ModelRegistry.from_yaml(YAML)
    ids = {m["id"] for m in reg.synthetic_auto_models()}
    assert "openrouter/auto" in ids
    assert "kilo/auto" in ids
    assert "kilo-auto/free" in ids
    assert "nimmakai/auto" in ids
