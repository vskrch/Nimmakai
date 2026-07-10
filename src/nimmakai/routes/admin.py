"""Health and pool observability endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from nimmakai import __version__
from nimmakai.auth import require_proxy_auth
from nimmakai.config import get_settings

router = APIRouter(tags=["admin"])


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    pool = getattr(request.app.state, "pool", None)
    registry = getattr(request.app.state, "registry", None)
    keys = len(pool) if pool is not None else 0
    keys_available = pool.available_count() if pool is not None else 0
    catalog_ok = True
    if registry is not None:
        catalog_ok = registry.last_refresh_ok or not registry.live_ids
        if registry.last_refresh_at is None:
            catalog_ok = registry.catalog is not None
    return JSONResponse(
        {
            "status": "ok",
            "version": __version__,
            "nim_keys_configured": keys,
            "keys_available": keys_available,
            "catalog_ok": catalog_ok,
            "routing_enabled": getattr(
                getattr(request.app.state, "settings", None),
                "routing_enabled",
                True,
            ),
        }
    )


@router.get("/stats")
async def stats(request: Request) -> JSONResponse:
    """Per-key RPM / latency / cooldown snapshot (no secret values)."""
    pool = request.app.state.pool
    routing_stats = getattr(request.app.state, "routing_stats", None)
    registry = getattr(request.app.state, "registry", None)
    payload: dict = {
        "version": __version__,
        "keys": pool.snapshot(),
    }
    if routing_stats is not None:
        payload["routing"] = {
            "intents_total": dict(routing_stats.intents_total),
            "models_total": dict(routing_stats.models_total),
            "fallback_advances": routing_stats.fallback_advances,
        }
    if registry is not None:
        snap = registry.snapshot()
        payload["catalog"] = {
            "yaml_version": snap["yaml_version"],
            "live_model_count": snap["live_model_count"],
            "last_refresh_age_s": snap["last_refresh_age_s"],
            "last_refresh_ok": snap["last_refresh_ok"],
            "ladders": snap.get("ladders"),
        }
    return JSONResponse(payload)


@router.get("/ladder")
async def ladder_view(request: Request) -> JSONResponse:
    """Current intelligent strength ladders per intent (no secrets)."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return JSONResponse(
            {
                "error": {
                    "message": "Catalog not loaded",
                    "code": "nimmakai_catalog_empty",
                }
            },
            status_code=503,
        )
    return JSONResponse(
        {
            "ladders": registry.ladder.snapshot(),
            "live_model_count": len(registry.live_ids),
            "policy": "strongest_available_first_then_ladder",
        }
    )


@router.get("/catalog")
async def catalog_view(request: Request) -> JSONResponse:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return JSONResponse(
            {
                "error": {
                    "message": "Catalog not loaded",
                    "code": "nimmakai_catalog_empty",
                }
            },
            status_code=503,
        )
    return JSONResponse(registry.snapshot())


@router.post("/admin/catalog/refresh")
async def catalog_refresh(request: Request) -> JSONResponse:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    registry = getattr(request.app.state, "registry", None)
    upstream = getattr(request.app.state, "upstream", None)
    if registry is None or upstream is None:
        return JSONResponse(
            {
                "error": {
                    "message": "Catalog not loaded",
                    "code": "nimmakai_catalog_empty",
                }
            },
            status_code=503,
        )
    ok = await registry.refresh_from_upstream(upstream)
    return JSONResponse({"ok": ok, "catalog": registry.snapshot()})
