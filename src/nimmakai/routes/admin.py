"""Health, catalog, and provider admin endpoints."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from nimmakai import __version__
from nimmakai.auth import require_admin, require_proxy_auth
from nimmakai.catalog.providers import provider_from_request_body
from nimmakai.config import get_settings

router = APIRouter(tags=["admin"])


async def _safe_json(request: Request) -> dict[str, Any] | JSONResponse:
    """Parse JSON body, returning 400 OpenAI envelope on malformed input."""
    try:
        return await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"message": "Invalid JSON body", "code": "invalid_json"}},
            status_code=400,
        )


@router.get("/admin/storage")
async def storage_info(request: Request) -> JSONResponse:
    """Where providers / prefs are persisted (sqlite path, counts)."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_admin(request, settings)
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
    require_admin(request, settings)
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
    require_admin(request, settings)
    from nimmakai.logging_setup import request_logs

    limit = 50
    with suppress(ValueError):
        limit = min(500, max(1, int(request.query_params.get("limit") or 50)))
    errors_only = request.query_params.get("errors") in {"1", "true", "yes"}
    path_prefix = request.query_params.get("path") or None
    st = request_logs.status()
    return JSONResponse(
        {
            "count": limit,
            "errors_only": errors_only,
            "path_prefix": path_prefix,
            "logging": st,
            "entries": request_logs.list(
                limit=limit, path_prefix=path_prefix, errors_only=errors_only
            ),
            "hint": (
                f"Durable file (last {st.get('max_entries')} requests): "
                f"{st.get('file_path') or 'not configured'}. "
                "Toggle via PUT /admin/request-logging."
            ),
        }
    )


@router.get("/admin/request-logging")
async def get_request_logging(request: Request) -> JSONResponse:
    """Status of durable request file logging."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_admin(request, settings)
    from nimmakai.logging_setup import request_logs

    return JSONResponse(request_logs.status())


@router.put("/admin/request-logging")
async def put_request_logging(request: Request) -> JSONResponse:
    """Enable/disable writing requests to request_logs.txt beside the DB."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_admin(request, settings)
    from nimmakai.logging_setup import request_logs

    try:
        body = await request.json()
    except Exception:
        body = {}
    if "enabled" not in body:
        return JSONResponse(
            {"error": {"message": "enabled required", "code": "invalid_request"}},
            status_code=400,
        )
    request_logs.set_enabled(bool(body.get("enabled")))
    return JSONResponse({"ok": True, **request_logs.status()})


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


@router.get("/ready")
async def readiness(request: Request) -> JSONResponse:
    """Strict readiness: auth, a provider runtime, and live models must exist."""
    health_response = await health(request)
    payload = json.loads(health_response.body)
    failures: list[str] = []
    if not payload.get("proxy_auth_configured"):
        failures.append("proxy_auth_not_configured")
    if int(payload.get("active_providers") or 0) < 1:
        failures.append("no_active_providers")
    if int(payload.get("live_models") or 0) < 1:
        failures.append("no_live_models")
    if not payload.get("catalog_ok"):
        failures.append("catalog_unavailable")
    payload["ready"] = not failures
    payload["readiness_failures"] = failures
    return JSONResponse(payload, status_code=200 if not failures else 503)


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
    require_admin(request, settings)
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
    require_admin(request, settings)
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
    require_admin(request, settings)
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
    require_admin(request, settings)
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
    require_admin(request, settings)
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return JSONResponse(
            {"error": {"message": "Catalog not loaded", "code": "nimmakai_catalog_empty"}},
            status_code=503,
        )
    sticky = list(registry.dynamic_chains.get("coding_agentic", [])[:15])
    adaptive = registry.health_reorder(sticky, intent="coding_agentic")
    from nimmakai.routing.optimizer import explain_top

    return JSONResponse(
        {
            "algorithm": "score = I^0.50 × S^0.38 × H^0.08 × P^0.04 (every request)",
            "sticky": registry.rankings_sticky,
            "frozen": registry.ladder.frozen,
            "adaptive_routing": getattr(settings, "adaptive_routing", True),
            "computed_at": registry.ladder.computed_at,
            "best_coding_sticky": sticky,
            "best_coding_live": adaptive,
            "best_coding": adaptive,  # what requests actually try first
            "score_breakdown": explain_top(
                sticky, registry, intent="coding_agentic", n=8
            ),
            "best_chat": registry.health_reorder(
                list(registry.dynamic_chains.get("chat_fast", [])[:10]),
                intent="chat_fast",
            ),
            "best_reasoning": registry.dynamic_chains.get("reasoning", [])[:10],
            "responsive": {
                m: round(registry.health.responsive_score(m), 3) for m in adaptive[:8]
            },
            "ladders": registry.ladder.snapshot(),
            "hint": (
                "Every request ranks intelligence × live speed × health. "
                "Sticky cache is the intelligence prior; speed adapts continuously. "
                "POST /admin/catalog/refresh to refresh the intelligence prior."
            ),
        }
    )


@router.get("/admin/providers")
async def list_providers(request: Request) -> JSONResponse:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_admin(request, settings)
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
    configured = {p["id"] for p in providers if p.get("enabled") and (p.get("key_count") or 0) > 0}
    presets = list_presets()
    for preset in presets:
        preset["already_configured"] = (
            preset["id"] in configured and preset["id"] != "custom"
        )
    return JSONResponse(
        {
            "providers": providers,
            "presets": presets,
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
    require_admin(request, settings)
    from nimmakai.catalog.presets import list_presets

    hub = getattr(request.app.state, "hub", None)
    configured: set[str] = set()
    if hub is not None:
        for p in hub.store.list_masked():
            if p.get("enabled") and (p.get("key_count") or 0) > 0:
                configured.add(p["id"])
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
    require_admin(request, settings)
    body_or_err = await _safe_json(request)
    if isinstance(body_or_err, JSONResponse):
        return body_or_err
    body: dict[str, Any] = body_or_err
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
    require_admin(request, settings)
    hub = getattr(request.app.state, "hub", None)
    registry = getattr(request.app.state, "registry", None)
    if hub is None:
        return JSONResponse(
            {"error": {"message": "Provider hub not ready", "code": "nimmakai_no_hub"}},
            status_code=503,
        )
    body_or_err = await _safe_json(request)
    if isinstance(body_or_err, JSONResponse):
        return body_or_err
    raw_body: dict[str, Any] = body_or_err
    body: dict[str, Any] = dict(raw_body)

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
            "model_whitelist": list(existing.model_whitelist),
            "model_blacklist": list(existing.model_blacklist),
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

    # Seeded free presets start disabled (templates). Adding keys via the
    # dashboard must enable them unless the client explicitly sets enabled=false.
    # Only auto-enable when this request actually supplies keys/preset — do not
    # re-enable a deliberately disabled provider on unrelated edits.
    keys_in_req = raw_body.get("api_keys")
    has_new_keys = (
        (isinstance(keys_in_req, list) and any(str(k).strip() for k in keys_in_req))
        or (isinstance(keys_in_req, str) and bool(keys_in_req.strip()))
        or bool(str(raw_body.get("preset") or raw_body.get("from_preset") or "").strip())
    )
    if (
        has_new_keys
        and cfg.resolved_keys()
        and raw_body.get("enabled") is not False
    ):
        cfg.enabled = True

    # Register provider + immediately fetch its /models (NMK-101)
    if registry is not None:
        registry.ladder.provider_ids = set(hub.provider_ids)
    masked = await hub.upsert_provider(cfg, registry=registry)
    live_added = len(registry.live_ids) if registry else 0
    ok = bool(registry and registry.live_ids)
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


@router.post("/admin/models/register")
async def register_models(request: Request) -> JSONResponse:
    """
    Explicitly register models into the routing pool with quality overrides.

    Body: {provider_id, models[], quality_override?, supports_tools?, supports_vision?}
    """
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_admin(request, settings)
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return JSONResponse(
            {"error": {"message": "Catalog not ready", "code": "nimmakai_not_ready"}},
            status_code=503,
        )
    body_or_err = await _safe_json(request)
    if isinstance(body_or_err, JSONResponse):
        return body_or_err
    body: dict[str, Any] = body_or_err
    provider_id = str(body.get("provider_id") or "").strip().lower()
    models = body.get("models") if isinstance(body.get("models"), list) else []
    if not provider_id or not models:
        return JSONResponse(
            {
                "error": {
                    "message": "provider_id and models (list) required",
                    "code": "invalid_request",
                }
            },
            status_code=400,
        )

    from nimmakai.catalog.providers import namespace_model

    quality_override = body.get("quality_override")
    supports_tools = body.get("supports_tools")
    supports_vision = body.get("supports_vision")

    added: list[str] = []
    for mid in models:
        if not isinstance(mid, str) or not mid.strip():
            continue
        ns = namespace_model(provider_id, mid.strip())
        registry.live_ids.add(ns)
        added.append(ns)

        if quality_override is not None and isinstance(quality_override, (int, float)):
            registry.ladder.quality_overrides[ns] = float(quality_override)
        if supports_tools is not None:
            registry.ladder.set_capability(ns, supports_tools=bool(supports_tools))
        if supports_vision is not None:
            registry.ladder.set_capability(ns, supports_vision=bool(supports_vision))

    registry.ladder.provider_ids.add(provider_id)
    registry.recompute_rankings(persist=True)

    return JSONResponse({
        "ok": True,
        "added": added,
        "live_model_count": len(registry.live_ids),
        "message": f"Registered {len(added)} model(s) from provider '{provider_id}'",
    })


@router.post("/admin/models/set-enabled")
async def set_model_enabled(request: Request) -> JSONResponse:
    """Include or exclude a live model from the routing pool.

    Body: {model_id: "zen/mimo-v2.5-free", enabled: true|false}
    """
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_admin(request, settings)
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return JSONResponse(
            {"error": {"message": "Catalog not ready", "code": "nimmakai_not_ready"}},
            status_code=503,
        )
    body_or_err = await _safe_json(request)
    if isinstance(body_or_err, JSONResponse):
        return body_or_err
    body: dict[str, Any] = body_or_err
    model_id = str(body.get("model_id") or "").strip()
    if "enabled" not in body:
        return JSONResponse(
            {
                "error": {
                    "message": "enabled (bool) is required",
                    "code": "invalid_request",
                }
            },
            status_code=400,
        )
    enabled = bool(body.get("enabled"))
    try:
        result = registry.set_model_enabled(model_id, enabled)
    except ValueError as exc:
        return JSONResponse(
            {"error": {"message": str(exc), "code": "invalid_request"}},
            status_code=400,
        )
    except Exception as exc:
        return JSONResponse(
            {
                "error": {
                    "message": f"Failed to persist model pool change: {exc}",
                    "code": "persist_failed",
                }
            },
            status_code=500,
        )
    return JSONResponse({"ok": True, **result})


@router.post("/admin/models/bulk-enabled")
async def bulk_models_enabled(request: Request) -> JSONResponse:
    """Bulk include/exclude models in the pool.

    Body: {enable?: string[], disable?: string[], provider_id?: string}
    When provider_id is set with enable_all/disable_all, toggles that provider's models.
    """
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_admin(request, settings)
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return JSONResponse(
            {"error": {"message": "Catalog not ready", "code": "nimmakai_not_ready"}},
            status_code=503,
        )
    body_or_err = await _safe_json(request)
    if isinstance(body_or_err, JSONResponse):
        return body_or_err
    body: dict[str, Any] = body_or_err
    enable = body.get("enable") if isinstance(body.get("enable"), list) else []
    disable = body.get("disable") if isinstance(body.get("disable"), list) else []
    provider_id = str(body.get("provider_id") or "").strip().lower()
    if provider_id:
        prov_models = sorted(
            m for m in registry.live_ids if m.split("/", 1)[0] == provider_id
        )
        if body.get("enable_all") is True:
            enable = list(enable) + prov_models
        if body.get("disable_all") is True:
            disable = list(disable) + prov_models
    try:
        result = registry.set_models_enabled(
            enable=[str(x) for x in enable],
            disable=[str(x) for x in disable],
        )
    except Exception as exc:
        return JSONResponse(
            {
                "error": {
                    "message": f"Failed to persist model pool change: {exc}",
                    "code": "persist_failed",
                }
            },
            status_code=500,
        )
    return JSONResponse({"ok": True, **result})


@router.delete("/admin/providers/{provider_id}")
async def delete_provider(provider_id: str, request: Request) -> JSONResponse:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_admin(request, settings)
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
    require_admin(request, settings)
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
    require_admin(request, settings)
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
    require_admin(request, settings)
    prefs = getattr(request.app.state, "preferences", None)
    if prefs is None:
        return JSONResponse(
            {"error": {"message": "Preferences not ready", "code": "nimmakai_no_prefs"}},
            status_code=503,
        )
    body_or_err = await _safe_json(request)
    if isinstance(body_or_err, JSONResponse):
        return body_or_err
    body: dict[str, Any] = body_or_err
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
    require_admin(request, settings)
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
    require_admin(request, settings)
    prefs = getattr(request.app.state, "preferences", None)
    if prefs is None:
        return JSONResponse(
            {"error": {"message": "Preferences not ready", "code": "nimmakai_no_prefs"}},
            status_code=503,
        )
    prefs.clear_all()
    return JSONResponse({"ok": True, "preferences": []})


# ── Real-time Events (SSE) & Provider Health (NMK-504, 505) ─────────────


@router.get("/admin/events")
async def sse_events(request: Request):
    """Server-Sent Events stream for live health/status updates (NMK-505)."""
    import asyncio
    import hmac

    from fastapi import HTTPException, status
    from fastapi.responses import StreamingResponse

    from nimmakai.auth import extract_bearer, require_admin, validate_proxy_token

    settings = getattr(request.app.state, "settings", None) or get_settings()
    cookie = getattr(settings, "session_cookie_name", "nk_session") or "nk_session"
    if request.cookies.get(cookie):
        require_admin(request, settings)
    else:
        token = request.query_params.get("token") or extract_bearer(request)
        validate_proxy_token(
            token, settings, accounts=getattr(request.app.state, "accounts", None)
        )
        accounts = getattr(request.app.state, "accounts", None)
        is_admin = False
        if settings.accept_any_proxy_key or token and any(
            hmac.compare_digest(token, k) for k in (settings.proxy_api_keys or [])
        ):
            is_admin = True
        elif token and token.startswith("sk-nk-") and accounts is not None:
            user = accounts.resolve_api_key(token)
            is_admin = bool(user and user.get("role") == "admin")
        if not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "message": "Admin access required.",
                        "code": "admin_required",
                    }
                },
            )

    async def event_generator():
        import json
        yield "event: connected\ndata: {}\n\n"
        cycle = 0
        while True:
            await asyncio.sleep(10)
            cycle += 1
            hub = getattr(request.app.state, "hub", None)
            registry = getattr(request.app.state, "registry", None)
            stats = getattr(request.app.state, "routing_stats", None)
            health_payload = {}
            provider_health = {}
            if hub:
                for pid, rt in hub.runtimes.items():
                    provider_health[pid] = {
                        "enabled": rt.config.enabled,
                        "runtime": hub.has_runtime(pid),
                        "available_keys": rt.pool.available_count(),
                    }
            if registry and registry.live_ids:
                for mid in list(registry.live_ids)[:50]:
                    h = registry.health._by_model.get(mid)
                    if h:
                        health_payload[mid] = {
                            "ok": not registry.health.is_unhealthy(mid),
                            "tps": round(h.ewma_tok_per_s, 1) if h.ewma_tok_per_s > 0 else 0,
                            "latency": round(h.ewma_latency, 2) if h.ewma_latency > 0 else 0,
                            "error_rate": round(h.error_rate, 2),
                        }
            payload = {
                "cycle": cycle,
                "live_models": len(registry.live_ids) if registry else 0,
                "active_providers": len(hub.active_provider_ids()) if hub else 0,
                "fallback_advances": stats.fallback_advances if stats else 0,
                "provider_health": provider_health,
                "model_health": health_payload,
            }
            yield f"event: health\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/admin/health/providers")
async def provider_health_view(request: Request) -> JSONResponse:
    """Per-provider aggregated health data (NMK-403/504)."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_admin(request, settings)
    hub = getattr(request.app.state, "hub", None)
    registry = getattr(request.app.state, "registry", None)
    if not hub or not registry:
        return JSONResponse({"providers": {}})

    providers: dict[str, dict] = {}
    for pid, rt in hub.runtimes.items():
        model_ids = {m for m in registry.live_ids if m.split("/")[0] == pid}
        health_agg = registry.health.provider_health(model_ids, pid)
        model_details: dict[str, dict] = {}
        for mid in sorted(model_ids)[:30]:
            h = registry.health._by_model.get(mid)
            if h:
                model_details[mid] = {
                    "ok": not registry.health.is_unhealthy(mid),
                    "ewma_latency_s": round(h.ewma_latency, 3),
                    "ewma_tok_per_s": round(h.ewma_tok_per_s, 1),
                    "success_count": h.success_count,
                    "error_count": h.error_count,
                    "error_rate": round(h.error_rate, 3),
                    "cooldown": h.in_cooldown(),
                }
        cb_state = hub.circuit_breaker.state(pid).value
        providers[pid] = {
            "enabled": rt.config.enabled,
            "runtime": hub.has_runtime(pid),
            "aggregate_health": round(health_agg, 3),
            "circuit_breaker": cb_state,
            "model_count": len(model_ids),
            "available_keys": rt.pool.available_count(),
            "models": model_details,
        }
    return JSONResponse({"providers": providers})


@router.get("/admin/trace/{request_id}")
async def request_trace(request_id: str, request: Request) -> JSONResponse:
    """Request trace view — full routing decision breakdown (NMK-506)."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    require_admin(request, settings)
    from nimmakai.logging_setup import request_logs
    entries = request_logs.list(limit=200)
    matching = [e for e in entries if e.get("req") == request_id]
    return JSONResponse({
        "request_id": request_id,
        "entries": matching,
        "hint": "Returns all log entries for the given request_id",
    })
