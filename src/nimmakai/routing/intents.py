"""Intent types for model routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Intent(str, Enum):
    CODING_AGENTIC = "coding_agentic"
    CHAT_FAST = "chat_fast"
    REASONING = "reasoning"
    LONG_HORIZON = "long_horizon"
    VISION = "vision"
    EMBEDDINGS = "embeddings"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    confidence: float
    rule_id: str
    features: dict[str, Any] = field(default_factory=dict)
