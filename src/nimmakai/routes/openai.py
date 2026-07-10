"""OpenAI-compatible endpoints proxied to NVIDIA NIM."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from nimmakai.auth import require_proxy_auth
from nimmakai.config import Settings, get_settings
from nimmakai.upstream import UpstreamClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["openai"])


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _upstream(request: Request) -> UpstreamClient:
    return request.app.state.upstream


def _maybe_default_model(body: dict[str, Any], settings: Settings) -> dict[str, Any]:
    if not body.get("model") and settings.default_model:
        body = {**body, "model": settings.default_model}
    return body


@router.get("/models")
async def list_models(request: Request) -> JSONResponse:
    settings = _settings(request)
    require_proxy_auth(request, settings)
    upstream = _upstream(request)
    status, body, headers = await upstream.request_json("GET", "/models")
    return JSONResponse(content=body, status_code=status, headers=headers)


@router.get("/models/{model_id:path}")
async def get_model(model_id: str, request: Request) -> JSONResponse:
    settings = _settings(request)
    require_proxy_auth(request, settings)
    upstream = _upstream(request)
    status, body, headers = await upstream.request_json("GET", f"/models/{model_id}")
    return JSONResponse(content=body, status_code=status, headers=headers)


@router.post("/chat/completions", response_model=None)
async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    """
    OpenAI Chat Completions — including streaming and tool/function calling
    payloads that coding agents rely on.
    """
    settings = _settings(request)
    require_proxy_auth(request, settings)
    upstream = _upstream(request)

    body = await request.json()
    body = _maybe_default_model(body, settings)
    stream = bool(body.get("stream"))

    if stream:
        status, byte_iter, headers, _key = await upstream.stream(
            "POST",
            "/chat/completions",
            json_body=body,
        )
        media = headers.get("content-type", "text/event-stream")
        return StreamingResponse(
            byte_iter,
            status_code=status,
            media_type=media,
            headers={
                **{k: v for k, v in headers.items() if k.lower() != "content-type"},
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    status, resp_body, headers = await upstream.request_json(
        "POST",
        "/chat/completions",
        json_body=body,
    )
    return JSONResponse(content=resp_body, status_code=status, headers=headers)


@router.post("/completions", response_model=None)
async def completions(request: Request) -> JSONResponse | StreamingResponse:
    """Legacy text completions (some tools still call this)."""
    settings = _settings(request)
    require_proxy_auth(request, settings)
    upstream = _upstream(request)

    body = await request.json()
    body = _maybe_default_model(body, settings)
    stream = bool(body.get("stream"))

    if stream:
        status, byte_iter, headers, _key = await upstream.stream(
            "POST",
            "/completions",
            json_body=body,
        )
        media = headers.get("content-type", "text/event-stream")
        return StreamingResponse(
            byte_iter,
            status_code=status,
            media_type=media,
            headers={
                **{k: v for k, v in headers.items() if k.lower() != "content-type"},
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    status, resp_body, headers = await upstream.request_json(
        "POST",
        "/completions",
        json_body=body,
    )
    return JSONResponse(content=resp_body, status_code=status, headers=headers)


@router.post("/embeddings")
async def embeddings(request: Request) -> JSONResponse:
    settings = _settings(request)
    require_proxy_auth(request, settings)
    upstream = _upstream(request)
    body = await request.json()
    body = _maybe_default_model(body, settings)
    status, resp_body, headers = await upstream.request_json(
        "POST",
        "/embeddings",
        json_body=body,
    )
    return JSONResponse(content=resp_body, status_code=status, headers=headers)


@router.post("/responses", response_model=None)
async def responses_api(request: Request) -> JSONResponse | StreamingResponse:
    """
    OpenAI Responses API passthrough (used by some newer agent SDKs).
    NIM may or may not support this path; we proxy transparently.
    """
    settings = _settings(request)
    require_proxy_auth(request, settings)
    upstream = _upstream(request)
    body = await request.json()
    body = _maybe_default_model(body, settings)
    stream = bool(body.get("stream"))

    if stream:
        status, byte_iter, headers, _key = await upstream.stream(
            "POST",
            "/responses",
            json_body=body,
        )
        media = headers.get("content-type", "text/event-stream")
        return StreamingResponse(
            byte_iter,
            status_code=status,
            media_type=media,
            headers={
                **{k: v for k, v in headers.items() if k.lower() != "content-type"},
                "Cache-Control": "no-cache",
            },
        )

    status, resp_body, headers = await upstream.request_json(
        "POST",
        "/responses",
        json_body=body,
    )
    return JSONResponse(content=resp_body, status_code=status, headers=headers)
