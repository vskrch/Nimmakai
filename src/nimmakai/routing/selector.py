"""Resolve client model field → ordered NIM model chain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from nimmakai.catalog.aliases import looks_like_nim_id, normalize_model_name
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
    ) -> RouteDecision:
        intent = intent_result.intent
        if intent == Intent.UNKNOWN:
            intent = Intent.CODING_AGENTIC

        raw = normalize_model_name(model_field)
        if not raw and self.settings.default_model:
            raw = normalize_model_name(self.settings.default_model)

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
            )

        # Check user preferences first — if user pinned models for this intent, use them
        intent_key = intent.value
        if self.preferences is not None and self.preferences.has_preference(intent_key):
            pref = self.preferences.get(intent_key)
            if pref is not None and pref.chain:
                mode: RouteMode = "passthrough" if pref.strict else "passthrough_with_fallback"
                chain = list(pref.chain)
                if not pref.strict:
                    siblings = self.registry.chain_for_intent(intent_key)
                    chain = chain + [m for m in siblings if m not in chain]
                return RouteDecision(
                    chain=self.registry.health_reorder(chain),
                    mode=mode,
                    intent=intent,
                    rule_id=f"user_pref:{intent_key}",
                    requested_model=model_field,
                )

        if intent == Intent.EMBEDDINGS:
            chain = self.registry.chain_for_intent("embeddings")
            if not chain and raw and looks_like_nim_id(raw):
                chain = [raw]
            return RouteDecision(
                chain=self.registry.health_reorder(chain),
                mode="auto" if self.registry.is_auto(raw) or not raw else "passthrough",
                intent=intent,
                rule_id=intent_result.rule_id,
                requested_model=model_field,
            )

        if self.registry.is_auto(raw) or raw == "":
            chain = self.registry.chain_for_intent(intent_key)
            return RouteDecision(
                chain=self.registry.health_reorder(chain),
                mode="auto",
                intent=intent,
                rule_id=intent_result.rule_id,
                requested_model=model_field,
            )

        if self.registry.is_alias(raw):
            target = self.registry.resolve_alias(raw)
            if target.kind == "chain":
                chain = self.registry.chain_for_intent(target.value)
                return RouteDecision(
                    chain=self.registry.health_reorder(chain),
                    mode="alias",
                    intent=Intent(target.value)
                    if target.value in {i.value for i in Intent}
                    else intent,
                    rule_id=intent_result.rule_id,
                    requested_model=model_field,
                )
            # alias → concrete model
            chain = [target.value]
            if self.settings.enable_fallback_on_explicit:
                siblings = self.registry.chain_for_intent(intent_key)
                chain = chain + [m for m in siblings if m != target.value]
            return RouteDecision(
                chain=self.registry.health_reorder(chain),
                mode="alias_model",
                intent=intent,
                rule_id=intent_result.rule_id,
                requested_model=model_field,
            )

        if self.registry.is_known(raw) or looks_like_nim_id(raw):
            resolved = self.registry.resolve_live_id(raw) or raw
            if self.settings.enable_fallback_on_explicit:
                siblings = self.registry.chain_for_intent(intent_key)
                chain = [resolved] + [m for m in siblings if m != resolved]
                mode: RouteMode = "passthrough_with_fallback"
            else:
                chain = [resolved]
                mode = "passthrough"
            return RouteDecision(
                chain=self.registry.health_reorder(chain)
                if mode == "passthrough_with_fallback"
                else chain,
                mode=mode,
                intent=intent,
                rule_id=intent_result.rule_id,
                requested_model=model_field,
            )

        # Unknown non-NIM string → treat as auto
        chain = self.registry.chain_for_intent(intent_key)
        return RouteDecision(
            chain=self.registry.health_reorder(chain),
            mode="unknown_alias_as_auto",
            intent=intent,
            rule_id=intent_result.rule_id,
            requested_model=model_field,
        )
