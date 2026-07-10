"""Per-intent user preferences — pin specific models or ladder order.

If user configures a preference for an intent, use it.
Otherwise fall back to the intelligent ladder (LadderService).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VALID_INTENTS = (
    "coding_agentic",
    "chat_fast",
    "reasoning",
    "long_horizon",
    "vision",
    "embeddings",
)


@dataclass
class IntentPreference:
    """User override for a specific intent."""

    intent: str
    chain: list[str] = field(default_factory=list)
    strict: bool = False
    note: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "chain": list(self.chain),
            "strict": self.strict,
            "note": self.note,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IntentPreference:
        return cls(
            intent=str(d.get("intent", "")),
            chain=list(d.get("chain") or []),
            strict=bool(d.get("strict", False)),
            note=str(d.get("note") or ""),
            updated_at=float(d.get("updated_at") or 0),
        )


@dataclass
class UserPreferences:
    """Persistent user overrides for per-intent model selection."""

    path: Path = field(
        default_factory=lambda: Path(".nimmakai/user_preferences.json")
    )
    preferences: dict[str, IntentPreference] = field(default_factory=dict)

    def get(self, intent: str) -> IntentPreference | None:
        return self.preferences.get(intent)

    def has_preference(self, intent: str) -> bool:
        pref = self.preferences.get(intent)
        return pref is not None and len(pref.chain) > 0

    def set(
        self,
        intent: str,
        chain: list[str],
        *,
        strict: bool = False,
        note: str = "",
    ) -> IntentPreference:
        if intent not in VALID_INTENTS:
            raise ValueError(
                f"Invalid intent '{intent}'. Must be one of: {VALID_INTENTS}"
            )
        pref = IntentPreference(
            intent=intent,
            chain=list(chain),
            strict=strict,
            note=note,
            updated_at=time.time(),
        )
        self.preferences[intent] = pref
        self.save()
        return pref

    def clear(self, intent: str) -> bool:
        if intent in self.preferences:
            del self.preferences[intent]
            self.save()
            return True
        return False

    def clear_all(self) -> None:
        self.preferences.clear()
        self.save()

    def list_all(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in sorted(self.preferences.values(), key=lambda x: x.intent)]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "preferences": {k: v.to_dict() for k, v in self.preferences.items()},
            "saved_at": time.time(),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.preferences.clear()
            for k, v in (raw.get("preferences") or {}).items():
                if isinstance(v, dict) and v.get("intent"):
                    self.preferences[k] = IntentPreference.from_dict(v)
            logger.info("loaded user preferences (%s intents)", len(self.preferences))
        except Exception:
            logger.exception("failed to load user preferences")
