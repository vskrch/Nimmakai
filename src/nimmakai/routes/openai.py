"""OpenAI-compatible endpoints proxied to NVIDIA NIM."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from nimmakai.auth import require_proxy_auth
from nimmakai.catalog import ModelRegistry
from nimmakai.config import Settings, get_settings
from nimmakai.logging_setup import RequestLog, log_request_line, request_logs
from nimmakai.routing import (
    FallbackExecutor,
    IntentClassifier,
    ModelSelector,
    RouteDecision,
)
from nimmakai.safety import AccountGuard
from nimmakai.upstream import UpstreamClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["openai"])


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _upstream(request: Request) -> UpstreamClient:
    return request.app.state.upstream


def _routing_disabled(request: Request, settings: Settings) -> bool:
    if not settings.routing_enabled:
        return True
    flag = request.headers.get("x-nimmakai-disable-route") or request.headers.get(
        "X-Nimmakai-Disable-Route"
    )
    return flag in {"1", "true", "yes"}


async def _prepare_routed(
    request: Request,
    body: dict[str, Any],
    *,
    path: str,
) -> tuple[
    dict[str, Any],
    RouteDecision | None,
    Any,
    str | None,
]:
    """
    Classify + select model chain. Returns (body, decision, guard_ctx, proxy_token).
    When routing is off, decision is None and body may only get default_model.
    """
    settings = _settings(request)
    proxy_token = require_proxy_auth(request, settings)
    guard: AccountGuard = request.app.state.guard

    if _routing_disabled(request, settings):
        if not body.get("model") and settings.default_model:
            body = {**body, "model": settings.default_model}
        ctx = await guard.before_request(
            headers=request.headers, proxy_token=proxy_token, body=body
        )
        return body, None, ctx, proxy_token

    classifier: IntentClassifier = request.app.state.classifier
    selector: ModelSelector | None = request.app.state.selector
    registry: ModelRegistry | None = request.app.state.registry

    if selector is None or registry is None:
        if not body.get("model") and settings.default_model:
            body = {**body, "model": settings.default_model}
        ctx = await guard.before_request(
            headers=request.headers, proxy_token=proxy_token, body=body
        )
        return body, None, ctx, proxy_token

    intent = classifier.classify(path=path, body=body, headers=request.headers)
    if settings.classify_mode == "rules_then_llm":
        upstream = _upstream(request)
        pool = request.app.state.pool
        pressure = pool.available_count() <= max(1, len(pool) // 4)
        chain = registry.chain_for_intent("chat_fast")
        fast = chain[0] if chain else None
        intent = await classifier.classify_maybe_llm(
            path=path,
            body=body,
            headers=request.headers,
            upstream=upstream,
            fast_model=fast,
            pool_pressure_high=pressure,
        )

    decision = selector.resolve(body.get("model"), intent)
    ctx = await guard.before_request(
        headers=request.headers, proxy_token=proxy_token, body=body
    )
    return body, decision, ctx, proxy_token


def _merge_headers(
    upstream_headers: dict[str, str],
    extra: dict[str, str],
) -> dict[str, str]:
    out = {k: v for k, v in upstream_headers.items() if k.lower() != "content-type"}
    out.update(extra)
    return out


@router.get("/models")
async def list_models(request: Request) -> JSONResponse:
    settings = _settings(request)
    require_proxy_auth(request, settings)
    hub = getattr(request.app.state, "hub", None)
    registry: ModelRegistry | None = getattr(request.app.state, "registry", None)

    # Prefer unified live catalog from hub refresh (namespaced ids)
    if registry is not None and registry.live_ids:
        data = []
        for mid in sorted(registry.live_ids):
            item: dict[str, Any] = {
                "id": mid,
                "object": "model",
                "created": 0,
                "owned_by": mid.split("/", 1)[0] if "/" in mid else "unknown",
            }
            data.append(registry.enrich_model_entry(item))
        if settings.inject_auto_model:
            auto = registry.synthetic_auto_model()
            data = [auto, *data]
        return JSONResponse(content={"object": "list", "data": data})

    # Fallback: single default upstream
    upstream = _upstream(request)
    status, body, headers, _key = await upstream.request_json("GET", "/models")
    if isinstance(body, dict) and isinstance(body.get("data"), list) and registry is not None:
        if hub is not None:
            pid = "nim"
            namespaced = []
            for item in body["data"]:
                if isinstance(item, dict) and item.get("id"):
                    ns = hub.namespace(pid, str(item["id"]))
                    namespaced.append(registry.enrich_model_entry({**item, "id": ns}))
                else:
                    namespaced.append(item)
            body = {**body, "data": namespaced}
        else:
            registry._ingest_context_from_api_items(body["data"])
            body = {
                **body,
                "data": [
                    registry.enrich_model_entry(item) if isinstance(item, dict) else item
                    for item in body["data"]
                ],
            }
        if settings.inject_auto_model:
            auto = registry.synthetic_auto_model()
            ids = {item.get("id") for item in body["data"] if isinstance(item, dict)}
            if auto["id"] not in ids:
                body = {**body, "data": [auto, *body["data"]]}
    return JSONResponse(content=body, status_code=status, headers=headers)


@router.get("/models/{model_id:path}")
async def get_model(model_id: str, request: Request) -> JSONResponse:
    settings = _settings(request)
    require_proxy_auth(request, settings)
    registry: ModelRegistry | None = getattr(request.app.state, "registry", None)
    hub = getattr(request.app.state, "hub", None)
    if model_id in {"auto", "nimmakai/auto"} and registry is not None:
        return JSONResponse(content=registry.synthetic_auto_model())
    if registry is not None:
        resolved = registry.resolve_live_id(model_id) or model_id
        if resolved in registry.live_ids:
            item = {
                "id": resolved,
                "object": "model",
                "created": 0,
                "owned_by": resolved.split("/", 1)[0],
            }
            return JSONResponse(content=registry.enrich_model_entry(item))
    if hub is not None:
        client, _pid, upstream_mid = hub.client_for_model(model_id)
        status, body, headers, _key = await client.request_json(
            "GET", f"/models/{upstream_mid}"
        )
        if registry is not None and isinstance(body, dict) and status < 400:
            body = registry.enrich_model_entry({**body, "id": model_id})
        return JSONResponse(content=body, status_code=status, headers=headers)
    upstream = _upstream(request)
    status, body, headers, _key = await upstream.request_json(
        "GET", f"/models/{model_id}"
    )
    if registry is not None and isinstance(body, dict) and status < 400:
        body = registry.enrich_model_entry(body)
    return JSONResponse(content=body, status_code=status, headers=headers)


def _client_ip(request: Request) -> str | None:
    xf = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if xf:
        return xf.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _finish_log(
    entry: RequestLog,
    *,
    status: int,
    t0: float,
    model_routed: str | None = None,
    provider: str | None = None,
    intent: str | None = None,
    route_mode: str | None = None,
    fallback_index: int | None = None,
    stream: bool | None = None,
    error: str | None = None,
    model_requested: str | None = None,
) -> None:
    entry.status = status
    entry.duration_ms = (time.perf_counter() - t0) * 1000
    if model_routed is not None:
        entry.model_routed = model_routed
    if provider is not None:
        entry.provider = provider
    if intent is not None:
        entry.intent = intent
    if route_mode is not None:
        entry.route_mode = route_mode
    if fallback_index is not None:
        entry.fallback_index = fallback_index
    if stream is not None:
        entry.stream = stream
    if error is not None:
        entry.error = error
    if model_requested is not None:
        entry.model_requested = model_requested
    request_logs.add(entry)
    log_request_line(entry)


async def _chat_like(
    request: Request,
    *,
    upstream_path: str,
) -> JSONResponse | StreamingResponse:
    t0 = time.perf_counter()
    req_id = getattr(request.state, "request_id", None) or "noreq"
    entry = RequestLog(
        id=req_id,
        ts=time.time(),
        method=request.method,
        path=str(request.url.path),
        client=_client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:160] or None,
    )
    upstream = _upstream(request)
    guard: AccountGuard = request.app.state.guard
    try:
        body = await request.json()
    except Exception as exc:
        _finish_log(entry, status=400, t0=t0, error=f"invalid_json:{exc}")
        return JSONResponse(
            {
                "error": {
                    "message": "Invalid JSON body",
                    "type": "invalid_request_error",
                    "code": "invalid_json",
                }
            },
            status_code=400,
        )

    entry.model_requested = str(body.get("model") or "") or None
    entry.stream = bool(body.get("stream"))
    # Cursor sometimes sends huge payloads — log size only
    try:
        msg_n = len(body.get("messages") or body.get("input") or [])
        entry.notes.append(f"messages={msg_n}")
        if body.get("tools"):
            entry.notes.append(f"tools={len(body.get('tools') or [])}")
    except Exception:
        pass

    try:
        body, decision, ctx, _token = await _prepare_routed(
            request, body, path=upstream_path
        )
    except Exception as exc:
        # Auth HTTPException propagates via FastAPI — re-raise
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            _finish_log(
                entry,
                status=exc.status_code,
                t0=t0,
                error=str(getattr(exc, "detail", "auth")),
            )
            raise
        _finish_log(entry, status=500, t0=t0, error=str(exc))
        raise

    stream = bool(body.get("stream"))
    preferred = ctx.preferred_key_id
    chain_head = ""
    if decision is not None and decision.chain:
        chain_head = decision.chain[0]
        logger.info(
            "route req=%s path=%s requested=%s intent=%s mode=%s chain_head=%s chain_len=%s",
            req_id,
            upstream_path,
            decision.requested_model,
            decision.intent.value,
            decision.mode,
            chain_head,
            len(decision.chain),
        )

    try:
        if decision is not None:
            fallback: FallbackExecutor = request.app.state.fallback
            if stream:
                result = await fallback.execute_stream(
                    upstream_path,
                    body,
                    decision,
                    preferred_key_id=preferred,
                )
                route_h = fallback.routing_headers(
                    decision,
                    model=result.model,
                    key_id=result.key.key_id if result.key else None,
                    fallback_index=result.fallback_index,
                )
                route_h["X-Request-Id"] = req_id
                media = result.headers.get("content-type", "text/event-stream")
                upstream_iter = result.byte_iter
                key_id = result.key.key_id if result.key else None
                ok = 200 <= result.status_code < 300
                provider = route_h.get("X-Nimmakai-Provider")

                async def _gated_stream() -> Any:
                    err: str | None = None
                    try:
                        async for chunk in upstream_iter:
                            yield chunk
                    except Exception as stream_exc:
                        err = str(stream_exc)
                        logger.warning(
                            "stream consumer error req=%s model=%s: %s",
                            req_id,
                            result.model,
                            stream_exc,
                        )
                        try:
                            yield b"data: [DONE]\n\n"
                        except Exception:
                            pass
                    finally:
                        await guard.after_request(ctx, key_id=key_id, success=ok and not err)
                        _finish_log(
                            entry,
                            status=result.status_code if not err else 499,
                            t0=t0,
                            model_routed=result.model,
                            provider=provider,
                            intent=decision.intent.value,
                            route_mode=decision.mode,
                            fallback_index=result.fallback_index,
                            stream=True,
                            error=err,
                            model_requested=str(decision.requested_model or body.get("model") or "")
                            or None,
                        )

                return StreamingResponse(
                    _gated_stream(),
                    status_code=result.status_code,
                    media_type=media,
                    headers={
                        **_merge_headers(result.headers, route_h),
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    },
                )

            result_j = await fallback.execute_json(
                upstream_path,
                body,
                decision,
                preferred_key_id=preferred,
            )
            route_h = fallback.routing_headers(
                decision,
                model=result_j.model,
                key_id=result_j.key.key_id if result_j.key else None,
                fallback_index=result_j.fallback_index,
            )
            route_h["X-Request-Id"] = req_id
            await guard.after_request(
                ctx,
                key_id=result_j.key.key_id if result_j.key else None,
                success=200 <= result_j.status_code < 300,
            )
            _finish_log(
                entry,
                status=result_j.status_code,
                t0=t0,
                model_routed=result_j.model,
                provider=route_h.get("X-Nimmakai-Provider"),
                intent=decision.intent.value,
                route_mode=decision.mode,
                fallback_index=result_j.fallback_index,
                stream=False,
                model_requested=str(decision.requested_model or body.get("model") or "")
                or None,
                error=None
                if result_j.status_code < 400
                else f"upstream_{result_j.status_code}",
            )
            return JSONResponse(
                content=result_j.body,
                status_code=result_j.status_code,
                headers=_merge_headers(result_j.headers, route_h),
            )

        # Passthrough (routing disabled)
        if stream:
            status, byte_iter, headers, key = await upstream.stream(
                "POST",
                upstream_path,
                json_body=body,
                preferred_key_id=preferred,
            )
            media = headers.get("content-type", "text/event-stream")
            key_id = key.key_id
            ok = 200 <= status < 300

            async def _gated_passthrough() -> Any:
                err: str | None = None
                try:
                    async for chunk in byte_iter:
                        yield chunk
                except Exception as stream_exc:
                    err = str(stream_exc)
                    try:
                        yield b"data: [DONE]\n\n"
                    except Exception:
                        pass
                finally:
                    await guard.after_request(ctx, key_id=key_id, success=ok and not err)
                    _finish_log(
                        entry,
                        status=status if not err else 499,
                        t0=t0,
                        model_routed=str(body.get("model") or ""),
                        stream=True,
                        route_mode="disabled",
                        error=err,
                    )

            return StreamingResponse(
                _gated_passthrough(),
                status_code=status,
                media_type=media,
                headers={
                    **{k: v for k, v in headers.items() if k.lower() != "content-type"},
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                    "X-Request-Id": req_id,
                },
            )

        status, resp_body, headers, key = await upstream.request_json(
            "POST",
            upstream_path,
            json_body=body,
            preferred_key_id=preferred,
        )
        await guard.after_request(ctx, key_id=key.key_id, success=200 <= status < 300)
        _finish_log(
            entry,
            status=status,
            t0=t0,
            model_routed=str(body.get("model") or ""),
            stream=False,
            route_mode="disabled",
        )
        headers = {**headers, "X-Request-Id": req_id}
        return JSONResponse(content=resp_body, status_code=status, headers=headers)
    except RuntimeError as exc:
        await guard.after_request(ctx, success=False)
        _finish_log(entry, status=503, t0=t0, error=str(exc), stream=stream)
        return JSONResponse(content=guard.pool_exhausted_error(), status_code=503)
    except Exception as exc:
        await guard.after_request(ctx, success=False)
        _finish_log(entry, status=500, t0=t0, error=str(exc), stream=stream)
        logger.exception("chat path failed req=%s", req_id)
        raise


@router.post("/chat/completions", response_model=None)
async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    return await _chat_like(request, upstream_path="/chat/completions")


@router.post("/completions", response_model=None)
async def completions(request: Request) -> JSONResponse | StreamingResponse:
    return await _chat_like(request, upstream_path="/completions")


@router.post("/embeddings")
async def embeddings(request: Request) -> JSONResponse:
    upstream = _upstream(request)
    guard: AccountGuard = request.app.state.guard
    body = await request.json()
    body, decision, ctx, _token = await _prepare_routed(
        request, body, path="/embeddings"
    )
    preferred = ctx.preferred_key_id
    try:
        if decision is not None and request.app.state.fallback is not None:
            fallback: FallbackExecutor = request.app.state.fallback
            # If embeddings chain empty, passthrough requested model
            if not decision.chain and body.get("model"):
                status, resp_body, headers, key = await upstream.request_json(
                    "POST",
                    "/embeddings",
                    json_body=body,
                    preferred_key_id=preferred,
                )
                await guard.after_request(
                    ctx, key_id=key.key_id, success=200 <= status < 300
                )
                return JSONResponse(
                    content=resp_body, status_code=status, headers=headers
                )

            result = await fallback.execute_json(
                "/embeddings",
                body,
                decision,
                preferred_key_id=preferred,
            )
            route_h = fallback.routing_headers(
                decision,
                model=result.model,
                key_id=result.key.key_id if result.key else None,
                fallback_index=result.fallback_index,
            )
            await guard.after_request(
                ctx,
                key_id=result.key.key_id if result.key else None,
                success=200 <= result.status_code < 300,
            )
            return JSONResponse(
                content=result.body,
                status_code=result.status_code,
                headers=_merge_headers(result.headers, route_h),
            )

        status, resp_body, headers, key = await upstream.request_json(
            "POST",
            "/embeddings",
            json_body=body,
            preferred_key_id=preferred,
        )
        await guard.after_request(ctx, key_id=key.key_id, success=200 <= status < 300)
        return JSONResponse(content=resp_body, status_code=status, headers=headers)
    except RuntimeError:
        await guard.after_request(ctx, success=False)
        return JSONResponse(content=guard.pool_exhausted_error(), status_code=503)
    except Exception:
        await guard.after_request(ctx, success=False)
        raise


@router.post("/responses", response_model=None)
async def responses_api(request: Request) -> JSONResponse | StreamingResponse:
    return await _chat_like(request, upstream_path="/responses")
