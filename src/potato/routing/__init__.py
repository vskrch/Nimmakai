"""Intent classification and model selection."""

from potato.routing.auto_router import (
    AutoRouterOptions,
    build_intent_aware_pool,
    intent_expansion_order,
    is_auto_router_id,
    parse_auto_router_options,
    sticky_fits_intent_pool,
)
from potato.routing.classifier import IntentClassifier
from potato.routing.fallback import FallbackExecutor, RoutingStats
from potato.routing.intents import Intent, IntentResult
from potato.routing.selector import ModelSelector, RouteDecision

__all__ = [
    "AutoRouterOptions",
    "FallbackExecutor",
    "Intent",
    "IntentClassifier",
    "IntentResult",
    "ModelSelector",
    "RouteDecision",
    "RoutingStats",
    "build_intent_aware_pool",
    "intent_expansion_order",
    "is_auto_router_id",
    "parse_auto_router_options",
    "sticky_fits_intent_pool",
]
