"""OpenAI-compatible endpoints proxied to NVIDIA NIM."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from nimmakai.auth import require_proxy_auth
from nimmakai.catalog import ModelRegistry
from nimmakai.config import Settings, get_settings
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
    upstream = _upstream(request)
    status, body, headers, _key = await upstream.request_json("GET", "/models")

    registry: ModelRegistry | None = getattr(request.app.state, "registry", None)
    if (
        settings.inject_auto_model
        and isinstance(body, dict)
        and isinstance(body.get("data"), list)
        and registry is not None
    ):
        auto = registry.synthetic_auto_model()
        ids = {item.get("id") for item in body["data"] if isinstance(item, dict)}
        if auto["id"] not in ids:
            body = {**body, "data": [auto, *body["data"]]}

    return JSONResponse(content=body, status_code=status, headers=headers)


@router.get("/models/{model_id:path}")
async def get_model(model_id: str, request: Request) -> JSONResponse:
    settings = _settings(request)
    require_proxy_auth(request, settings)
    if model_id in {"auto", "nimmakai/auto"}:
        registry: ModelRegistry | None = getattr(request.app.state, "registry", None)
        if registry is not None:
            return JSONResponse(content=registry.synthetic_auto_model())
    upstream = _upstream(request)
    status, body, headers, _key = await upstream.request_json(
        "GET", f"/models/{model_id}"
    )
    return JSONResponse(content=body, status_code=status, headers=headers)


async def _chat_like(
    request: Request,
    *,
    upstream_path: str,
) -> JSONResponse | StreamingResponse:
    upstream = _upstream(request)
    guard: AccountGuard = request.app.state.guard
    body = await request.json()
    body, decision, ctx, _token = await _prepare_routed(
        request, body, path=upstream_path
    )
    stream = bool(body.get("stream"))
    preferred = ctx.preferred_key_id

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
                media = result.headers.get("content-type", "text/event-stream")
                upstream_iter = result.byte_iter
                key_id = result.key.key_id if result.key else None
                ok = 200 <= result.status_code < 300

                async def _gated_stream() -> Any:
                    try:
                        async for chunk in upstream_iter:
                            yield chunk
                    finally:
                        await guard.after_request(ctx, key_id=key_id, success=ok)

                return StreamingResponse(
                    _gated_stream(),
                    status_code=result.status_code,
                    media_type=media,
                    headers={
                        **_merge_headers(result.headers, route_h),
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
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
            await guard.after_request(
                ctx,
                key_id=result_j.key.key_id if result_j.key else None,
                success=200 <= result_j.status_code < 300,
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
                try:
                    async for chunk in byte_iter:
                        yield chunk
                finally:
                    await guard.after_request(ctx, key_id=key_id, success=ok)

            return StreamingResponse(
                _gated_passthrough(),
                status_code=status,
                media_type=media,
                headers={
                    **{k: v for k, v in headers.items() if k.lower() != "content-type"},
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        status, resp_body, headers, key = await upstream.request_json(
            "POST",
            upstream_path,
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
