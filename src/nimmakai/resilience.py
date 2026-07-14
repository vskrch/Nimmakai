"""Self-healing helpers for production reliability."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


async def heal_and_refresh(
    *,
    hub: Any,
    registry: Any,
    settings: Any,
    force: bool = False,
) -> dict[str, Any]:
    """
    Fail-safe recovery pass:
    - clear stale model cooldowns that have expired
    - re-enable runtimes that regained keys
    - refresh catalog if empty or force
    """
    report: dict[str, Any] = {
        "healed_models": 0,
        "refreshed": False,
        "live_models": 0,
        "active_providers": 0,
    }
    if registry is not None and hasattr(registry, "health"):
        report["healed_models"] = registry.health.expire_stale_cooldowns()

    if hub is not None:
        # Re-ensure runtimes for enabled providers that dropped out
        for cfg in list(hub.store.providers.values()):
            if not cfg.enabled:
                continue
            if hub.has_runtime(cfg.id):
                continue
            if not cfg.resolved_keys():
                continue
            try:
                await hub._ensure_runtime(cfg)
                logger.info("self-heal: restored runtime for provider %s", cfg.id)
            except Exception:
                logger.exception("self-heal: failed to restore provider %s", cfg.id)
        report["active_providers"] = len(hub.active_provider_ids())

    need_refresh = force
    if registry is not None:
        if not registry.live_ids:
            need_refresh = True
        report["live_models"] = len(registry.live_ids)

    if need_refresh and registry is not None and hub is not None:
        try:
            ok = await registry.refresh_from_hub(
                hub, fetch_docs=False, run_probes=False
            )
            report["refreshed"] = bool(ok)
            report["live_models"] = len(registry.live_ids)
            logger.info(
                "self-heal catalog refresh ok=%s live=%s",
                ok,
                report["live_models"],
            )
        except Exception:
            logger.exception("self-heal catalog refresh failed")

    return report


def emergency_coding_chain(registry: Any, *, max_n: int = 10) -> list[str]:
    """
    Last-resort chain when ladder is empty: score live models for coding_agentic.
    """
    if registry is None or not getattr(registry, "live_ids", None):
        return []
    try:
        chain = registry.ladder.ladder_for("coding_agentic", max_n=max_n)
        if chain:
            return registry.health_reorder(chain)
        # Cold ladder: rebuild then retry
        registry.ladder.rebuild(set(registry.live_ids))
        chain = registry.ladder.ladder_for("coding_agentic", max_n=max_n)
        return registry.health_reorder(chain) if chain else sorted(registry.live_ids)[:max_n]
    except Exception:
        logger.exception("emergency_coding_chain failed")
        return sorted(registry.live_ids)[:max_n]
