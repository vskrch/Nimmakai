"""Health, catalog, and provider admin endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from nimmakai import __version__
from nimmakai.auth import require_proxy_auth
from nimmakai.catalog.providers import provider_from_request_body
from nimmakai.config import get_settings

router = APIRouter(tags=["admin"])


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    pool = getattr(request.app.state, "pool", None)
    registry = getattr(request.app.state, "registry", None)
    hub = getattr(request.app.state, "hub", None)
    keys = len(pool) if pool is not None else 0
    keys_available = pool.available_count() if pool is not None else 0
    catalog_ok = True
    if registry is not None:
        catalog_ok = registry.last_refresh_ok or not registry.live_ids
        if registry.last_refresh_at is None:
            catalog_ok = registry.catalog is not None
    providers = []
    if hub is not None:
        providers = [
            {"id": p.id, "enabled": p.enabled, "key_count": len(p.resolved_keys())}
            for p in hub.store.enabled_providers()
        ]
    return JSONResponse(
        {
            "status": "ok",
            "version": __version__,
            "nim_keys_configured": keys,
            "keys_available": keys_available,
            "catalog_ok": catalog_ok,
            "providers": providers,
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
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    pool = request.app.state.pool
    routing_stats = getattr(request.app.state, "routing_stats", None)
    registry = getattr(request.app.state, "registry", None)
    hub = getattr(request.app.state, "hub", None)
    payload: dict = {
        "version": __version__,
        "keys": pool.snapshot(),
    }
    if hub is not None:
        payload["providers"] = {
            pid: {
                "base_url": rt.config.base_url,
                "enabled": rt.config.enabled,
                "keys": rt.pool.snapshot(),
            }
            for pid, rt in hub.runtimes.items()
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
    hub = getattr(request.app.state, "hub", None)
    upstream = getattr(request.app.state, "upstream", None)
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
    if hub is not None:
        ok = await registry.refresh_from_hub(hub)
    elif upstream is not None:
        ok = await registry.refresh_from_upstream(upstream)
    else:
        ok = False
    return JSONResponse({"ok": ok, "catalog": registry.snapshot()})


@router.get("/admin/providers")
async def list_providers(request: Request) -> JSONResponse:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    hub = getattr(request.app.state, "hub", None)
    if hub is None:
        return JSONResponse({"providers": []})
    return JSONResponse({"providers": hub.store.list_masked()})


@router.post("/admin/providers")
async def upsert_provider(request: Request) -> JSONResponse:
    """
    Register or update an OpenAI-compatible provider.
    Body: {id, base_url, api_keys?, api_keys_env?, rpm_limit?, enabled?, name?}
    """
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    hub = getattr(request.app.state, "hub", None)
    registry = getattr(request.app.state, "registry", None)
    if hub is None:
        return JSONResponse(
            {"error": {"message": "Provider hub not ready", "code": "nimmakai_no_hub"}},
            status_code=503,
        )
    body: dict[str, Any] = await request.json()
    try:
        cfg = provider_from_request_body(body)
    except ValueError as exc:
        return JSONResponse(
            {"error": {"message": str(exc), "code": "invalid_request"}},
            status_code=400,
        )
    if cfg.api_style != "openai":
        return JSONResponse(
            {
                "error": {
                    "message": "Only api_style=openai is supported in this version",
                    "code": "unsupported_api_style",
                }
            },
            status_code=400,
        )
    masked = await hub.upsert_provider(cfg)
    if registry is not None:
        registry.ladder.provider_ids = set(hub.provider_ids)
        await registry.refresh_from_hub(hub, fetch_docs=False, run_probes=False)
    return JSONResponse({"ok": True, "provider": masked})


@router.delete("/admin/providers/{provider_id}")
async def delete_provider(provider_id: str, request: Request) -> JSONResponse:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    hub = getattr(request.app.state, "hub", None)
    registry = getattr(request.app.state, "registry", None)
    if hub is None:
        return JSONResponse(
            {"error": {"message": "Provider hub not ready", "code": "nimmakai_no_hub"}},
            status_code=503,
        )
    if provider_id.lower() == "nim":
        # Disable builtin rather than delete
        ok = await hub.remove_provider("nim")
    else:
        ok = await hub.remove_provider(provider_id)
    if not ok:
        return JSONResponse(
            {"error": {"message": "Provider not found", "code": "not_found"}},
            status_code=404,
        )
    if registry is not None:
        registry.ladder.provider_ids = set(hub.provider_ids)
        await registry.refresh_from_hub(hub, fetch_docs=False, run_probes=False)
    return JSONResponse({"ok": True, "providers": hub.store.list_masked()})


@router.post("/admin/providers/{provider_id}/refresh")
async def refresh_provider(provider_id: str, request: Request) -> JSONResponse:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    hub = getattr(request.app.state, "hub", None)
    registry = getattr(request.app.state, "registry", None)
    if hub is None or registry is None:
        return JSONResponse(
            {"error": {"message": "Not ready", "code": "nimmakai_not_ready"}},
            status_code=503,
        )
    if provider_id.lower() not in hub.runtimes:
        return JSONResponse(
            {"error": {"message": "Provider not found or disabled", "code": "not_found"}},
            status_code=404,
        )
    ok = await registry.refresh_from_hub(hub, fetch_docs=False, run_probes=False)
    return JSONResponse({"ok": ok, "catalog": registry.snapshot()})


# ── User Preferences (per-intent model overrides) ────────────────────


@router.get("/preferences")
async def list_preferences(request: Request) -> JSONResponse:
    """List all user intent preferences."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    prefs = getattr(request.app.state, "preferences", None)
    if prefs is None:
        return JSONResponse({"preferences": []})
    return JSONResponse({"preferences": prefs.list_all()})


@router.post("/preferences")
async def set_preference(request: Request) -> JSONResponse:
    """
    Set or update an intent preference.
    Body: {intent, chain[], strict?, note?}
    """
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    prefs = getattr(request.app.state, "preferences", None)
    if prefs is None:
        return JSONResponse(
            {"error": {"message": "Preferences not ready", "code": "nimmakai_no_prefs"}},
            status_code=503,
        )
    body: dict[str, Any] = await request.json()
    intent = str(body.get("intent") or "")
    chain = body.get("chain") or []
    if not intent or not isinstance(chain, list) or len(chain) == 0:
        return JSONResponse(
            {
                "error": {
                    "message": "intent and non-empty chain are required",
                    "code": "invalid_request",
                }
            },
            status_code=400,
        )
    try:
        pref = prefs.set(
            intent,
            chain,
            strict=bool(body.get("strict", False)),
            note=str(body.get("note") or ""),
        )
    except ValueError as exc:
        return JSONResponse(
            {"error": {"message": str(exc), "code": "invalid_intent"}},
            status_code=400,
        )
    return JSONResponse({"ok": True, "preference": pref.to_dict()})


@router.delete("/preferences/{intent}")
async def delete_preference(intent: str, request: Request) -> JSONResponse:
    """Remove a user intent preference (reverts to intelligent routing)."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    prefs = getattr(request.app.state, "preferences", None)
    if prefs is None:
        return JSONResponse(
            {"error": {"message": "Preferences not ready", "code": "nimmakai_no_prefs"}},
            status_code=503,
        )
    ok = prefs.clear(intent)
    if not ok:
        return JSONResponse(
            {"error": {"message": "Preference not found", "code": "not_found"}},
            status_code=404,
        )
    return JSONResponse({"ok": True, "preferences": prefs.list_all()})


@router.delete("/preferences")
async def clear_all_preferences(request: Request) -> JSONResponse:
    """Remove all user intent preferences (reverts everything to intelligent routing)."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    prefs = getattr(request.app.state, "preferences", None)
    if prefs is None:
        return JSONResponse(
            {"error": {"message": "Preferences not ready", "code": "nimmakai_no_prefs"}},
            status_code=503,
        )
    prefs.clear_all()
    return JSONResponse({"ok": True, "preferences": []})
