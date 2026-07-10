"""Model catalog package."""

from nimmakai.catalog.families import build_preference_chain, latest_in_family
from nimmakai.catalog.health import ModelHealthStore
from nimmakai.catalog.registry import ModelRegistry
from nimmakai.catalog.schema import AliasTarget, ModelsCatalog

__all__ = [
    "AliasTarget",
    "ModelHealthStore",
    "ModelRegistry",
    "ModelsCatalog",
    "build_preference_chain",
    "latest_in_family",
]
