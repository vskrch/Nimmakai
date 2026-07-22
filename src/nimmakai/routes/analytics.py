"""Analytics REST + SSE endpoints."""

from __future__ import annotations

import csv
import io
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from nimmakai.analytics.cost import list_default_rates
from nimmakai.auth import (
    auth_from_request,
    extract_bearer,
    require_proxy_auth,
    validate_proxy_token,
)
from nimmakai.config import get_settings

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _settings(request: Request):
    return getattr(request.app.state, "settings", None) or get_settings()


def _store(request: Request):
    store = getattr(request.app.state, "analytics_store", None)
    if store is None:
        return None
    return store


def _scope_user_id(request: Request) -> str | None:
    """Non-admins only see their own traces."""
    ctx = auth_from_request(request)
    if ctx is None:
        return None
    if ctx.is_admin:
        return None
    return ctx.user_id


def _require_analytics(request: Request):
    settings = _settings(request)
    require_proxy_auth(request, settings)
    if not getattr(settings, "analytics_enabled", True):
        return None, JSONResponse(
            {"error": {"message": "Analytics disabled", "code": "analytics_disabled"}},
            status_code=503,
        )
    store = _store(request)
    if store is None:
        return None, JSONResponse(
            {
                "error": {
                    "message": "Analytics not initialized",
                    "code": "analytics_unavailable",
                }
            },
            status_code=503,
        )
    return store, None


def _float_q(request: Request, name: str) -> float | None:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _int_q(request: Request, name: str, default: int) -> int:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ── traces ──────────────────────────────────────────────────────────


@router.get("/traces")
async def list_traces(request: Request) -> JSONResponse:
    store, err = _require_analytics(request)
    if err:
        return err
    assert store is not None
    data = store.list_traces(
        limit=_int_q(request, "limit", 50),
        offset=_int_q(request, "offset", 0),
        intent=request.query_params.get("intent") or None,
        model=request.query_params.get("model") or None,
        provider=request.query_params.get("provider") or None,
        api_key=request.query_params.get("api_key") or None,
        user_id=_scope_user_id(request),
        status=request.query_params.get("status") or None,
        since=_float_q(request, "since"),
        until=_float_q(request, "until"),
        search=request.query_params.get("search") or None,
        sort=request.query_params.get("sort") or "created_at",
        order=request.query_params.get("order") or "desc",
    )
    return JSONResponse(data)


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str, request: Request) -> JSONResponse:
    store, err = _require_analytics(request)
    if err:
        return err
    assert store is not None
    trace = store.get_trace(trace_id)
    if not trace:
        return JSONResponse(
            {"error": {"message": "Trace not found", "code": "not_found"}},
            status_code=404,
        )
    scoped = _scope_user_id(request)
    if scoped and trace.get("user_id") != scoped:
        return JSONResponse(
            {"error": {"message": "Trace not found", "code": "not_found"}},
            status_code=404,
        )
    return JSONResponse(trace)


@router.get("/traces/{trace_id}/spans")
async def get_trace_spans(trace_id: str, request: Request) -> JSONResponse:
    store, err = _require_analytics(request)
    if err:
        return err
    assert store is not None
    trace = store.get_trace(trace_id)
    scoped = _scope_user_id(request)
    if scoped and (not trace or trace.get("user_id") != scoped):
        return JSONResponse(
            {"error": {"message": "Trace not found", "code": "not_found"}},
            status_code=404,
        )
    return JSONResponse({"trace_id": trace_id, "spans": store.get_spans(trace_id)})


# ── timeseries ──────────────────────────────────────────────────────


@router.get("/timeseries/requests")
async def ts_requests(request: Request) -> JSONResponse:
    return await _ts(request, "requests")


@router.get("/timeseries/latency")
async def ts_latency(request: Request) -> JSONResponse:
    return await _ts(request, "latency")


@router.get("/timeseries/tokens")
async def ts_tokens(request: Request) -> JSONResponse:
    return await _ts(request, "tokens")


@router.get("/timeseries/cost")
async def ts_cost(request: Request) -> JSONResponse:
    return await _ts(request, "cost")


@router.get("/timeseries/ttft")
async def ts_ttft(request: Request) -> JSONResponse:
    return await _ts(request, "ttft")


async def _ts(request: Request, metric: str) -> JSONResponse:
    store, err = _require_analytics(request)
    if err:
        return err
    assert store is not None
    points = store.timeseries(
        metric,
        since=_float_q(request, "since"),
        until=_float_q(request, "until"),
        interval=request.query_params.get("interval") or "1m",
        intent=request.query_params.get("intent") or None,
        model=request.query_params.get("model") or None,
        provider=request.query_params.get("provider") or None,
        user_id=_scope_user_id(request),
    )
    return JSONResponse({"metric": metric, "points": points})


# ── breakdowns ──────────────────────────────────────────────────────


@router.get("/breakdown/models")
async def bd_models(request: Request) -> JSONResponse:
    return await _bd(request, "models")


@router.get("/breakdown/providers")
async def bd_providers(request: Request) -> JSONResponse:
    return await _bd(request, "providers")


@router.get("/breakdown/api_keys")
async def bd_api_keys(request: Request) -> JSONResponse:
    return await _bd(request, "api_keys")


@router.get("/breakdown/intents")
async def bd_intents(request: Request) -> JSONResponse:
    return await _bd(request, "intents")


@router.get("/breakdown/errors")
async def bd_errors(request: Request) -> JSONResponse:
    return await _bd(request, "errors")


@router.get("/breakdown/fallbacks")
async def bd_fallbacks(request: Request) -> JSONResponse:
    return await _bd(request, "fallbacks")


async def _bd(request: Request, dimension: str) -> JSONResponse:
    store, err = _require_analytics(request)
    if err:
        return err
    assert store is not None
    items = store.breakdown(
        dimension,
        since=_float_q(request, "since"),
        until=_float_q(request, "until"),
        limit=_int_q(request, "limit", 50),
        user_id=_scope_user_id(request),
    )
    return JSONResponse({"dimension": dimension, "items": items})


# ── summary ─────────────────────────────────────────────────────────


@router.get("/summary")
async def analytics_summary(request: Request) -> JSONResponse:
    store, err = _require_analytics(request)
    if err:
        return err
    assert store is not None
    return JSONResponse(
        store.summary(
            since=_float_q(request, "since"),
            until=_float_q(request, "until"),
            user_id=_scope_user_id(request),
        )
    )


@router.get("/status")
async def analytics_status(request: Request) -> JSONResponse:
    settings = _settings(request)
    require_proxy_auth(request, settings)
    writer = getattr(request.app.state, "trace_writer", None)
    bus = getattr(request.app.state, "event_bus", None)
    store = _store(request)
    return JSONResponse(
        {
            "enabled": bool(getattr(settings, "analytics_enabled", True)),
            "writer": writer.stats() if writer else None,
            "subscribers": bus.subscriber_count if bus else 0,
            "db": store.writer_stats_placeholder() if store else None,
            "retention_days": getattr(settings, "analytics_retention_days", 7),
            "rollup_retention_days": getattr(
                settings, "analytics_rollup_retention_days", 90
            ),
        }
    )


# ── cost rates ──────────────────────────────────────────────────────


@router.get("/cost/rates")
async def cost_rates(request: Request) -> JSONResponse:
    store, err = _require_analytics(request)
    if err:
        return err
    assert store is not None
    return JSONResponse(
        {
            "defaults": list_default_rates(),
            "overrides": store.list_cost_overrides(),
        }
    )


@router.put("/cost/rates/{model_id:path}")
async def put_cost_rate(model_id: str, request: Request) -> JSONResponse:
    from nimmakai.auth import require_admin

    store, err = _require_analytics(request)
    if err:
        return err
    require_admin(request, _settings(request))
    assert store is not None
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"message": "Invalid JSON", "code": "invalid_json"}},
            status_code=400,
        )
    try:
        inp = float(body.get("input_per_m", 0))
        out = float(body.get("output_per_m", 0))
    except (TypeError, ValueError):
        return JSONResponse(
            {"error": {"message": "input_per_m/output_per_m required", "code": "bad_request"}},
            status_code=400,
        )
    if inp < 0 or out < 0:
        return JSONResponse(
            {"error": {"message": "rates must be >= 0", "code": "bad_request"}},
            status_code=400,
        )
    store.set_cost_override(model_id, inp, out)
    return JSONResponse({"ok": True, "model_id": model_id, "input_per_m": inp, "output_per_m": out})


@router.delete("/cost/rates/{model_id:path}")
async def delete_cost_rate(model_id: str, request: Request) -> JSONResponse:
    from nimmakai.auth import require_admin

    store, err = _require_analytics(request)
    if err:
        return err
    require_admin(request, _settings(request))
    assert store is not None
    ok = store.delete_cost_override(model_id)
    return JSONResponse({"ok": ok, "model_id": model_id})


@router.post("/cost/rates/import")
async def import_cost_rates(request: Request) -> JSONResponse:
    """Bulk-import cost rates for all live models from models.dev.

    Body: { "overwrite": false } — skip models that already have overrides.
    """
    from nimmakai.analytics.cost import lookup_rates
    from nimmakai.auth import require_admin

    store, err = _require_analytics(request)
    if err:
        return err
    require_admin(request, _settings(request))
    assert store is not None
    try:
        body = await request.json()
    except Exception:
        body = {}
    overwrite = bool(body.get("overwrite"))

    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return JSONResponse(
            {"error": {"message": "Catalog not ready", "code": "nimmakai_not_ready"}},
            status_code=503,
        )

    existing = store.cost_overrides_map()
    imported = 0
    skipped = 0
    for mid in sorted(registry.active_live_ids()):
        if mid in existing and not overwrite:
            skipped += 1
            continue
        inp, out = lookup_rates(mid)
        if inp == 0.0 and out == 0.0:
            skipped += 1
            continue
        store.set_cost_override(mid, inp, out)
        imported += 1

    return JSONResponse({
        "ok": True,
        "imported": imported,
        "skipped": skipped,
        "total_live": len(registry.active_live_ids()),
    })


# ── export ──────────────────────────────────────────────────────────


@router.get("/export/traces", response_model=None)
async def export_traces(request: Request) -> StreamingResponse | JSONResponse:
    store, err = _require_analytics(request)
    if err:
        return err
    assert store is not None
    fmt = (request.query_params.get("format") or "csv").lower()
    since = _float_q(request, "since")
    until = _float_q(request, "until")
    limit = _int_q(request, "limit", 10000)
    rows = list(
        store.iter_export(
            since=since,
            until=until,
            limit=limit,
            user_id=_scope_user_id(request),
        )
    )
    cols = [
        "trace_id",
        "created_at",
        "model_requested",
        "model_routed",
        "intent",
        "intent_confidence",
        "provider_id",
        "status_code",
        "duration_ms",
        "upstream_ttft_ms",
        "prompt_tokens",
        "completion_tokens",
        "estimated_cost_usd",
        "fallback_index",
        "error_message",
    ]

    if fmt == "jsonl":

        async def _gen_jsonl():
            for r in rows:
                yield json.dumps(r, default=str) + "\n"

        return StreamingResponse(
            _gen_jsonl(),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="traces-{int(time.time())}.jsonl"'
            },
        )

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: r.get(c) for c in cols})
    data = buf.getvalue()

    async def _gen_csv():
        yield data

    return StreamingResponse(
        _gen_csv(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="traces-{int(time.time())}.csv"'
        },
    )


# ── SSE ─────────────────────────────────────────────────────────────


@router.get("/events", response_model=None)
async def analytics_events(request: Request) -> StreamingResponse | JSONResponse:
    """SSE live feed. Admins see all traces; users only their own."""
    import hmac

    from nimmakai.auth import AuthContext, resolve_auth

    settings = _settings(request)
    cookie = getattr(settings, "session_cookie_name", "nk_session") or "nk_session"
    store = getattr(request.app.state, "accounts", None)
    token = request.query_params.get("token") or extract_bearer(request)

    if request.cookies.get(cookie):
        auth = resolve_auth(request, settings)
        require_proxy_auth(request, settings)
    elif token:
        validate_proxy_token(token, settings, accounts=store)
        # Build AuthContext from query/header token (EventSource cannot set Authorization)
        if store is not None and token.startswith("sk-nk-"):
            user = store.resolve_api_key(token)
            if not user:
                return JSONResponse(
                    {"error": {"message": "Invalid API key", "code": "invalid_api_key"}},
                    status_code=401,
                )
            is_admin = user.get("role") == "admin"
            auth = AuthContext(
                token=token,
                user_id=user["id"],
                email=user.get("email"),
                role="admin" if is_admin else "user",
                status=user.get("status"),
                is_admin=is_admin,
                via="api_key",
            )
            if auth.status != "active":
                return JSONResponse(
                    {
                        "error": {
                            "message": "Account is not active",
                            "code": "account_not_active",
                        }
                    },
                    status_code=403,
                )
        elif settings.accept_any_proxy_key or any(
            hmac.compare_digest(token, k) for k in (settings.proxy_api_keys or [])
        ):
            auth = AuthContext(
                token=token,
                role="legacy_admin",
                status="active",
                is_admin=True,
                via="legacy_proxy",
            )
        else:
            return JSONResponse(
                {"error": {"message": "Invalid API key", "code": "invalid_api_key"}},
                status_code=401,
            )
    else:
        return JSONResponse(
            {"error": {"message": "Authentication required", "code": "unauthorized"}},
            status_code=401,
        )

    if not getattr(settings, "analytics_enabled", True):
        return JSONResponse(
            {"error": {"message": "Analytics disabled", "code": "analytics_disabled"}},
            status_code=503,
        )

    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        return JSONResponse(
            {"error": {"message": "Event bus unavailable", "code": "analytics_unavailable"}},
            status_code=503,
        )

    see_all = bool(auth.is_admin)
    uid = auth.user_id

    async def _generate():
        async for event in bus.subscribe(user_id=uid, see_all=see_all):
            if await request.is_disconnected():
                break
            yield event

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── retention trigger (admin) ───────────────────────────────────────


@router.post("/retention/run")
async def run_retention(request: Request) -> JSONResponse:
    import asyncio

    from nimmakai.auth import require_admin

    require_admin(request, _settings(request))
    store, err = _require_analytics(request)
    if err:
        return err
    retention = getattr(request.app.state, "retention_manager", None)
    if retention is None:
        return JSONResponse(
            {"error": {"message": "Retention manager unavailable"}},
            status_code=503,
        )
    report = await asyncio.to_thread(retention.run_cycle)
    return JSONResponse({"ok": True, "report": report})
