"""Dynamic model cost lookup using models.dev API."""

from __future__ import annotations

import logging
import threading
import time

import httpx

logger = logging.getLogger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"
DEFAULT_TTL_SECONDS = 3600  # 1 hour


def _normalize(model_id: str) -> str:
    return (model_id or "").strip().lower()


def _bare_name(model_id: str) -> str:
    mid = _normalize(model_id)
    return mid.rsplit("/", 1)[-1] if mid else ""


class ModelsDevCostCache:
    """Thread-safe, TTL-based cache for model costs from models.dev."""

    def __init__(
        self,
        url: str = MODELS_DEV_URL,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        # Primary index: every useful alias → rates
        self._data: dict[str, tuple[float, float]] = {}
        # Canonical keys only (for list_default_rates)
        self._canonical: dict[str, tuple[float, float]] = {}
        self._expires_at: float = 0.0
        self._last_fetch_ok: bool = False

    def _is_expired(self) -> bool:
        return time.monotonic() >= self._expires_at

    @staticmethod
    def _index_aliases(
        result: dict[str, tuple[float, float]],
        rates: tuple[float, float],
        *aliases: str,
    ) -> None:
        for alias in aliases:
            key = _normalize(alias)
            if not key:
                continue
            # Prefer first non-zero rate when colliding; otherwise keep first.
            existing = result.get(key)
            if existing is None or (existing == (0.0, 0.0) and rates != (0.0, 0.0)):
                result[key] = rates

    def _fetch(self) -> tuple[dict[str, tuple[float, float]], dict[str, tuple[float, float]]]:
        """Fetch and parse the models.dev API.

        Returns (alias_index, canonical_index).
        """
        try:
            resp = httpx.get(self._url, timeout=15.0)
            resp.raise_for_status()
            raw = resp.json()
        except Exception:
            logger.debug("Failed to fetch models.dev pricing data", exc_info=True)
            return {}, {}

        aliases: dict[str, tuple[float, float]] = {}
        canonical: dict[str, tuple[float, float]] = {}
        if not isinstance(raw, dict):
            return aliases, canonical

        for provider_id, provider_data in raw.items():
            if not isinstance(provider_data, dict):
                continue
            models = provider_data.get("models")
            if not isinstance(models, dict):
                continue
            pid = _normalize(str(provider_id))
            for model_id, model_data in models.items():
                if not isinstance(model_data, dict):
                    continue
                cost = model_data.get("cost")
                if not isinstance(cost, dict):
                    continue
                input_cost = cost.get("input")
                output_cost = cost.get("output")
                if input_cost is None or output_cost is None:
                    continue
                try:
                    inp = float(input_cost)
                    out = float(output_cost)
                except (TypeError, ValueError):
                    continue
                if inp < 0 or out < 0:
                    continue
                rates = (inp, out)
                mid = _normalize(str(model_id))
                # Canonical key: provider/model (even when model already namespaced)
                canon = f"{pid}/{mid}"
                canonical[canon] = rates
                self._index_aliases(aliases, rates, canon, mid, _bare_name(mid))
                # Also index model.id when present and different
                raw_id = model_data.get("id")
                if isinstance(raw_id, str) and raw_id.strip():
                    self._index_aliases(
                        aliases, rates, raw_id, f"{pid}/{raw_id}", _bare_name(raw_id)
                    )

        return aliases, canonical

    def _ensure_loaded(self) -> None:
        if not self._is_expired() and self._data:
            return
        with self._lock:
            if not self._is_expired() and self._data:
                return
            new_aliases, new_canonical = self._fetch()
            if new_aliases:
                self._data = new_aliases
                self._canonical = new_canonical
                self._last_fetch_ok = True
                self._expires_at = time.monotonic() + self._ttl
                logger.info(
                    "Loaded %d model cost aliases (%d canonical) from models.dev",
                    len(new_aliases),
                    len(new_canonical),
                )
            else:
                if not self._data:
                    self._last_fetch_ok = False
                # Back off briefly on failure so we don't hammer the API
                self._expires_at = time.monotonic() + min(self._ttl, 300)

    def lookup(self, model_id: str) -> tuple[float, float] | None:
        """Return (input_per_M, output_per_M) or None if not found.

        Matching order:
        1. Exact normalized id (e.g. ``openai/gpt-4o``)
        2. Strip first namespace (``nim/deepseek-chat`` → ``deepseek-chat``)
        3. Bare trailing segment
        """
        self._ensure_loaded()
        mid = _normalize(model_id)
        if not mid:
            return None
        with self._lock:
            if mid in self._data:
                return self._data[mid]
            if "/" in mid:
                rest = mid.split("/", 1)[1]
                if rest in self._data:
                    return self._data[rest]
            bare = _bare_name(mid)
            if bare and bare in self._data:
                return self._data[bare]
            return None

    def all_rates(self) -> dict[str, tuple[float, float]]:
        """Return a copy of canonical rates (provider/model keys)."""
        self._ensure_loaded()
        with self._lock:
            return dict(self._canonical)

    @property
    def is_loaded(self) -> bool:
        """Whether the cache has ever successfully loaded data."""
        return self._last_fetch_ok

    def invalidate(self) -> None:
        """Force the cache to re-fetch on next lookup."""
        with self._lock:
            self._expires_at = 0.0
            self._data = {}
            self._canonical = {}


_cache: ModelsDevCostCache | None = None
_cache_lock = threading.Lock()


def get_cache() -> ModelsDevCostCache:
    """Get or create the global cache singleton."""
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = ModelsDevCostCache()
    return _cache


def lookup_dynamic(model_id: str) -> tuple[float, float] | None:
    """Look up cost for a model from models.dev. Returns None if not found."""
    return get_cache().lookup(model_id)


def all_dynamic_rates() -> dict[str, tuple[float, float]]:
    """Return all cached canonical rates from models.dev."""
    return get_cache().all_rates()
