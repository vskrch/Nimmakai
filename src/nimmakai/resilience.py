"""Self-healing helpers for production reliability."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_ALERT_LOG_NAME = "nimmakai.alerts"


def _alert(event: str, **kw: Any) -> None:
    """Emit a structured alert event for downstream aggregation / webhooks."""
    import json as _json

    payload = {"event": event, **kw}
    logger.warning("ALERT %s", _json.dumps(payload, default=str))


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
        "restored_providers": 0,
    }
    if registry is not None and hasattr(registry, "health"):
        report["healed_models"] = registry.health.expire_stale_cooldowns()

    restored_providers: list[str] = []
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
                restored_providers.append(cfg.id)
            except Exception:
                logger.exception("self-heal: failed to restore provider %s", cfg.id)
                _alert(
                    "nimmakai.provider_restore_failed",
                    provider=cfg.id,
                )
        report["active_providers"] = len(hub.active_provider_ids())
        report["restored_providers"] = len(restored_providers)

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

    # NMK-402: If providers were restored, recompute rankings so their models
    # immediately participate in routing.
    if restored_providers and registry is not None and registry.live_ids:
        try:
            registry.recompute_rankings(persist=True)
            logger.info(
                "self-heal: recomputed rankings after restoring %s provider(s)",
                len(restored_providers),
            )
        except Exception:
            logger.exception("self-heal: ranking recompute failed")

    return report


def emergency_coding_chain(registry: Any, *, max_n: int = 10) -> list[str]:
    """
    Last-resort chain when ladder is empty: score live models for coding_agentic.

    Does NOT rebuild the frozen ladder (too expensive on request path).
    Returns active models sorted by ladder scoring or alphabetically as fallback.
    """
    if registry is None or not getattr(registry, "live_ids", None):
        return []
    active = sorted(
        registry.active_live_ids()
        if hasattr(registry, "active_live_ids")
        else set(registry.live_ids)
    )
    try:
        chain = registry.ladder.ladder_for("coding_agentic", max_n=max_n)
        if chain:
            return registry.health_reorder(chain)
        # Cold ladder: return active models (fallback executor handles ranking)
        if not active:
            _alert(
                "nimmakai.all_chains_empty",
                intent="coding_agentic",
                live_models=0,
                cause="no_live_models",
            )
        return active[:max_n]
    except Exception:
        logger.exception("emergency_coding_chain failed")
        _alert(
            "nimmakai.all_chains_empty",
            intent="coding_agentic",
            live_models=len(active),
            cause="emergency_chain_failed",
        )
        return active[:max_n]
