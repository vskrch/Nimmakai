"""Health, catalog, and provider admin endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from nimmakai import __version__
from nimmakai.auth import require_proxy_auth
from nimmakai.catalog.providers import provider_from_request_body
from nimmakai.config import get_settings

router = APIRouter(tags=["admin"])


@router.get("/admin/storage")
async def storage_info(request: Request) -> JSONResponse:
    """Where providers / prefs are persisted (sqlite path, counts)."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    hub = getattr(request.app.state, "hub", None)
    prefs = getattr(request.app.state, "preferences", None)
    db_path = Path(settings.sqlite_path)
    return JSONResponse(
        {
            "backend": "sqlite",
            "sqlite_path": str(db_path),
            "sqlite_exists": db_path.is_file(),
            "providers_count": len(hub.store.providers) if hub else 0,
            "preferences_count": len(prefs.preferences) if prefs else 0,
            "note": (
                "Providers and routing preferences are stored in SQLite. "
                "Free-provider templates are seeded without keys; add keys in the UI "
                "or via env vars (GROQ_API_KEYS, …). On ephemeral hosts, mount a volume "
                "on the sqlite path so keys survive restarts."
            ),
        }
    )


@router.post("/admin/heal")
async def admin_heal(request: Request) -> JSONResponse:
    """Force self-heal: restore provider runtimes + refresh catalog if empty."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    from nimmakai.resilience import heal_and_refresh

    hub = getattr(request.app.state, "hub", None)
    registry = getattr(request.app.state, "registry", None)
    report = await heal_and_refresh(
        hub=hub, registry=registry, settings=settings, force=True
    )
    return JSONResponse({"ok": True, "heal": report})


@router.get("/admin/logs")
async def admin_logs(request: Request) -> JSONResponse:
    """
    Recent request log ring (per dyno). Use for Cursor debugging.

    Query: ?limit=50&errors=1&path=/v1/chat
    """
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    from nimmakai.logging_setup import request_logs

    limit = 50
    try:
        limit = min(200, max(1, int(request.query_params.get("limit") or 50)))
    except ValueError:
        pass
    errors_only = request.query_params.get("errors") in {"1", "true", "yes"}
    path_prefix = request.query_params.get("path") or None
    return JSONResponse(
        {
            "count": limit,
            "errors_only": errors_only,
            "path_prefix": path_prefix,
            "entries": request_logs.list(
                limit=limit, path_prefix=path_prefix, errors_only=errors_only
            ),
            "hint": (
                "Heroku full logs: heroku logs -a your-nimmakai -t. "
                "Each chat line includes req=… routed=… intent=… stream=…"
            ),
        }
    )


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    pool = getattr(request.app.state, "pool", None)
    registry = getattr(request.app.state, "registry", None)
    hub = getattr(request.app.state, "hub", None)
    settings = getattr(request.app.state, "settings", None) or get_settings()
    keys = len(pool) if pool is not None else 0
    keys_available = pool.available_count() if pool is not None else 0
    total_keys = keys
    active_providers = 0
    catalog_ok = True
    live_models = 0
    if registry is not None:
        live_models = len(registry.live_ids)
        catalog_ok = registry.last_refresh_ok or not registry.live_ids
        if registry.last_refresh_at is None:
            catalog_ok = registry.catalog is not None
        if live_models == 0 and registry.last_refresh_at is not None:
            catalog_ok = False
    providers = []
    if hub is not None:
        total_keys = 0
        for p in hub.store.providers.values():
            kc = len(p.resolved_keys())
            total_keys += kc
            has_rt = hub.has_runtime(p.id)
            if has_rt:
                active_providers += 1
            providers.append(
                {
                    "id": p.id,
                    "enabled": p.enabled,
                    "key_count": kc,
                    "runtime": has_rt,
                }
            )
        # Sum available keys across active runtimes
        keys_available = sum(
            rt.pool.available_count()
            for rt in hub.runtimes.values()
            if rt.config.enabled
        )
    proxy_configured = bool(settings.proxy_api_keys) or settings.allow_insecure_auth
    status = "ok"
    # Degraded only when clearly unusable in production (no keys at all, or
    # a refresh already ran and still produced zero models).
    no_keys = total_keys == 0
    failed_catalog = (
        registry is not None
        and live_models == 0
        and registry.last_refresh_at is not None
        and not registry.last_refresh_ok
    )
    if no_keys or failed_catalog:
        status = "degraded"
    return JSONResponse(
        {
            "status": status,
            "version": __version__,
            "nim_keys_configured": keys,  # legacy field (default pool size)
            "keys_configured": total_keys,
            "keys_available": keys_available,
            "active_providers": active_providers,
            "live_models": live_models,
            "catalog_ok": catalog_ok,
            "proxy_auth_configured": proxy_configured,
            "providers": providers,
            "routing_enabled": getattr(settings, "routing_enabled", True),
            "dashboard": "/dashboard",
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
            "model_tokens": {
                k: {"prompt_tokens": v.prompt_tokens, "completion_tokens": v.completion_tokens}
                for k, v in routing_stats.model_tokens.items()
            },
            "key_tokens": {
                k: {"prompt_tokens": v.prompt_tokens, "completion_tokens": v.completion_tokens}
                for k, v in routing_stats.key_tokens.items()
            },
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
            "sticky": registry.rankings_sticky,
            "frozen": registry.ladder.frozen,
            "computed_at": registry.ladder.computed_at,
            "best_coding": registry.dynamic_chains.get("coding_agentic", [])[:12],
            "refresh": "POST /admin/catalog/refresh or POST /admin/rankings/refresh",
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
    """
    Full service cache refresh: re-fetch provider /v1/models and **recompute**
    sticky best-model rankings (this is the only path that rebuilds the cache
    by default — periodic background sync only updates live ids).
    """
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
        ok = await registry.refresh_from_hub(
            hub, fetch_docs=False, run_probes=False, recompute_rankings=True
        )
    elif upstream is not None:
        ok = await registry.refresh_from_upstream(upstream)
        if ok:
            registry.recompute_rankings(persist=True)
    else:
        ok = False
    return JSONResponse(
        {
            "ok": ok,
            "catalog": registry.snapshot(),
            "rankings": {
                "best_coding": registry.dynamic_chains.get("coding_agentic", [])[:12],
                "best_chat": registry.dynamic_chains.get("chat_fast", [])[:8],
                "frozen": registry.ladder.frozen,
                "computed_at": registry.ladder.computed_at,
            },
            "message": "Catalog + best-model ranking cache refreshed",
        }
    )


@router.post("/admin/rankings/refresh")
async def rankings_refresh(request: Request) -> JSONResponse:
    """Recompute best open models from current live_ids and persist cache."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return JSONResponse(
            {"error": {"message": "Catalog not loaded", "code": "nimmakai_catalog_empty"}},
            status_code=503,
        )
    best = registry.recompute_rankings(persist=True)
    return JSONResponse({"ok": True, "rankings": best})


@router.get("/admin/rankings")
async def rankings_view(request: Request) -> JSONResponse:
    """Inspect sticky precomputed best-model cache + live adaptive order."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return JSONResponse(
            {"error": {"message": "Catalog not loaded", "code": "nimmakai_catalog_empty"}},
            status_code=503,
        )
    sticky = list(registry.dynamic_chains.get("coding_agentic", [])[:15])
    adaptive = registry.health_reorder(sticky)
    return JSONResponse(
        {
            "sticky": registry.rankings_sticky,
            "frozen": registry.ladder.frozen,
            "adaptive_routing": getattr(settings, "adaptive_routing", True),
            "computed_at": registry.ladder.computed_at,
            "best_coding_sticky": sticky,
            "best_coding_adaptive": adaptive,
            "best_coding": adaptive,  # what requests actually try first
            "best_chat": registry.health_reorder(
                list(registry.dynamic_chains.get("chat_fast", [])[:10])
            ),
            "best_reasoning": registry.dynamic_chains.get("reasoning", [])[:10],
            "responsive": {
                m: round(registry.health.responsive_score(m), 3) for m in adaptive[:8]
            },
            "ladders": registry.ladder.snapshot(),
            "hint": (
                "Sticky quality cache is precomputed at startup. "
                "Each request auto-adapts: currently responding models jump to the front. "
                "POST /admin/catalog/refresh to recompute the quality cache."
            ),
        }
    )


@router.get("/admin/providers")
async def list_providers(request: Request) -> JSONResponse:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    hub = getattr(request.app.state, "hub", None)
    if hub is None:
        return JSONResponse({"providers": [], "presets": [], "pool": {}})
    from nimmakai.catalog.presets import get_preset, list_presets

    registry = getattr(request.app.state, "registry", None)
    live_ids = list(registry.live_ids) if registry is not None else []
    counts: dict[str, int] = {}
    for mid in live_ids:
        pid = mid.split("/", 1)[0] if "/" in mid else "unknown"
        counts[pid] = counts.get(pid, 0) + 1

    providers = hub.store.list_masked()
    # Annotate with runtime status + live model counts for the admin UI
    for p in providers:
        p["runtime"] = hub.has_runtime(p["id"])
        rt = hub.runtimes.get(p["id"])
        p["available_keys"] = rt.pool.available_count() if rt else 0
        p["model_count"] = counts.get(p["id"], 0)
        preset = get_preset(p["id"])
        if preset:
            p["free_tier"] = bool(preset.get("free_tier"))
            p["speed_tier"] = preset.get("speed_tier")
            p["signup_url"] = preset.get("signup_url") or ""
    return JSONResponse(
        {
            "providers": providers,
            "presets": list_presets(),
            "pool": {
                "live_models": len(live_ids),
                "active_providers": sum(1 for p in providers if p.get("runtime")),
                "models_by_provider": counts,
            },
            "pool_note": (
                "All enabled providers with keys are merged into one model pool. "
                "Routing scores quality × affinity × health × free-provider speed."
            ),
        }
    )


@router.get("/admin/providers/presets")
async def provider_presets(request: Request) -> JSONResponse:
    """Free / popular OpenAI-compatible endpoint templates for the admin UI."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    from nimmakai.catalog.presets import list_presets

    hub = getattr(request.app.state, "hub", None)
    configured = set(hub.store.providers.keys()) if hub else set()
    presets = list_presets()
    for p in presets:
        p["already_configured"] = p["id"] in configured and p["id"] != "custom"
    return JSONResponse({"presets": presets})


@router.post("/admin/providers/test")
async def test_provider(request: Request) -> JSONResponse:
    """
    Probe an OpenAI-compatible base URL + key without saving.
    Body: {base_url, api_keys: [str], ...} or {id} to test an existing provider.
    """
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_proxy_auth(request, settings)
    body: dict[str, Any] = await request.json()
    hub = getattr(request.app.state, "hub", None)

    base_url = str(body.get("base_url") or "").strip().rstrip("/")
    keys = body.get("api_keys") or []
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.split(",") if k.strip()]
    keys = [str(k).strip() for k in keys if str(k).strip()]

    # Test existing provider by id (runtime or stored config)
    pid = str(body.get("id") or "").strip().lower()
    if pid and hub is not None:
        if pid in hub.runtimes:
            rt = hub.runtimes[pid]
            try:
                status, resp, _h, _k = await rt.upstream.request_json("GET", "/models")
                n = 0
                sample: list[str] = []
                if isinstance(resp, dict) and isinstance(resp.get("data"), list):
                    n = len(resp["data"])
                    for item in resp["data"][:8]:
                        if isinstance(item, dict) and item.get("id"):
                            sample.append(str(item["id"]))
                ok = status < 400
                return JSONResponse(
                    {
                        "ok": ok,
                        "status_code": status,
                        "model_count": n,
                        "sample_models": sample,
                        "message": (
                            f"OK — {n} models from {pid}"
                            if ok
                            else f"HTTP {status} from {pid}"
                        ),
                    },
                    status_code=200 if ok else 502,
                )
            except Exception as exc:
                return JSONResponse(
                    {"ok": False, "message": f"Connection failed: {exc}"},
                    status_code=502,
                )
        cfg = hub.store.providers.get(pid)
        if cfg is not None:
            base_url = base_url or cfg.base_url
            if not keys:
                keys = cfg.resolved_keys()

    if not base_url:
        return JSONResponse(
            {"error": {"message": "base_url is required", "code": "invalid_request"}},
            status_code=400,
        )
    if not keys:
        return JSONResponse(
            {
                "error": {
                    "message": "At least one API key is required to test",
                    "code": "invalid_request",
                }
            },
            status_code=400,
        )

    from nimmakai.balancer import KeyPool
    from nimmakai.upstream import UpstreamClient

    pool = KeyPool(
        api_keys=keys,
        rpm_limit=60,
        rpd_limit=10000,
        max_in_flight_per_key=1,
    )
    client = UpstreamClient(
        base_url=base_url,
        pool=pool,
        timeout=min(30.0, settings.upstream_timeout),
        user_agent=settings.upstream_user_agent,
    )
    try:
        await client.start()
        status, resp, _h, _k = await client.request_json("GET", "/models")
        n = 0
        sample: list[str] = []
        if isinstance(resp, dict) and isinstance(resp.get("data"), list):
            n = len(resp["data"])
            for item in resp["data"][:8]:
                if isinstance(item, dict) and item.get("id"):
                    sample.append(str(item["id"]))
        ok = status < 400
        return JSONResponse(
            {
                "ok": ok,
                "status_code": status,
                "model_count": n,
                "sample_models": sample,
                "message": (
                    f"OK — {n} models reachable"
                    if ok
                    else f"Upstream returned HTTP {status}"
                ),
            },
            status_code=200 if ok else 502,
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "message": f"Connection failed: {exc}"},
            status_code=502,
        )
    finally:
        await client.stop()


@router.post("/admin/providers")
async def upsert_provider(request: Request) -> JSONResponse:
    """
    Register or update an OpenAI-compatible provider.
    Body: {id, base_url, api_keys?, api_keys_env?, rpm_limit?, enabled?, name?}

    Models from every enabled provider with keys are namespaced (provider/model)
    and merged into the global routing pool.
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

    # Expand from free-provider preset when requested
    preset_id = str(body.get("preset") or body.get("from_preset") or "").strip().lower()
    if preset_id and preset_id != "custom":
        from nimmakai.catalog.presets import get_preset

        preset = get_preset(preset_id)
        if preset and not preset.get("custom"):
            body.setdefault("id", preset["id"])
            body.setdefault("name", preset["name"])
            if not body.get("base_url"):
                body["base_url"] = preset.get("base_url")
            body.setdefault("rpm_limit", preset.get("rpm_limit", 40))
            body.setdefault("rpd_limit", preset.get("rpd_limit", 2000))
            body.setdefault(
                "max_in_flight_per_key", preset.get("max_in_flight_per_key", 3)
            )
            if preset.get("api_keys_env") and not body.get("api_keys_env"):
                body["api_keys_env"] = preset["api_keys_env"]

    provider_id = body.get("id")
    if not provider_id:
        return JSONResponse(
            {"error": {"message": "Provider ID is required", "code": "invalid_request"}},
            status_code=400,
        )
    provider_id = str(provider_id).strip().lower()
    # Sanitize id for namespacing (alphanumeric + hyphen/underscore)
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider_id):
        return JSONResponse(
            {
                "error": {
                    "message": (
                        "Provider ID must be lowercase alphanumeric "
                        "(start with a letter), max 64 chars"
                    ),
                    "code": "invalid_request",
                }
            },
            status_code=400,
        )

    existing = hub.store.providers.get(provider_id)
    if existing:
        merged_body = {
            "id": existing.id,
            "name": existing.name,
            "base_url": existing.base_url,
            "api_keys": existing.api_keys,
            "api_keys_env": existing.api_keys_env,
            "enabled": existing.enabled,
            "rpm_limit": existing.rpm_limit,
            "rpd_limit": existing.rpd_limit,
            "max_in_flight_per_key": existing.max_in_flight_per_key,
            "api_style": existing.api_style,
            "builtin": existing.builtin,
        }
        for k, v in body.items():
            if v is None or v == "_":
                continue
            # Empty api_keys list on partial update keeps existing keys
            if k == "api_keys" and isinstance(v, list) and len(v) == 0:
                continue
            if k == "api_keys" and isinstance(v, str) and not v.strip():
                continue
            merged_body[k] = v
        body = merged_body

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
    if not cfg.base_url:
        return JSONResponse(
            {
                "error": {
                    "message": "base_url is required (OpenAI-compatible …/v1 root)",
                    "code": "invalid_request",
                }
            },
            status_code=400,
        )
    if not cfg.resolved_keys():
        return JSONResponse(
            {
                "error": {
                    "message": (
                        "At least one API key is required (api_keys or api_keys_env)"
                    ),
                    "code": "invalid_request",
                }
            },
            status_code=400,
        )

    masked = await hub.upsert_provider(cfg)
    live_added = 0
    if registry is not None:
        registry.ladder.provider_ids = set(hub.provider_ids)
        # New provider models must re-enter sticky ranking cache
        ok = await registry.refresh_from_hub(
            hub, fetch_docs=False, run_probes=False, recompute_rankings=True
        )
        live_added = len(registry.live_ids)
    else:
        ok = True
    return JSONResponse(
        {
            "ok": True,
            "provider": masked,
            "catalog_ok": ok,
            "live_model_count": live_added,
            "message": (
                f"Provider '{cfg.id}' saved — "
                f"{live_added} models in the unified pool"
            ),
        }
    )


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
        await registry.refresh_from_hub(
            hub, fetch_docs=False, run_probes=False, recompute_rankings=True
        )
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
    ok = await registry.refresh_from_hub(
        hub, fetch_docs=False, run_probes=False, recompute_rankings=True
    )
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
    chain = body.get("chain") if isinstance(body.get("chain"), list) else None
    if not intent or chain is None:
        return JSONResponse(
            {
                "error": {
                    "message": "intent and chain (list) are required",
                    "code": "invalid_request",
                }
            },
            status_code=400,
        )
    # Empty chain clears the preference (revert to intelligent routing)
    if len(chain) == 0:
        prefs.clear(intent)
        return JSONResponse({"ok": True, "preference": None, "cleared": True})
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
