"""Async multi-source fetcher for model intelligence data.

Internet sources are primary; disk-cached for restart resilience.
Source failures are fully isolated — one source going down never breaks others.

Sources (highest priority first in merge):
  1. OpenRouter /api/v1/models       (capabilities, context — no key needed)
  2. ArtificialAnalysis API          (intelligence_index, TPS — optional key)
  3. HuggingFace OpenEvals parquet   (MMLU, HumanEval normalized — no key)
  4. Arena-AI community JSON         (ELO scores — no key)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
import urllib.request
from dataclasses import dataclass, field, fields
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


@dataclass
class IntelBundle:
    """All externally-fetched intelligence data for one model (by bare slug)."""

    model_slug: str

    # Quality signals (None = this source did not report)
    aa_intelligence_idx: float | None = None
    hf_mmlu: float | None = None
    hf_humaneval: float | None = None
    arena_elo: float | None = None
    param_b: float | None = None
    context_length: int | None = None
    aa_tps: float | None = None

    # Capability flags (None = unknown, True/False = confirmed)
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_reasoning: bool | None = None
    supports_embeddings: bool | None = None

    sources: list[str] = field(default_factory=list)
    fetched_at: float = 0.0

    def merge_from(self, other: IntelBundle) -> None:
        """Fill gaps from other. Never overwrites non-None values."""
        for f in fields(self):
            if f.name in ("model_slug", "sources", "fetched_at"):
                continue
            if getattr(self, f.name) is None:
                v = getattr(other, f.name)
                if v is not None:
                    object.__setattr__(self, f.name, v)
        for s in other.sources:
            if s not in self.sources:
                self.sources.append(s)


# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------


async def _fetch_openrouter(client: httpx.AsyncClient) -> dict[str, IntelBundle]:
    """Public endpoint — no API key needed. Returns context_length, capabilities."""
    try:
        resp = await client.get(
            "https://openrouter.ai/api/v1/models",
            headers={"HTTP-Referer": "https://nimmakai.ai", "X-Title": "Nimmakai Router"},
            timeout=20.0,
        )
        resp.raise_for_status()
        models = resp.json().get("data") or []
    except Exception as exc:
        logger.warning("openrouter intel fetch failed: %s", exc)
        return {}

    bundles: dict[str, IntelBundle] = {}
    for m in models:
        slug = _normalize_slug(str(m.get("id") or ""))
        if not slug:
            continue
        sp = set(m.get("supported_parameters") or [])
        arch = m.get("architecture") or {}
        input_mods = set(arch.get("input_modalities") or [])
        output_mods = set(arch.get("output_modalities") or [])
        desc = str(m.get("description") or "").lower()
        bundles[slug] = IntelBundle(
            model_slug=slug,
            context_length=_safe_int(m.get("context_length")),
            supports_tools="tools" in sp,
            supports_vision="image" in input_mods,
            supports_reasoning=(
                "reasoning" in sp
                or "thinking" in sp
                or "reasoning" in desc
                or "chain-of-thought" in desc
            ),
            supports_embeddings="embeddings" in output_mods,
            sources=["openrouter"],
            fetched_at=time.time(),
        )
    logger.info("openrouter: %d model bundles fetched", len(bundles))
    return bundles


async def _fetch_artificial_analysis(
    client: httpx.AsyncClient, api_key: str | None
) -> dict[str, IntelBundle]:
    """Requires ARTIFICIAL_ANALYSIS_API_KEY. Graceful no-op if absent."""
    if not api_key:
        logger.debug("ARTIFICIAL_ANALYSIS_API_KEY not set; skipping source")
        return {}
    try:
        resp = await client.get(
            "https://api.artificialanalysis.ai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=25.0,
        )
        resp.raise_for_status()
        data = resp.json()
        models = data.get("models") or (data if isinstance(data, list) else [])
    except Exception as exc:
        logger.warning("artificialanalysis intel fetch failed: %s", exc)
        return {}

    bundles: dict[str, IntelBundle] = {}
    for m in models:
        slug = _normalize_slug(
            str(m.get("model_id") or m.get("id") or m.get("name") or "")
        )
        if not slug:
            continue
        bundles[slug] = IntelBundle(
            model_slug=slug,
            aa_intelligence_idx=_safe_float(
                m.get("intelligence_index") or m.get("quality_index")
            ),
            aa_tps=_safe_float(m.get("output_speed") or m.get("tokens_per_second")),
            sources=["artificialanalysis"],
            fetched_at=time.time(),
        )
    logger.info("artificialanalysis: %d model bundles fetched", len(bundles))
    return bundles


async def _fetch_hf_openeval(client: httpx.AsyncClient) -> dict[str, IntelBundle]:
    """Public HuggingFace OpenEvals parquet. Uses pyarrow (optional dep)."""
    url = (
        "https://huggingface.co/datasets/OpenEvals/leaderboard-data"
        "/resolve/main/data/train-00000-of-00001.parquet"
    )
    try:
        raw = await asyncio.to_thread(_download_bytes_sync, url, 30.0)
    except Exception as exc:
        logger.warning("hf openeval fetch failed: %s", exc)
        return {}
    try:
        import io

        import pyarrow.parquet as pq

        table = await asyncio.to_thread(lambda: pq.read_table(io.BytesIO(raw)))
        rows = table.to_pylist()
    except ImportError:
        logger.info("pyarrow not installed; skipping HF openeval source")
        return {}
    except Exception as exc:
        logger.warning("hf openeval parse failed: %s", exc)
        return {}

    bundles: dict[str, IntelBundle] = {}
    for row in rows:
        slug = _normalize_slug(str(row.get("model") or row.get("model_name") or ""))
        if not slug:
            continue
        bundles[slug] = IntelBundle(
            model_slug=slug,
            hf_mmlu=_safe_float(row.get("mmlu") or row.get("mmlu_score")),
            hf_humaneval=_safe_float(
                row.get("humaneval") or row.get("humaneval_score")
            ),
            sources=["hf_openeval"],
            fetched_at=time.time(),
        )
    logger.info("hf_openeval: %d model bundles fetched", len(bundles))
    return bundles


async def _fetch_arena_leaderboard(client: httpx.AsyncClient) -> dict[str, IntelBundle]:
    """Community arena leaderboard. No auth required."""
    try:
        resp = await client.get(
            "https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboards",
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        entries = (
            data if isinstance(data, list) else (data.get("data") or data.get("models") or [])
        )
    except Exception as exc:
        logger.warning("arena leaderboard fetch failed: %s", exc)
        return {}

    bundles: dict[str, IntelBundle] = {}
    for entry in entries:
        name = str(
            entry.get("model") or entry.get("model_name") or entry.get("name") or ""
        )
        slug = _normalize_slug(name)
        if not slug:
            continue
        elo = _safe_float(entry.get("elo") or entry.get("rating") or entry.get("arena_score"))
        bundles[slug] = IntelBundle(
            model_slug=slug,
            arena_elo=elo,
            sources=["arena"],
            fetched_at=time.time(),
        )
    logger.info("arena_leaderboard: %d model bundles fetched", len(bundles))
    return bundles


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class IntelFetcher:
    """Fetches model intelligence from all sources concurrently.

    Failures in any single source are fully isolated.
    Results disk-cached at cache_path for restart resilience.
    """

    def __init__(
        self,
        *,
        cache_path: Path = Path(".nimmakai/intel_cache.json"),
        ttl_hours: float = 6.0,
        aa_api_key: str | None = None,
    ) -> None:
        self.cache_path = cache_path
        self.ttl_hours = ttl_hours
        self.aa_api_key = aa_api_key
        self._mem_cache: dict[str, IntelBundle] | None = None
        self._mem_cache_at: float = 0.0

    async def fetch_all(self) -> dict[str, IntelBundle]:
        """Fetch all sources concurrently. Returns merged dict keyed by slug."""
        if self._mem_cache is not None:
            age_h = (time.time() - self._mem_cache_at) / 3600
            if age_h < self.ttl_hours:
                return self._mem_cache

        disk = self._load_disk_cache()

        async with httpx.AsyncClient(follow_redirects=True) as client:
            results = await asyncio.gather(
                _fetch_openrouter(client),
                _fetch_artificial_analysis(client, self.aa_api_key),
                _fetch_hf_openeval(client),
                _fetch_arena_leaderboard(client),
                return_exceptions=True,
            )

        source_names = ["openrouter", "artificialanalysis", "hf_openeval", "arena"]
        valid: list[dict[str, IntelBundle]] = []
        for i, r in enumerate(results):
            if isinstance(r, dict):
                valid.append(r)
            else:
                logger.warning("intel source [%s] failed: %s", source_names[i], r)

        merged = _merge_bundles(valid)

        # Backfill from disk for slugs not in fresh fetch
        if disk:
            for slug, b in disk.items():
                if slug not in merged:
                    merged[slug] = b

        self._mem_cache = merged
        self._mem_cache_at = time.time()
        self._save_disk_cache(merged)
        logger.info(
            "intel fetch complete: %d models, sources=%s",
            len(merged),
            sorted({s for b in merged.values() for s in b.sources}),
        )
        return merged

    def _load_disk_cache(self) -> dict[str, IntelBundle] | None:
        if not self.cache_path.is_file():
            return None
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            result: dict[str, IntelBundle] = {}
            all_field_names = {f.name for f in fields(IntelBundle)}
            for slug, d in (raw.get("bundles") or {}).items():
                b = IntelBundle(
                    model_slug=slug,
                    **{
                        k: v
                        for k, v in d.items()
                        if k in all_field_names and k != "model_slug"
                    },
                )
                result[slug] = b
            logger.info("intel disk cache loaded: %d bundles", len(result))
            return result
        except Exception as exc:
            logger.warning("intel cache load failed: %s", exc)
            return None

    def _save_disk_cache(self, bundles: dict[str, IntelBundle]) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": time.time(),
                "bundles": {
                    slug: {f.name: getattr(b, f.name) for f in fields(b)}
                    for slug, b in bundles.items()
                },
            }
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.cache_path)
        except Exception as exc:
            logger.warning("intel cache save failed: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_slug(model_id: str) -> str:
    """Strip provider prefix + date suffix. 'openai/gpt-4o-2024-11-20' -> 'gpt-4o'"""
    bare = str(model_id or "").strip().lower().rsplit("/", 1)[-1]
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", bare)


def _merge_bundles(sources: list[dict[str, IntelBundle]]) -> dict[str, IntelBundle]:
    """First source with a value for a field wins (highest priority first)."""
    merged: dict[str, IntelBundle] = {}
    for source in sources:
        for slug, bundle in source.items():
            if slug not in merged:
                merged[slug] = IntelBundle(model_slug=slug)
            merged[slug].merge_from(bundle)
    return merged


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _download_bytes_sync(url: str, timeout: float) -> bytes:
    """Synchronous download for asyncio.to_thread usage."""
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()