"""Versioned model catalog: YAML + live /v1/models intersection."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from nimmakai.catalog.aliases import looks_like_nim_id, normalize_model_name
from nimmakai.catalog.health import ModelHealthStore
from nimmakai.catalog.schema import (
    AliasTarget,
    ModelsCatalog,
    catalog_from_dict,
    parse_alias_value,
)

if TYPE_CHECKING:
    from nimmakai.upstream import UpstreamClient

logger = logging.getLogger(__name__)


class ModelRegistry:
    def __init__(
        self,
        catalog: ModelsCatalog,
        *,
        strict_catalog: bool = False,
        health: ModelHealthStore | None = None,
    ) -> None:
        self.catalog = catalog
        self.strict_catalog = strict_catalog
        self.health = health or ModelHealthStore()
        self.live_ids: set[str] = set()
        self.last_refresh_at: float | None = None  # monotonic
        self.last_refresh_ok: bool = False
        self._yaml_path: Path | None = None

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        strict_catalog: bool = False,
    ) -> ModelRegistry:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"models catalog not found: {p}")
        with p.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        reg = cls(catalog_from_dict(data), strict_catalog=strict_catalog)
        reg._yaml_path = p
        return reg

    @classmethod
    def from_settings(cls, settings: Any) -> ModelRegistry:
        path = Path(settings.models_config_path)
        if not path.is_absolute():
            # Resolve relative to CWD first, then repo-ish parents
            candidates = [
                path,
                Path.cwd() / path,
                Path(__file__).resolve().parents[3] / path,
            ]
            for c in candidates:
                if c.is_file():
                    path = c
                    break
        return cls.from_yaml(path, strict_catalog=settings.strict_catalog)

    def auto_tokens(self) -> set[str]:
        return {normalize_model_name(t) for t in self.catalog.defaults.auto_mode_model_tokens}

    def is_auto(self, model: str | None) -> bool:
        return normalize_model_name(model) in self.auto_tokens()

    def is_alias(self, name: str | None) -> bool:
        n = normalize_model_name(name)
        return n in self.catalog.aliases

    def resolve_alias(self, name: str) -> AliasTarget:
        raw = self.catalog.aliases[normalize_model_name(name)]
        return parse_alias_value(raw)

    def is_known(self, model_id: str) -> bool:
        mid = normalize_model_name(model_id)
        if mid in self.live_ids:
            return True
        if mid in self.catalog.models:
            # Known from YAML; if we have live data, prefer live
            if self.live_ids:
                return mid in self.live_ids
            return True
        return False

    def model_meta(self, model_id: str):
        return self.catalog.models.get(normalize_model_name(model_id))

    def chain_for_intent(self, intent: str) -> list[str]:
        entry = self.catalog.intents.get(intent)
        if entry is None:
            entry = self.catalog.intents.get("coding_agentic")
        if entry is None:
            return []
        return self._filter_available(list(entry.chain))

    def _filter_available(self, chain: list[str]) -> list[str]:
        if not self.live_ids:
            # No live probe yet — return YAML chain as-is
            return list(chain)
        filtered = [m for m in chain if m in self.live_ids]
        skipped = [m for m in chain if m not in self.live_ids]
        for m in skipped:
            logger.warning("catalog: skipping unavailable model id %s", m)
        if not filtered and self.strict_catalog:
            raise RuntimeError("strict_catalog: no models available for chain")
        # If everything filtered out, fall back to YAML so we still try
        return filtered if filtered else list(chain)

    def health_reorder(self, chain: list[str]) -> list[str]:
        return self.health.health_reorder(chain)

    def record_outcome(
        self,
        model: str,
        key_id: str | None,
        success: bool,
        latency: float | None = None,
        status_code: int | None = None,
        unavailable: bool = False,
    ) -> None:
        self.health.record_outcome(
            model,
            key_id=key_id,
            success=success,
            latency=latency,
            status_code=status_code,
            unavailable=unavailable,
        )

    async def refresh_from_upstream(self, upstream: UpstreamClient) -> bool:
        try:
            status, body, _headers, _key = await upstream.request_json("GET", "/models")
            if status >= 400:
                logger.warning("catalog refresh failed: HTTP %s", status)
                self.last_refresh_ok = False
                return False
            ids: set[str] = set()
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("id"):
                        ids.add(str(item["id"]))
            self.live_ids = ids
            self.last_refresh_at = time.monotonic()
            self.last_refresh_ok = True
            logger.info("catalog refresh ok — %s live model(s)", len(ids))
            return True
        except Exception:
            logger.exception("catalog refresh error")
            self.last_refresh_ok = False
            return False

    def snapshot(self) -> dict[str, Any]:
        age = None
        if self.last_refresh_at is not None:
            age = round(time.monotonic() - self.last_refresh_at, 1)
        return {
            "yaml_version": self.catalog.version,
            "yaml_updated": self.catalog.updated,
            "yaml_path": str(self._yaml_path) if self._yaml_path else None,
            "live_model_count": len(self.live_ids),
            "last_refresh_age_s": age,
            "last_refresh_ok": self.last_refresh_ok,
            "intents": {
                name: {"description": ic.description, "chain": list(ic.chain)}
                for name, ic in self.catalog.intents.items()
            },
            "aliases": dict(self.catalog.aliases),
            "health": self.health.snapshot(),
        }

    def synthetic_auto_model(self) -> dict[str, Any]:
        return {
            "id": "nimmakai/auto",
            "object": "model",
            "created": 0,
            "owned_by": "nimmakai",
            "permission": [],
            "root": "nimmakai/auto",
            "parent": None,
        }
