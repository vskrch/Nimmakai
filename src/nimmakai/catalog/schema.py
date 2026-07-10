"""Pydantic schema for config/models.yaml."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CatalogDefaults(BaseModel):
    auto_mode_model_tokens: list[str] = Field(
        default_factory=lambda: ["auto", "nimmakai/auto", ""]
    )
    passthrough_if_known: bool = True
    max_fallback_attempts: int = 3
    classify_mode: Literal["rules_only", "rules_then_llm"] = "rules_only"


class IntentChain(BaseModel):
    description: str = ""
    chain: list[str] = Field(default_factory=list)


class ModelMeta(BaseModel):
    tiers: list[str] = Field(default_factory=list)
    quality_rank: int = 50
    supports_tools: bool = True
    supports_vision: bool = False
    soft_rpm: float | None = None


class ModelsCatalog(BaseModel):
    version: int = 1
    updated: str | None = None
    defaults: CatalogDefaults = Field(default_factory=CatalogDefaults)
    aliases: dict[str, str] = Field(default_factory=dict)
    intents: dict[str, IntentChain] = Field(default_factory=dict)
    models: dict[str, ModelMeta] = Field(default_factory=dict)


class AliasTarget(BaseModel):
    kind: Literal["chain", "model"]
    value: str


def parse_alias_value(raw: str) -> AliasTarget:
    """Parse 'chain:coding_agentic' or a bare model id."""
    text = raw.strip()
    if text.startswith("chain:"):
        return AliasTarget(kind="chain", value=text[len("chain:") :].strip())
    return AliasTarget(kind="model", value=text)


def catalog_from_dict(data: dict[str, Any]) -> ModelsCatalog:
    return ModelsCatalog.model_validate(data)
