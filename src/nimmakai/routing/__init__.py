"""Intent classification and model selection."""

from nimmakai.routing.auto_router import (
    AutoRouterOptions,
    is_auto_router_id,
    parse_auto_router_options,
)
from nimmakai.routing.classifier import IntentClassifier
from nimmakai.routing.fallback import FallbackExecutor, RoutingStats
from nimmakai.routing.intents import Intent, IntentResult
from nimmakai.routing.selector import ModelSelector, RouteDecision

__all__ = [
    "AutoRouterOptions",
    "FallbackExecutor",
    "Intent",
    "IntentClassifier",
    "IntentResult",
    "ModelSelector",
    "RouteDecision",
    "RoutingStats",
    "is_auto_router_id",
    "parse_auto_router_options",
]
