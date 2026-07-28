"""Model selector resolution modes."""

from __future__ import annotations

from pathlib import Path

import pytest

from potato.catalog import ModelRegistry
from potato.catalog.model_ladders import ModelLadder, ModelLadderStore
from potato.catalog.preferences import IntentPreference, UserPreferences
from potato.config import Settings
from potato.routing import Intent, IntentResult, ModelSelector
from potato.routing.auto_router import AutoRouterOptions

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
    d = s.resolve("potato/auto", _intent(Intent.CHAT_FAST))
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
    # Explicit model stays pinned at head (F-08) — never surprise the client
    assert d.chain[0] == "org/my-model"
    assert d.pinned_head == "org/my-model"


def test_passthrough_with_fallback() -> None:
    s = _selector(enable_fallback_on_explicit=True)
    d = s.resolve("org/my-model", _intent())
    assert d.mode == "passthrough_with_fallback"
    assert d.chain[0] == "org/my-model"
    assert d.pinned_head == "org/my-model"
    assert len(d.chain) > 1


def test_auto_cheap_mode() -> None:
    s = _selector()
    s.registry.live_ids.add("nim/llama-3.1-8b-instruct")
    s.registry.live_ids.add("nim/llama-3.1-405b-instruct")
    s.registry._rebuild_all_chains()
    d = s.resolve("potato/auto-cheap", _intent())
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
    # Requested model pinned first; horizontal sibling remains as fallback
    assert d.chain[0] == "groq/llama-3.3-70b-versatile"
    assert d.pinned_head == "groq/llama-3.3-70b-versatile"
    assert "cerebras/llama-3.3-70b-versatile" in d.chain


# ── Regression: alias -> concrete model (NameError fix at selector.py:299) ──


def test_alias_to_model_with_fallback_enabled() -> None:
    """Regression for NameError: `optimized` referenced before assignment.

    Before fix: enable_fallback_on_explicit=True + alias→model raised NameError.
    Now: alias target pinned first, siblings appended as fallback.
    """
    s = _selector(enable_fallback_on_explicit=True)
    # Inject an alias that resolves to a concrete live model id (not chain:...)
    s.registry.catalog.aliases["my-model-alias"] = "qwen/qwen3.5-122b-a10b"
    d = s.resolve("my-model-alias", _intent())
    assert d.mode == "alias_model"
    assert d.chain[0] == "qwen/qwen3.5-122b-a10b"
    assert d.pinned_head == "qwen/qwen3.5-122b-a10b"
    # Fallback siblings from the coding chain are appended after the pinned head
    assert len(d.chain) > 1


def test_alias_to_model_with_fallback_disabled() -> None:
    """alias→model with fallback off yields a single-model chain."""
    s = _selector(enable_fallback_on_explicit=False)
    s.registry.catalog.aliases["my-model-alias"] = "qwen/qwen3.5-122b-a10b"
    d = s.resolve("my-model-alias", _intent())
    assert d.mode == "alias_model"
    assert d.chain == ["qwen/qwen3.5-122b-a10b"]
    assert d.pinned_head == "qwen/qwen3.5-122b-a10b"


def test_alias_to_model_unknown_target_passes_through() -> None:
    """alias→model whose value is not a live id still resolves (passthrough pin)."""
    s = _selector(enable_fallback_on_explicit=False)
    s.registry.catalog.aliases["my-model-alias"] = "org/unknown-model"
    d = s.resolve("my-model-alias", _intent())
    assert d.mode == "alias_model"
    assert d.chain[0] == "org/unknown-model"


def test_alias_to_model_disabled_target_raises() -> None:
    """alias→model whose target is admin-disabled raises model_disabled."""
    s = _selector(enable_fallback_on_explicit=True)
    target = "qwen/qwen3.5-122b-a10b"
    s.registry.catalog.aliases["my-model-alias"] = target
    s.registry.disabled_models.add(target)
    with pytest.raises(ValueError, match="model_disabled"):
        s.resolve("my-model-alias", _intent())


# ── routing_disabled branch (selector.py:109-121) ──


def test_routing_disabled_uses_raw_model() -> None:
    s = _selector()
    d = s.resolve("qwen/qwen3.5-122b-a10b", _intent(), routing_disabled=True)
    assert d.mode == "disabled"
    assert d.chain == ["qwen/qwen3.5-122b-a10b"]


def test_routing_disabled_falls_back_to_default_model() -> None:
    s = _selector(default_model="nvidia/nemotron-3-super-120b-a12b")
    d = s.resolve(None, _intent(), routing_disabled=True)
    assert d.mode == "disabled"
    assert d.chain == ["nvidia/nemotron-3-super-120b-a12b"]


def test_routing_disabled_empty_model_uses_auto() -> None:
    s = _selector()
    d = s.resolve("", _intent(), routing_disabled=True)
    assert d.mode == "disabled"
    assert d.chain == ["auto"]


# ── UNKNOWN intent defaults to CODING_AGENTIC (selector.py:87-88) ──


def test_unknown_intent_defaults_to_coding() -> None:
    s = _selector()
    d = s.resolve("auto", _intent(Intent.UNKNOWN))
    assert d.intent == Intent.CODING_AGENTIC


# ── default_model fallback when model field empty (selector.py:91-92) ──


def test_empty_model_uses_auto_router() -> None:
    """Empty model field → auto routing (is_auto_router_id('') is True).

    ``default_model`` fills ``raw`` for the routing-disabled path, but on the
    live path an empty model field is treated as auto regardless of
    ``default_model`` because ``is_auto_router_id('')`` returns True.
    """
    s = _selector(default_model="qwen/qwen3.5-122b-a10b")
    d = s.resolve("", _intent())
    assert d.mode == "auto"
    assert len(d.chain) >= 1


# ── tier forces coding intent (selector.py:98-105) ──


def test_coding_tier_forces_coding_intent() -> None:
    s = _selector()
    d = s.resolve("potato/auto-coding", _intent(Intent.CHAT_FAST))
    assert d.intent == Intent.CODING_AGENTIC


def test_frontier_tier_keeps_reasoning_when_classified() -> None:
    s = _selector()
    # frontier tier with reasoning intent stays reasoning (not forced to coding)
    d = s.resolve("kilo-auto/frontier", _intent(Intent.REASONING))
    assert d.intent == Intent.REASONING


# ── disabled model rejection (selector.py:123-125) ──


def test_disabled_known_model_raises() -> None:
    s = _selector()
    s.registry.disabled_models.add("qwen/qwen3.5-122b-a10b")
    with pytest.raises(ValueError, match="model_disabled"):
        s.resolve("qwen/qwen3.5-122b-a10b", _intent())


# ── Custom model ladder (selector.py:127-150) ──


def test_custom_model_ladder_used() -> None:
    s = _selector(enable_fallback_on_explicit=True)
    ladders = ModelLadderStore(db=None)
    ladders.set("potato/coding", ["qwen/qwen3.5-122b-a10b", "zai/glm-5.2"])
    s.model_ladders = ladders
    d = s.resolve("potato/coding", _intent())
    assert d.mode == "passthrough_with_fallback"
    assert d.rule_id == "custom_ladder:potato/coding"
    assert d.chain[0] == "qwen/qwen3.5-122b-a10b"
    assert "zai/glm-5.2" in d.chain


def test_custom_model_ladder_empty_chain_falls_through() -> None:
    s = _selector()
    ladders = ModelLadderStore(db=None)
    # Empty chain → not used, falls through to normal routing
    ladders.ladders["potato/coding"] = ModelLadder(model_id="potato/coding", chain=[])
    s.model_ladders = ladders
    d = s.resolve("potato/coding", _intent())
    # Falls through to auto-router (potato/coding is a coding-tier alias)
    assert d.mode in {"auto", "unknown_alias_as_auto"}


def test_custom_model_group_dynamic_reranking() -> None:
    """Admin custom model group is treated as a closed candidate pool re-ranked dynamically."""
    s = _selector()
    ladders = ModelLadderStore(db=None)
    # Admin defines a custom model group with models
    group_models = ["minimaxai/minimax-m3", "zai/glm-5.2", "qwen/qwen3.5-122b-a10b"]
    ladders.set("potato/coding", group_models)
    s.model_ladders = ladders

    d = s.resolve("potato/coding", _intent(Intent.CODING_AGENTIC))
    assert d.mode == "passthrough_with_fallback"
    assert d.rule_id == "custom_ladder:potato/coding"
    # All models in chain belong exclusively to the admin group candidate pool
    assert set(d.chain).issubset(set(group_models))
    # Dynamic scoring & health re-ranking ranks highest scoring model in the group first
    assert d.chain[0] in ("zai/glm-5.2", "qwen/qwen3.5-122b-a10b")


# ── User preferences (selector.py:152-185) ──


def test_user_preference_strict_passthrough() -> None:
    s = _selector()
    prefs = UserPreferences(path=Path("/tmp/potato-test-prefs.json"))
    prefs.preferences["coding_agentic"] = IntentPreference(
        intent="coding_agentic",
        chain=["zai/glm-5.2"],
        strict=True,
    )
    s.preferences = prefs
    d = s.resolve("auto", _intent())
    assert d.mode == "passthrough"
    assert d.chain[0] == "zai/glm-5.2"


def test_user_preference_non_strict_appends_siblings() -> None:
    s = _selector()
    prefs = UserPreferences(path=Path("/tmp/potato-test-prefs.json"))
    prefs.preferences["coding_agentic"] = IntentPreference(
        intent="coding_agentic",
        chain=["zai/glm-5.2"],
        strict=False,
    )
    s.preferences = prefs
    d = s.resolve("auto", _intent())
    assert d.mode == "passthrough_with_fallback"
    # Non-strict: preference model is merged with siblings and health-reordered;
    # the preference model must be present, siblings appended after.
    assert "zai/glm-5.2" in d.chain
    assert len(d.chain) > 1


# ── Embeddings intent (selector.py:187-223) ──


def test_embeddings_explicit_model_leads() -> None:
    s = _selector()
    s.registry.live_ids.add("nim/nv-embed-v1")
    s.registry._rebuild_all_chains()
    d = s.resolve("nim/nv-embed-v1", _intent(Intent.EMBEDDINGS))
    assert d.intent == Intent.EMBEDDINGS
    assert d.chain[0] == "nim/nv-embed-v1"


def test_embeddings_auto_uses_chain() -> None:
    s = _selector()
    d = s.resolve("auto", _intent(Intent.EMBEDDINGS))
    assert d.intent == Intent.EMBEDDINGS
    assert d.mode == "auto"


def test_embeddings_unknown_nim_id_passthrough() -> None:
    s = _selector()
    # No embeddings chain, raw looks like nim id → passthrough
    d = s.resolve("org/my-embed-model", _intent(Intent.EMBEDDINGS))
    assert d.intent == Intent.EMBEDDINGS


def test_embeddings_disabled_model_raises() -> None:
    s = _selector()
    s.registry.live_ids.add("nim/nv-embed-v1")
    s.registry.disabled_models.add("nim/nv-embed-v1")
    s.registry._rebuild_all_chains()
    with pytest.raises(ValueError, match="model_disabled"):
        s.resolve("nim/nv-embed-v1", _intent(Intent.EMBEDDINGS))


# ── Vision intent (selector.py:225-243) ──


def test_vision_empty_chain_raises() -> None:
    s = _selector()
    # Force the vision chain empty: disable every model the vision ladder picks.
    vision_chain = s.registry.chain_for_intent("vision")
    for m in vision_chain:
        s.registry.disabled_models.add(m)
    s.registry._rebuild_all_chains()
    with pytest.raises(ValueError, match="no_vision_model"):
        s.resolve("auto", _intent(Intent.VISION))


def test_vision_chain_returned() -> None:
    s = _selector()
    s.registry.live_ids.add("qwen/qwen3-vl-72b-instruct")
    s.registry._rebuild_all_chains()
    d = s.resolve("auto", _intent(Intent.VISION))
    assert d.intent == Intent.VISION
    assert d.mode == "auto"
    assert len(d.chain) >= 1


# ── Unknown alias → auto (selector.py:379-390) ──


def test_unknown_non_nim_string_treated_as_auto() -> None:
    s = _selector()
    d = s.resolve("some-unknown-virtual-model", _intent())
    assert d.mode == "unknown_alias_as_auto"
    assert len(d.chain) >= 1


# ── Auto-router hard guarantee: never empty when live pool has models ──


def test_auto_never_empty_when_live_pool_exists() -> None:
    s = _selector()
    d = s.resolve("auto", _intent())
    assert d.mode == "auto"
    assert len(d.chain) >= 1


# ── _finalize_chain: admin-disabled models dropped (selector.py:504-512) ──


def test_finalize_drops_disabled_models_from_chain() -> None:
    s = _selector(enable_fallback_on_explicit=True)
    # Disable a model that would normally appear in the coding chain
    s.registry.disabled_models.add("qwen/qwen3.5-122b-a10b")
    d = s.resolve("auto", _intent())
    assert "qwen/qwen3.5-122b-a10b" not in d.chain


# ── _finalize_chain: OpenRouter models[] fallback (selector.py:488-495) ──


def test_models_fallback_appended_to_chain() -> None:
    s = _selector(enable_fallback_on_explicit=True)
    s.registry.live_ids.add("groq/llama-3.3-70b-versatile")
    s.registry._rebuild_all_chains()
    opts = AutoRouterOptions(
        is_auto=True,
        tier="balanced",
        models_fallback=["groq/llama-3.3-70b-versatile"],
    )
    d = s.resolve("auto", _intent(), auto_opts=opts)
    assert "groq/llama-3.3-70b-versatile" in d.chain


def test_models_fallback_model_outside_intent_pool_appended() -> None:
    """models_fallback entry not in live_ids is appended at finalize.

    Covers selector.py:494-495: the model is not in ``live_ids`` (so
    ``build_intent_aware_pool`` never sees it and it's absent from the chain),
    ``resolve_live_id`` returns None so ``resolved = m``, and ``m not in chain``
    is True → the append branch executes.
    """
    s = _selector(enable_fallback_on_explicit=True)
    # Do NOT add custom/standalone-model to live_ids — it only enters via
    # models_fallback so the append branch at selector.py:495 is exercised.
    opts = AutoRouterOptions(
        is_auto=True,
        tier="balanced",
        models_fallback=["custom/standalone-model"],
    )
    d = s.resolve("auto", _intent(), auto_opts=opts)
    assert "custom/standalone-model" in d.chain


def test_models_fallback_disabled_model_skipped() -> None:
    s = _selector(enable_fallback_on_explicit=True)
    s.registry.live_ids.add("groq/llama-3.3-70b-versatile")
    s.registry.disabled_models.add("groq/llama-3.3-70b-versatile")
    s.registry._rebuild_all_chains()
    opts = AutoRouterOptions(
        is_auto=True,
        tier="balanced",
        models_fallback=["groq/llama-3.3-70b-versatile"],
    )
    d = s.resolve("auto", _intent(), auto_opts=opts)
    assert "groq/llama-3.3-70b-versatile" not in d.chain


# ── _finalize_chain: session pin (selector.py:513-520) ──


def test_session_pin_prepended_when_live() -> None:
    s = _selector()
    opts = AutoRouterOptions(is_auto=True, tier="balanced")
    d = s.resolve(
        "auto",
        _intent(),
        auto_opts=opts,
        preferred_model="zai/glm-5.2",
    )
    # Pinned model leads when it fits the intent pool
    assert d.chain[0] == "zai/glm-5.2"


def test_session_pin_disabled_not_prepended() -> None:
    s = _selector()
    s.registry.disabled_models.add("zai/glm-5.2")
    opts = AutoRouterOptions(is_auto=True, tier="balanced")
    d = s.resolve(
        "auto",
        _intent(),
        auto_opts=opts,
        preferred_model="zai/glm-5.2",
    )
    assert d.chain[0] != "zai/glm-5.2"


# ── routing_disabled: no model, no default → ["auto"] (selector.py:110-112) ──


def test_routing_disabled_no_model_no_default_uses_auto() -> None:
    s = _selector()  # no default_model
    d = s.resolve(None, _intent(), routing_disabled=True)
    assert d.mode == "disabled"
    assert d.chain == ["auto"]


def test_routing_disabled_no_model_with_default_uses_default() -> None:
    """Covers selector.py:111-112: empty raw + default_model → chain=[default]."""
    s = _selector(default_model="nvidia/nemotron-3-super-120b-a12b")
    d = s.resolve(None, _intent(), routing_disabled=True)
    assert d.mode == "disabled"
    assert d.chain == ["nvidia/nemotron-3-super-120b-a12b"]


# ── known model, resolved None, disabled check (selector.py:316-329) ──


def test_known_model_disabled_raises() -> None:
    """Model in catalog but admin-disabled → model_disabled (not passthrough)."""
    s = _selector()
    # Add a catalog model that resolves via provider prefix, then disable its live id
    s.registry.live_ids.add("nim/my-catalog-model")
    s.registry.catalog.models["nim/my-catalog-model"] = (
        type(s.registry.catalog.models.get("qwen/qwen3.5-122b-a10b"))()
        if s.registry.catalog.models
        else None
    )
    s.registry.disabled_models.add("nim/my-catalog-model")
    s.registry._rebuild_all_chains()
    with pytest.raises(ValueError, match="model_disabled"):
        s.resolve("nim/my-catalog-model", _intent())


def test_known_nim_id_unknown_live_passthrough() -> None:
    """NIM-style id not in live pool and not disabled → passthrough pin."""
    s = _selector(enable_fallback_on_explicit=False)
    d = s.resolve("nim/brand-new-unseen-model", _intent())
    # Looks like nim id, not disabled → passthrough
    assert d.mode == "passthrough"
    assert d.chain[0] == "nim/brand-new-unseen-model"


def test_known_non_nim_unknown_falls_through_to_auto() -> None:
    """Non-NIM id, not in live pool, not disabled → fall through to auto.

    Covers selector.py:325-327: `not looks_like_nim_id(raw)` → resolved stays None
    → falls through to the unknown_alias_as_auto branch.
    """
    s = _selector()
    d = s.resolve("some-virtual-model", _intent())
    assert d.mode == "unknown_alias_as_auto"
    assert len(d.chain) >= 1


def test_catalog_model_with_empty_live_pool_passthrough() -> None:
    """Catalog model + empty live pool → passthrough (model resolves from catalog).

    When ``live_ids`` is empty, ``resolve_live_id`` returns catalog models
    (selector.py:298), so a known catalog id resolves to itself and routes as
    passthrough rather than falling through to auto.
    """
    from potato.catalog.schema import ModelMeta

    s = _selector()
    s.registry.catalog.models["my-virtual"] = ModelMeta()
    s.registry.live_ids.clear()
    s.registry._rebuild_all_chains()
    d = s.resolve("my-virtual", _intent())
    assert d.mode == "passthrough_with_fallback"
    assert d.chain[0] == "my-virtual"


# ── embeddings: unknown nim id with empty chain passthrough (selector.py:200-203) ──


def test_embeddings_unknown_nim_id_no_chain_passthrough() -> None:
    s = _selector()
    # Clear embeddings chain context: raw is a nim id, chain empty, not auto
    d = s.resolve("org/my-embed-model", _intent(Intent.EMBEDDINGS))
    assert d.intent == Intent.EMBEDDINGS
    # org/my-embed-model is not in live_ids, looks_like_nim_id → passthrough [raw]
    assert d.chain[0] == "org/my-embed-model"


def test_embeddings_auto_id_with_empty_chain_uses_raw() -> None:
    """Embeddings + auto id (potato/auto) + empty chain → chain=[raw].

    Covers selector.py:202-203: the `elif not chain and raw and looks_like_nim_id`
    branch when raw is an auto-router id (is_auto True) so the explicit branch
    at 190 is skipped.
    """
    s = _selector()
    # embeddings chain is empty in the default LIVE set; potato/auto is_auto=True
    # but contains "/" so looks_like_nim_id is True
    d = s.resolve("potato/auto", _intent(Intent.EMBEDDINGS))
    assert d.intent == Intent.EMBEDDINGS
    assert "potato/auto" in d.chain


def test_embeddings_explicit_disabled_nim_id_raises() -> None:
    """Embeddings + explicit disabled nim id + empty chain → model_disabled.

    Covers selector.py:199-200: the disabled-hit check in the unknown-passthrough
    sub-branch.
    """
    s = _selector()
    s.registry.live_ids.add("nim/nv-embed-v1")
    s.registry.disabled_models.add("nim/nv-embed-v1")
    s.registry._rebuild_all_chains()
    with pytest.raises(ValueError, match="model_disabled"):
        s.resolve("nim/nv-embed-v1", _intent(Intent.EMBEDDINGS))


# ── _resolve_auto hard guarantee: empty finalized chain → rebuild (selector.py:443-461) ──


def test_auto_free_tier_with_no_free_models_returns_empty() -> None:
    """free tier but no free models → chain is empty (free_only is a hard filter).

    The hard guarantee's last-resort branch is gated on `not free_only`, so an
    empty free pool stays empty rather than serving a non-free model.
    Covers selector.py:443-455 (empty-chain rebuild path).
    """
    s = _selector()
    d = s.resolve("kilo-auto/free", _intent())
    assert d.mode == "auto"
    assert d.auto_tier == "free"
    # No free models in LIVE → empty chain (correct: free_only is strict)
    assert d.chain == []


# ── _finalize_chain: models_fallback already in chain is skipped (selector.py:495) ──


def test_models_fallback_already_in_chain_not_duplicated() -> None:
    s = _selector(enable_fallback_on_explicit=True)
    # qwen is in LIVE and the coding chain head; adding it via models[] is a no-op
    opts = AutoRouterOptions(
        is_auto=True,
        tier="balanced",
        models_fallback=["qwen/qwen3.5-122b-a10b"],
    )
    d = s.resolve("auto", _intent(), auto_opts=opts)
    # Present exactly once (no duplication)
    assert d.chain.count("qwen/qwen3.5-122b-a10b") == 1
