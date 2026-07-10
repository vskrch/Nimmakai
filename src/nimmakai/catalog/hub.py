"""Per-provider upstream clients + key pools."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from nimmakai.balancer import KeyPool
from nimmakai.catalog.providers import (
    ProviderConfig,
    ProviderStore,
    namespace_model,
    split_provider_model,
)
from nimmakai.config import Settings
from nimmakai.upstream import UpstreamClient

logger = logging.getLogger(__name__)


@dataclass
class ProviderRuntime:
    config: ProviderConfig
    pool: KeyPool
    upstream: UpstreamClient


class ProviderHub:
    """
    OpenRouter-style hub: many OpenAI-compatible backends.
    `default` is the nim (or first enabled) upstream for legacy call sites.
    """

    def __init__(self, store: ProviderStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.runtimes: dict[str, ProviderRuntime] = {}

    @property
    def provider_ids(self) -> set[str]:
        return self.store.provider_ids()

    @property
    def default(self) -> UpstreamClient:
        if "nim" in self.runtimes and self.runtimes["nim"].config.enabled:
            return self.runtimes["nim"].upstream
        for rt in self.runtimes.values():
            if rt.config.enabled:
                return rt.upstream
        # Empty nim client may exist for fail-closed
        if "nim" in self.runtimes:
            return self.runtimes["nim"].upstream
        raise RuntimeError("No providers configured")

    @property
    def default_pool(self) -> KeyPool:
        if "nim" in self.runtimes:
            return self.runtimes["nim"].pool
        for rt in self.runtimes.values():
            return rt.pool
        raise RuntimeError("No providers configured")

    async def start(self) -> None:
        for cfg in self.store.providers.values():
            await self._ensure_runtime(cfg)

    async def stop(self) -> None:
        for rt in self.runtimes.values():
            await rt.upstream.stop()
        self.runtimes.clear()

    async def _ensure_runtime(self, cfg: ProviderConfig) -> ProviderRuntime | None:
        pid = cfg.id.lower()
        keys = cfg.resolved_keys()
        if pid in self.runtimes:
            # recreate if base_url changed — simple: always rebuild on upsert
            await self.runtimes[pid].upstream.stop()
            del self.runtimes[pid]

        if not cfg.enabled:
            return None
        if cfg.api_style != "openai":
            logger.warning(
                "provider %s api_style=%s skipped (phase 1 openai only)",
                pid,
                cfg.api_style,
            )
            return None

        pool = KeyPool(
            api_keys=keys,  # may be empty → fail-closed
            rpm_limit=max(1.0, cfg.rpm_limit * self.settings.nim_rpm_safety_factor),
            cooldown_seconds=self.settings.nim_cooldown_seconds,
            rpd_limit=cfg.rpd_limit,
            max_in_flight_per_key=cfg.max_in_flight_per_key,
            auth_fail_threshold=self.settings.auth_fail_threshold,
            auth_quarantine_seconds=self.settings.auth_quarantine_seconds,
            sticky_boost=self.settings.sticky_boost,
        )
        upstream = UpstreamClient(
            base_url=cfg.base_url or self.settings.nim_base_url,
            pool=pool,
            timeout=self.settings.upstream_timeout,
            user_agent=self.settings.upstream_user_agent,
            proxy_url=self.settings.egress_proxy_url(),
            retry_backoff_base=self.settings.retry_backoff_base_seconds,
            retry_backoff_cap=self.settings.retry_backoff_cap_seconds,
        )
        await upstream.start()
        rt = ProviderRuntime(config=cfg, pool=pool, upstream=upstream)
        self.runtimes[pid] = rt
        logger.info(
            "provider %s ready — base=%s keys=%s",
            pid,
            cfg.base_url,
            len(keys),
        )
        return rt

    async def upsert_provider(self, cfg: ProviderConfig) -> dict[str, Any]:
        self.store.upsert(cfg)
        await self._ensure_runtime(self.store.providers[cfg.id.lower()])
        return self.store.providers[cfg.id.lower()].mask()

    async def remove_provider(self, provider_id: str) -> bool:
        ok = self.store.remove(provider_id)
        pid = provider_id.lower()
        if pid in self.runtimes:
            cfg = self.store.providers.get(pid)
            if cfg is None or not cfg.enabled:
                await self.runtimes[pid].upstream.stop()
                del self.runtimes[pid]
            else:
                await self._ensure_runtime(cfg)
        return ok

    def client_for_model(self, model_id: str) -> tuple[UpstreamClient, str, str]:
        """
        Returns (upstream, provider_id, upstream_model_id).
        """
        pid, upstream_mid = split_provider_model(
            model_id, self.provider_ids, default_provider="nim"
        )
        rt = self.runtimes.get(pid)
        if rt is None or not rt.config.enabled:
            # fall back to default provider client
            return self.default, pid, upstream_mid
        return rt.upstream, pid, upstream_mid

    def namespace(self, provider_id: str, upstream_model_id: str) -> str:
        return namespace_model(provider_id, upstream_model_id)
