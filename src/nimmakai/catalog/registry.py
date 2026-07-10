"""Versioned model catalog: docs + live API + family preferences + probes."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from nimmakai.catalog.aliases import normalize_model_name
from nimmakai.catalog.docs_fetcher import DocModel, enrich_publishers, fetch_models_md
from nimmakai.catalog.families import (
    SHARED_FALLBACKS,
    latest_in_family,
)
from nimmakai.catalog.health import ModelHealthStore
from nimmakai.catalog.prober import ProbeBudget, load_snapshot, probe_models, save_snapshot
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
        snapshot_path: Path | None = None,
        docs_url: str = "https://build.nvidia.com/models.md",
        probe_budget_per_hour: int = 8,
        enrich_doc_details: bool = True,
    ) -> None:
        self.catalog = catalog
        self.strict_catalog = strict_catalog
        self.health = health or ModelHealthStore()
        self.live_ids: set[str] = set()
        self.probed_ok: set[str] = set()
        self.doc_models: list[DocModel] = []
        self.dynamic_chains: dict[str, list[str]] = {}
        self.last_refresh_at: float | None = None
        self.last_refresh_ok: bool = False
        self.last_docs_ok: bool = False
        self._yaml_path: Path | None = None
        self.snapshot_path = snapshot_path or Path(".nimmakai/catalog_snapshot.json")
        self.docs_url = docs_url
        self.probe_budget = ProbeBudget(probe_budget_per_hour)
        self.enrich_doc_details = enrich_doc_details
        self._load_disk_snapshot()

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        strict_catalog: bool = False,
        snapshot_path: Path | None = None,
        docs_url: str = "https://build.nvidia.com/models.md",
        probe_budget_per_hour: int = 8,
    ) -> ModelRegistry:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"models catalog not found: {p}")
        with p.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        reg = cls(
            catalog_from_dict(data),
            strict_catalog=strict_catalog,
            snapshot_path=snapshot_path,
            docs_url=docs_url,
            probe_budget_per_hour=probe_budget_per_hour,
        )
        reg._yaml_path = p
        return reg

    @classmethod
    def from_settings(cls, settings: Any) -> ModelRegistry:
        path = Path(settings.models_config_path)
        if not path.is_absolute():
            candidates = [
                path,
                Path.cwd() / path,
                Path(__file__).resolve().parents[3] / path,
            ]
            for c in candidates:
                if c.is_file():
                    path = c
                    break
        snap = Path(
            getattr(settings, "catalog_snapshot_path", ".nimmakai/catalog_snapshot.json")
        )
        return cls.from_yaml(
            path,
            strict_catalog=settings.strict_catalog,
            snapshot_path=snap,
            docs_url=getattr(
                settings, "catalog_docs_url", "https://build.nvidia.com/models.md"
            ),
            probe_budget_per_hour=int(getattr(settings, "probe_budget_per_hour", 8)),
        )

    def _load_disk_snapshot(self) -> None:
        data = load_snapshot(self.snapshot_path)
        if not data:
            return
        self.live_ids = set(data.get("live_ids") or [])
        self.probed_ok = set(data.get("probed_ok") or [])
        self.dynamic_chains = {
            k: list(v) for k, v in (data.get("dynamic_chains") or {}).items()
        }
        logger.info(
            "loaded catalog snapshot (%s live ids, %s dynamic intents)",
            len(self.live_ids),
            len(self.dynamic_chains),
        )

    def _persist_snapshot(self) -> None:
        save_snapshot(
            self.snapshot_path,
            {
                "live_ids": sorted(self.live_ids),
                "probed_ok": sorted(self.probed_ok),
                "dynamic_chains": self.dynamic_chains,
                "saved_at": time.time(),
            },
        )

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
            if self.live_ids:
                return mid in self.live_ids
            return True
        return False

    def model_meta(self, model_id: str):
        return self.catalog.models.get(normalize_model_name(model_id))

    def chain_for_intent(self, intent: str) -> list[str]:
        if self.catalog.defaults.dynamic_families:
            if intent in self.dynamic_chains and self.dynamic_chains[intent]:
                return self._filter_available(list(self.dynamic_chains[intent]))
            # Build on the fly from current live_ids
            chain = self._build_dynamic(intent)
            if chain:
                return self._filter_available(chain)

        entry = self.catalog.intents.get(intent)
        if entry is None:
            entry = self.catalog.intents.get("coding_agentic")
        if entry is None:
            return []
        return self._filter_available(list(entry.chain))

    def _primary_family_for(self, intent: str) -> str:
        entry = self.catalog.intents.get(intent)
        if entry and entry.primary_family:
            return entry.primary_family
        fams = self.catalog.families
        if intent in {"coding_agentic", "long_horizon"}:
            return fams.coding_primary
        if intent == "vision":
            return fams.coding_primary  # qwen VL when present
        return fams.chat_primary

    def _build_dynamic(self, intent: str) -> list[str]:
        from nimmakai.catalog.families import all_in_family

        primary = self._primary_family_for(intent)
        fallbacks = tuple(self.catalog.families.fallbacks) or SHARED_FALLBACKS

        ids = self.live_ids or set()
        if not ids and self.dynamic_chains.get(intent):
            return list(self.dynamic_chains[intent])

        chain: list[str] = []
        head = latest_in_family(ids, primary)
        if head:
            chain.append(head)
        for fam in fallbacks:
            mid = latest_in_family(ids, fam)
            if mid and mid not in chain:
                chain.append(mid)

        # Soft: add next-best same-family variants as deeper fallbacks
        for fam in (primary, *fallbacks):
            for mid in all_in_family(ids, fam)[1:3]:
                if mid not in chain:
                    chain.append(mid)

        # Power-first: never reorder by probe confirmation — probes only
        # mark unavailable models via health cooldown, they get demoted later.
        return chain

    def _filter_available(self, chain: list[str]) -> list[str]:
        if not self.live_ids:
            return list(chain)
        filtered = [m for m in chain if m in self.live_ids]
        for m in chain:
            if m not in self.live_ids:
                logger.warning("catalog: skipping unavailable model id %s", m)
        if not filtered and self.strict_catalog:
            raise RuntimeError("strict_catalog: no models available for chain")
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
        tokens: int | None = None,
    ) -> None:
        self.health.record_outcome(
            model,
            key_id=key_id,
            success=success,
            latency=latency,
            status_code=status_code,
            unavailable=unavailable,
            tokens=tokens,
        )
        if success:
            self.probed_ok.add(model)

    def _join_docs_to_ids(self) -> set[str]:
        """Map doc slugs/publishers onto live API ids."""
        if not self.doc_models or not self.live_ids:
            return set(self.live_ids)

        by_slug: dict[str, str] = {}
        for mid in self.live_ids:
            slug = mid.split("/", 1)[-1].lower()
            by_slug.setdefault(slug, mid)

        matched: set[str] = set()
        for doc in self.doc_models:
            slug = doc.slug.lower().replace("_", "-")
            # try exact slug match against api model name
            for live_slug, mid in by_slug.items():
                if live_slug == slug or live_slug.replace("_", "-") == slug:
                    matched.add(mid)
                    break
            guess = doc.api_id_guess
            if guess and guess in self.live_ids:
                matched.add(guess)
        # Always keep full live set for resolution; docs enrich descriptions only
        return set(self.live_ids)

    def _rebuild_all_chains(self) -> None:
        intents = list(self.catalog.intents.keys()) or [
            "coding_agentic",
            "chat_fast",
            "reasoning",
            "long_horizon",
            "vision",
        ]
        for intent in intents:
            self.dynamic_chains[intent] = self._build_dynamic(intent)
        logger.info(
            "dynamic chains rebuilt: %s",
            {k: v[:3] for k, v in self.dynamic_chains.items()},
        )

    async def refresh_from_upstream(
        self,
        upstream: UpstreamClient,
        *,
        fetch_docs: bool = True,
        run_probes: bool = True,
    ) -> bool:
        api_ok = False
        try:
            status, body, _headers, _key = await upstream.request_json("GET", "/models")
            if status >= 400:
                logger.warning("catalog refresh failed: HTTP %s", status)
            else:
                ids: set[str] = set()
                data = body.get("data") if isinstance(body, dict) else None
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("id"):
                            ids.add(str(item["id"]))
                if ids:
                    self.live_ids = ids
                    api_ok = True
                    logger.info("catalog API refresh ok — %s live model(s)", len(ids))
        except Exception:
            logger.exception("catalog API refresh error")

        if fetch_docs:
            try:
                docs = await fetch_models_md(self.docs_url)
                if docs and self.enrich_doc_details:
                    docs = await enrich_publishers(docs, limit=30)
                if docs:
                    self.doc_models = docs
                    self.last_docs_ok = True
                else:
                    self.last_docs_ok = False
            except Exception:
                logger.exception("docs refresh failed")
                self.last_docs_ok = False

        if not api_ok and not self.live_ids:
            # fail-safe: keep snapshot
            self.last_refresh_ok = False
            logger.warning("catalog refresh degraded — using snapshot/live cache")
            if self.dynamic_chains:
                return False
            return False

        self._join_docs_to_ids()
        self._rebuild_all_chains()

        if run_probes and self.probe_budget.remaining() > 0:
            # Probe only chain heads we care about (anti-ban)
            candidates: list[str] = []
            for intent in ("coding_agentic", "chat_fast"):
                for mid in self.dynamic_chains.get(intent, [])[:2]:
                    if mid not in candidates:
                        candidates.append(mid)
            if candidates:
                results = await probe_models(upstream, candidates, self.probe_budget)
                for mid, st in results.items():
                    if st in {"ok", "rate_limited"}:
                        self.probed_ok.add(mid)
                    elif st == "unavailable":
                        self.health.record_outcome(
                            mid, success=False, status_code=404, unavailable=True
                        )
                self._rebuild_all_chains()

        self.last_refresh_at = time.monotonic()
        self.last_refresh_ok = api_ok or bool(self.live_ids)
        self._persist_snapshot()
        return self.last_refresh_ok

    def snapshot(self) -> dict[str, Any]:
        age = None
        if self.last_refresh_at is not None:
            age = round(time.monotonic() - self.last_refresh_at, 1)
        return {
            "yaml_version": self.catalog.version,
            "yaml_updated": self.catalog.updated,
            "yaml_path": str(self._yaml_path) if self._yaml_path else None,
            "live_model_count": len(self.live_ids),
            "docs_count": len(self.doc_models),
            "docs_ok": self.last_docs_ok,
            "probed_ok_count": len(self.probed_ok),
            "probe_budget_remaining": self.probe_budget.remaining(),
            "last_refresh_age_s": age,
            "last_refresh_ok": self.last_refresh_ok,
            "dynamic_families": self.catalog.defaults.dynamic_families,
            "families": self.catalog.families.model_dump(),
            "dynamic_chains": dict(self.dynamic_chains),
            "intents": {
                name: {
                    "description": ic.description,
                    "chain": self.chain_for_intent(name),
                    "primary_family": ic.primary_family,
                }
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
