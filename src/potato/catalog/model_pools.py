"""Granular Model Pool & Intent Gating Store.

Allows administrators to restrict costly or specialized models to specific intents/routers
(e.g., allow deepseek-r1 only for coding/reasoning, and exclude from potato/auto).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModelPoolConfig:
    model_id: str
    allowed_intents: list[str] = field(default_factory=list)
    excluded_intents: list[str] = field(default_factory=list)
    allow_auto_router: bool = True
    note: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "allowed_intents": list(self.allowed_intents),
            "excluded_intents": list(self.excluded_intents),
            "allow_auto_router": self.allow_auto_router,
            "note": self.note,
            "updated_at": self.updated_at,
        }


class ModelPoolStore:
    """Thread-safe manager for per-model intent gating & pool inclusion."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._configs: dict[str, ModelPoolConfig] = {}

    def load(self) -> None:
        """Load all model pool configs from SQLite into memory cache."""
        if not self._db:
            return
        try:
            raw_map = self._db.load_model_pool_configs()
            loaded: dict[str, ModelPoolConfig] = {}
            for mid, item in raw_map.items():
                loaded[mid] = ModelPoolConfig(
                    model_id=mid,
                    allowed_intents=list(item.get("allowed_intents") or []),
                    excluded_intents=list(item.get("excluded_intents") or []),
                    allow_auto_router=bool(item.get("allow_auto_router", True)),
                    note=str(item.get("note") or ""),
                    updated_at=float(item.get("updated_at") or 0.0),
                )
            self._configs = loaded
            logger.info("loaded %d model pool gating configs from SQLite", len(self._configs))
        except Exception:
            logger.exception("failed loading model pool configs from SQLite")

    def get(self, model_id: str) -> ModelPoolConfig | None:
        return self._configs.get(model_id) or self._configs.get(model_id.lower())

    def is_allowed(
        self,
        model_id: str,
        intent: str,
        *,
        is_auto_router: bool = False,
    ) -> bool:
        """
        Check if model_id is allowed to participate in intent pool.
        """
        cfg = self.get(model_id)
        if not cfg:
            return True  # Unrestricted by default

        # 1. Check auto-router inclusion tag
        if is_auto_router and not cfg.allow_auto_router:
            return False

        # 2. Check explicit allowed intents (white list)
        if cfg.allowed_intents and intent not in cfg.allowed_intents:
            return False

        # 3. Check explicit excluded intents (black list)
        if cfg.excluded_intents and intent in cfg.excluded_intents:
            return False

        return True

    def set_config(
        self,
        model_id: str,
        allowed_intents: list[str],
        excluded_intents: list[str],
        allow_auto_router: bool = True,
        note: str = "",
    ) -> ModelPoolConfig:
        cfg = ModelPoolConfig(
            model_id=model_id,
            allowed_intents=[str(i).strip() for i in allowed_intents if str(i).strip()],
            excluded_intents=[str(i).strip() for i in excluded_intents if str(i).strip()],
            allow_auto_router=bool(allow_auto_router),
            note=str(note or ""),
            updated_at=time.time(),
        )
        self._configs[model_id] = cfg
        if self._db:
            self._db.upsert_model_pool_config(
                model_id=cfg.model_id,
                allowed_intents_json=json.dumps(cfg.allowed_intents),
                excluded_intents_json=json.dumps(cfg.excluded_intents),
                allow_auto_router=1 if cfg.allow_auto_router else 0,
                note=cfg.note,
                updated_at=cfg.updated_at,
            )
        return cfg

    def delete_config(self, model_id: str) -> bool:
        existed = model_id in self._configs
        if existed:
            del self._configs[model_id]
            if self._db:
                self._db.delete_model_pool_config(model_id)
        return existed

    def to_dict_list(self) -> list[dict[str, Any]]:
        return [cfg.to_dict() for cfg in self._configs.values()]
