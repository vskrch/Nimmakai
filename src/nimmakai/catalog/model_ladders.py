"""Per-model custom router ladders.

A model ladder is a user-defined ordered fallback chain for a specific virtual
model id (e.g. ``nimmakai/coding``). When a client requests that model and a
custom ladder exists, the chain is used as the passthrough-with-fallback order
(A available → else B → else C). The default ``nimmakai/auto`` is never written
here, so it stays on the intelligent auto-router.

Persistence: SQLite only (no JSON backup) — DB is the source of truth.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModelLadder:
    """Custom router chain for a specific virtual model id."""

    model_id: str
    chain: list[str] = field(default_factory=list)
    note: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "chain": list(self.chain),
            "note": self.note,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelLadder:
        return cls(
            model_id=str(d.get("model_id") or ""),
            chain=list(d.get("chain") or []),
            note=str(d.get("note") or ""),
            updated_at=float(d.get("updated_at") or 0),
        )


class ModelLadderStore:
    """Persistent per-model custom router ladders (SQLite-backed)."""

    def __init__(self, db: Any | None = None) -> None:
        self._db = db
        self.ladders: dict[str, ModelLadder] = {}

    def load(self) -> None:
        if self._db is None:
            return
        rows = self._db.list_model_ladders()
        self.ladders.clear()
        for row in rows:
            if row.get("model_id"):
                self.ladders[row["model_id"]] = ModelLadder.from_dict(row)
        logger.info("loaded %s custom model ladders", len(self.ladders))

    def get(self, model_id: str) -> ModelLadder | None:
        return self.ladders.get(model_id)

    def has_ladder(self, model_id: str) -> bool:
        lad = self.ladders.get(model_id)
        return lad is not None and len(lad.chain) > 0

    def set(
        self,
        model_id: str,
        chain: list[str],
        *,
        note: str = "",
    ) -> ModelLadder:
        if not model_id:
            raise ValueError("model_id is required")
        lad = ModelLadder(
            model_id=model_id,
            chain=list(chain),
            note=note,
            updated_at=time.time(),
        )
        self.ladders[model_id] = lad
        if self._db is not None:
            self._db.upsert_model_ladder(lad.to_dict())
        return lad

    def clear(self, model_id: str) -> bool:
        if model_id not in self.ladders:
            return False
        del self.ladders[model_id]
        if self._db is not None:
            self._db.delete_model_ladder(model_id)
        return True

    def clear_all(self) -> None:
        self.ladders.clear()
        if self._db is not None:
            self._db.clear_model_ladders()

    def list_all(self) -> list[dict[str, Any]]:
        return [
            l.to_dict()
            for l in sorted(self.ladders.values(), key=lambda x: x.model_id)
        ]