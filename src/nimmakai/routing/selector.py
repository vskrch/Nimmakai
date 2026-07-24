"""Resolve client model field → ordered NIM model chain.

OpenRouter / Kilo parity: ``openrouter/auto``, ``kilo/auto``, ``kilo-auto/*``
trigger prompt-aware selection with optional session model pin + plugins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from nimmakai.catalog.aliases import looks_like_nim_id, normalize_model_name
from nimmakai.routing.auto_router import (
    AutoRouterOptions,
    filter_chain,
    is_auto_router_id,
    pin_model_first,
    resolve_auto_tier,
    tier_to_variant,
)
from nimmakai.routing.intents import Intent, IntentResult

if TYPE_CHECKING:
    from nimmakai.catalog.registry import ModelRegistry
    from nimmakai.config import Settings

RouteMode = Literal[
    "auto",
    "alias",
    "alias_model",
    "passthrough",
    "passthrough_with_fallback",
    "unknown_alias_as_auto",
    "disabled",
]


@dataclass
class RouteDecision:
    chain: list[str]
    mode: RouteMode
    intent: Intent
    rule_id: str
    requested_model: str | None
    # OpenRouter-style metadata
    auto_tier: str | None = None
    variant: str = "default"
    sticky_model: str | None = None
    # Sticky/explicit head that _chain must keep first unless unhealthy
    pinned_head: str | None = None
    allowed_models: list[str] = field(default_factory=list)
    # Estimated prompt tokens for context-length filtering (T13)
    estimated_tokens: int | None = None


class ModelSelector:
    def __init__(
        self,
        registry: ModelRegistry,
        settings: Settings,
        preferences: Any | None = None,
    ) -> None:
        self.registry = registry
        self.settings = settings
        self.preferences = preferences

    def resolve(
        self,
        model_field: str | None,
        intent_result: IntentResult,
        *,
        routing_disabled: bool = False,
        auto_opts: AutoRouterOptions | None = None,
        preferred_model: str | None = None,
    ) -> RouteDecision:
        intent = intent_result.intent
        if intent == Intent.UNKNOWN:
            intent = Intent.CODING_AGENTIC

        raw = normalize_model_name(model_field)
        if not raw and self.settings.default_model:
            raw = normalize_model_name(self.settings.default_model)

        opts = auto_opts or AutoRouterOptions()
        tier = opts.tier or resolve_auto_tier(raw if raw else model_field)
        variant = tier_to_variant(tier) if tier else "default"

        # Force coding intent for coding / frontier tiers (agent + architecture)
        if (
            tier in ("coding", "frontier")
            and intent not in (Intent.EMBEDDINGS, Intent.VISION)
            and (tier == "coding" or intent == Intent.CHAT_FAST)
        ):
            # frontier keeps reasoning/long_horizon when classified
            intent = Intent.CODING_AGENTIC

        intent_key = intent.value

        if routing_disabled:
            chain = [raw] if raw else []
            if not chain and self.settings.default_model:
                chain = [self.settings.default_model]
            return RouteDecision(
                chain=chain or ["auto"],
                mode="disabled",
                intent=intent,
                rule_id=intent_result.rule_id,
                requested_model=model_field,
                auto_tier=tier,
                variant=variant,
            )

        requested_live = self.registry.resolve_live_id(raw, include_disabled=True)
        if requested_live in self.registry.disabled_models:
            raise ValueError("model_disabled")

        # User preferences first
        if self.preferences is not None and self.preferences.has_preference(intent_key):
            pref = self.preferences.get(intent_key)
            if pref is not None and pref.chain:
                mode: RouteMode = (
                    "passthrough" if pref.strict else "passthrough_with_fallback"
                )
                chain = list(pref.chain)
                if not pref.strict:
                    siblings = self.registry.chain_for_intent(
                        intent_key, variant=variant
                    )
                    chain = chain + [m for m in siblings if m not in chain]
                chain = self._finalize_chain(
                    chain,
                    intent_key=intent_key,
                    variant=variant,
                    free_only=tier == "free",
                    allowed=opts.allowed_models,
                    preferred_model=preferred_model if tier else None,
                    models_fallback=opts.models_fallback,
                )
                return RouteDecision(
                    chain=chain,
                    mode=mode,
                    intent=intent,
                    rule_id=f"user_pref:{intent_key}",
                    requested_model=model_field,
                    auto_tier=tier,
                    variant=variant,
                    sticky_model=preferred_model,
                    pinned_head=preferred_model,
                    allowed_models=list(opts.allowed_models),
                )

        if intent == Intent.EMBEDDINGS:
            chain = self.registry.chain_for_intent("embeddings", variant=variant)
            # Explicit enabled embedding model must lead (not be swallowed by chain head)
            if raw and not self.registry.is_auto(raw):
                resolved = self.registry.resolve_live_id(raw)
                if resolved:
                    chain = [resolved] + [m for m in chain if m != resolved]
                elif not chain and looks_like_nim_id(raw):
                    # Unknown passthrough only when not admin-disabled
                    disabled_hit = self.registry.resolve_live_id(
                        raw, include_disabled=True
                    )
                    if disabled_hit in self.registry.disabled_models:
                        raise ValueError("model_disabled")
                    chain = [raw]
            elif not chain and raw and looks_like_nim_id(raw):
                chain = [raw]
            chain = self.registry.health_reorder(
                chain, intent=intent_key, variant=variant
            )
            # Keep explicit pin first after health reorder
            if raw and not self.registry.is_auto(raw):
                resolved = self.registry.resolve_live_id(raw)
                if resolved:
                    chain = pin_model_first(chain, resolved)
            return RouteDecision(
                chain=chain,
                mode="auto"
                if self.registry.is_auto(raw) or not raw
                else "passthrough",
                intent=intent,
                rule_id=intent_result.rule_id,
                requested_model=model_field,
                auto_tier=tier,
                variant=variant,
                pinned_head=self.registry.resolve_live_id(raw) if raw else None,
            )

        if intent == Intent.VISION:
            chain = self.registry.chain_for_intent("vision", variant=variant)
            chain = self.registry.health_reorder(
                chain, intent="vision", variant=variant
            )
            if not chain:
                # Never route image requests to alphabetical text models (T19)
                raise ValueError("no_vision_model")
            return RouteDecision(
                chain=chain,
                mode="auto",
                intent=intent,
                rule_id=intent_result.rule_id,
                requested_model=model_field,
                auto_tier=tier,
                variant=variant,
                pinned_head=preferred_model,
                sticky_model=preferred_model,
            )

        # Auto router (OpenRouter/Kilo/Nimmakai virtual models)
        is_auto = (
            raw == ""
            or self.registry.is_auto(raw)
            or is_auto_router_id(model_field)
            or is_auto_router_id(raw)
        )
        if is_auto:
            chain = self.registry.chain_for_intent(intent_key, variant=variant)
            if not chain and self.registry.active_live_ids():
                from nimmakai.resilience import emergency_chain

                chain = emergency_chain(self.registry, intent=intent_key)
                if not chain:
                    chain = sorted(self.registry.active_live_ids())
            chain = self._finalize_chain(
                chain,
                intent_key=intent_key,
                variant=variant,
                free_only=tier == "free",
                allowed=opts.allowed_models,
                preferred_model=preferred_model,
                models_fallback=opts.models_fallback,
            )
            return RouteDecision(
                chain=chain,
                mode="auto",
                intent=intent,
                rule_id=intent_result.rule_id,
                requested_model=model_field,
                auto_tier=tier or "balanced",
                variant=variant,
                sticky_model=preferred_model,
                pinned_head=preferred_model,
                allowed_models=list(opts.allowed_models),
            )

        if self.registry.is_alias(raw):
            target = self.registry.resolve_alias(raw)
            if target.kind == "chain":
                chain = self.registry.chain_for_intent(target.value, variant=variant)
                chain = self._finalize_chain(
                    chain,
                    intent_key=target.value,
                    variant=variant,
                    free_only=False,
                    allowed=opts.allowed_models,
                    preferred_model=None,
                    models_fallback=opts.models_fallback,
                )
                return RouteDecision(
                    chain=chain,
                    mode="alias",
                    intent=Intent(target.value)
                    if target.value in {i.value for i in Intent}
                    else intent,
                    rule_id=intent_result.rule_id,
                    requested_model=model_field,
                    variant=variant,
                )
            # alias → concrete model
            target_model = (
                self.registry.resolve_live_id(target.value, include_disabled=True)
                or target.value
            )
            if target_model in self.registry.disabled_models:
                raise ValueError("model_disabled")
            chain = [target_model]
            if self.settings.enable_fallback_on_explicit:
                siblings = self.registry.chain_for_intent(intent_key, variant=variant)
                chain = chain + [m for m in siblings if m != target_model]
            # For coding, always rank all candidates — the best coder leads,
            # the user-requested model stays as fallback.
            if intent_key == "coding_agentic":
                seen = set(chain)
                for m in self.registry.coding_candidates():
                    if m not in seen:
                        chain.append(m)
                        seen.add(m)
                chain = self.registry.health_reorder(
                    chain, intent=intent_key, variant=variant
                )
                head = chain[0]
                rest = [m for m in chain if m != head]
            else:
                optimized = self.registry.health_reorder(
                    chain, intent=intent_key, variant=variant
                )
                head = target_model
                rest = [m for m in optimized if m != head]
            return RouteDecision(
                chain=[head] + rest,
                mode="alias_model",
                intent=intent,
                rule_id=intent_result.rule_id,
                requested_model=model_field,
                variant=variant,
                pinned_head=head,
            )

        if self.registry.is_known(raw) or looks_like_nim_id(raw):
            resolved = self.registry.resolve_live_id(raw)
            if resolved is None:
                # Disabled or unknown: reject disabled explicitly; unknown NIM ids
                # may still passthrough if they are not in the disabled set.
                disabled_hit = self.registry.resolve_live_id(
                    raw, include_disabled=True
                )
                if disabled_hit in self.registry.disabled_models:
                    raise ValueError("model_disabled")
                if not looks_like_nim_id(raw):
                    # Fall through to auto below
                    resolved = None
                else:
                    resolved = raw
            if resolved is not None:
                if self.settings.enable_fallback_on_explicit:
                    siblings = self.registry.chain_for_intent(
                        intent_key, variant=variant
                    )
                    bare = resolved.split("/")[-1] if "/" in resolved else resolved
                    horizontals = [
                        m
                        for m in siblings
                        if (m.split("/")[-1] if "/" in m else m) == bare
                        and m != resolved
                    ]
                    rest = [
                        m for m in siblings if m != resolved and m not in horizontals
                    ]
                    rest_opt = self.registry.health_reorder(
                        rest, intent=intent_key, variant=variant
                    )
                    chain = (
                        [resolved]
                        + [m for m in horizontals if m != resolved]
                        + [
                            m
                            for m in rest_opt
                            if m != resolved and m not in horizontals
                        ]
                    )
                    mode: RouteMode = "passthrough_with_fallback"
                else:
                    chain = [resolved]
                    mode = "passthrough"
                # For coding, rank candidates but keep the requested model pinned
                # so _chain / sticky affinity is not silently discarded (F-08).
                if intent_key == "coding_agentic":
                    seen = set(chain)
                    for m in self.registry.coding_candidates():
                        if m not in seen:
                            chain.append(m)
                            seen.add(m)
                    chain = self.registry.health_reorder(
                        chain, intent=intent_key, variant=variant
                    )
                    chain = pin_model_first(chain, resolved)
                return RouteDecision(
                    chain=chain,
                    mode=mode,
                    intent=intent,
                    rule_id=intent_result.rule_id,
                    requested_model=model_field,
                    variant=variant,
                    pinned_head=resolved,
                )

        # Unknown non-NIM string → treat as auto (Cursor defaults, etc.)
        chain = self.registry.chain_for_intent(intent_key, variant=variant)
        chain = self._finalize_chain(
            chain,
            intent_key=intent_key,
            variant=variant,
            free_only=tier == "free",
            allowed=opts.allowed_models,
            preferred_model=preferred_model if tier else None,
            models_fallback=opts.models_fallback,
        )
        return RouteDecision(
            chain=chain,
            mode="unknown_alias_as_auto",
            intent=intent,
            rule_id=intent_result.rule_id,
            requested_model=model_field,
            auto_tier=tier,
            variant=variant,
            sticky_model=preferred_model,
            pinned_head=preferred_model,
            allowed_models=list(opts.allowed_models),
        )

    def _finalize_chain(
        self,
        chain: list[str],
        *,
        intent_key: str,
        variant: str,
        free_only: bool,
        allowed: list[str],
        preferred_model: str | None,
        models_fallback: list[str],
    ) -> list[str]:
        # OpenRouter models[] as extra fallback candidates
        if models_fallback:
            for m in models_fallback:
                discovered = self.registry.resolve_live_id(m, include_disabled=True)
                if discovered in self.registry.disabled_models:
                    continue
                resolved = self.registry.resolve_live_id(m) or m
                if resolved not in chain:
                    chain = chain + [resolved]

        # Always rank over the full live coding pool, not just the frozen
        # ladder subset — a newly-available coder can lead when it scores
        # best on capability × availability × latency.
        if intent_key == "coding_agentic":
            seen = set(chain)
            for m in self.registry.coding_candidates()[:20]:
                if m not in seen:
                    chain = chain + [m]
                    seen.add(m)

        chain = self.registry.health_reorder(
            chain, intent=intent_key, variant=variant
        )
        chain = filter_chain(
            chain, allowed_models=allowed or None, free_only=free_only
        )
        # Drop admin-disabled models from every finalized route chain
        if self.registry.live_ids and self.registry.disabled_models:
            chain = [
                m
                for m in chain
                if (
                    self.registry.resolve_live_id(m, include_disabled=True) or m
                )
                not in self.registry.disabled_models
            ]
        # Session model pin (OpenRouter sticky routing) — pin once after ranking
        preferred_live = (
            self.registry.resolve_live_id(preferred_model, include_disabled=True)
            if preferred_model
            else None
        )
        if preferred_model and preferred_live not in self.registry.disabled_models:
            chain = pin_model_first(chain, preferred_model)
        return chain
