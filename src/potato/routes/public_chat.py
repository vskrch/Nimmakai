"""Public chat endpoints — no account required.

Serves the /chat web UI's backend. Uses the gateway's own provider keys
(admin-configured) so anyone can chat without signing up. Per-IP rate limited.

Gate: settings.public_chat_enabled (default true).
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from potato.compat import openai_error
from potato.config import get_settings

logger = logging.getLogger("potato.public_chat")

router = APIRouter()

# ponytail: in-memory per-IP sliding-window rate limiter. Per-account locks if
# throughput matters across multiple workers (single-process default).
_RPM_WINDOW = 60.0
_hits: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str, rpm: int) -> bool:
    now = time.time()
    q = _hits[ip]
    # drop entries older than the window
    while q and now - q[0] > _RPM_WINDOW:
        q.popleft()
    if len(q) >= rpm:
        return False
    q.append(now)
    return True


def _settings(request: Request) -> Any:
    return getattr(request.app.state, "settings", None) or get_settings()


def _public_disabled(settings: Any) -> JSONResponse | None:
    if not getattr(settings, "public_chat_enabled", True):
        return JSONResponse(
            content=openai_error(
                "Public chat is disabled on this gateway.",
                code="public_chat_disabled",
                type_="invalid_request_error",
            ),
            status_code=403,
        )
    return None


@router.get("/chat/api/models")
async def public_models(request: Request) -> JSONResponse:
    """List available models for the public chat picker (no auth)."""
    settings = _settings(request)
    blocked = _public_disabled(settings)
    if blocked is not None:
        return blocked
    registry = getattr(request.app.state, "registry", None)
    if registry is None or not registry.live_ids:
        return JSONResponse(
            content={"object": "list", "data": []},
        )
    data = []
    for mid in sorted(registry.active_live_ids()):
        item: dict[str, Any] = {
            "id": mid,
            "object": "model",
            "created": 0,
            "owned_by": mid.split("/", 1)[0] if "/" in mid else "unknown",
        }
        data.append(registry.enrich_model_entry(item))
    if getattr(settings, "inject_auto_model", True):
        autos = registry.synthetic_auto_models()
        data = [*autos, *data]
    return JSONResponse(content={"object": "list", "data": data})


@router.post("/chat/api/completions", response_model=None)
async def public_completions(request: Request) -> JSONResponse | StreamingResponse:
    """Public chat completion (no auth). Per-IP rate limited. Uses gateway keys."""
    settings = _settings(request)
    blocked = _public_disabled(settings)
    if blocked is not None:
        return blocked

    ip = _client_ip(request)
    if not _check_rate_limit(ip, int(getattr(settings, "public_chat_rpm", 20))):
        return JSONResponse(
            content=openai_error(
                f"Rate limit reached ({settings.public_chat_rpm} req/min for anonymous chat). "
                "Create an account for higher limits.",
                code="public_rate_limited",
                type_="rate_limit_error",
            ),
            status_code=429,
            headers={"Retry-After": "60"},
        )

    # Reuse the internal _chat_like pipeline but skip require_active_user.
    # We import here to avoid circular imports and to set a request-state flag.
    from potato.routes.openai import _chat_like

    # Mark as anonymous-public so trace attribution logs it
    request.state.public_chat = True

    # Temporarily allow insecure auth on the request scope so require_active_user
    # resolves to an anonymous legacy_admin context (the gateway serves itself).
    # We do this by stashing a synthetic auth context that downstream code reads.
    from potato.auth import AuthContext

    request.state.auth = AuthContext(
        token=None,
        user_id=None,
        email=None,
        role="anonymous",
        status="active",
        is_admin=False,
        via="public_chat",
    )

    return await _chat_like(request, upstream_path="/chat/completions")


@router.get("/chat/api/health")
async def public_health(request: Request) -> JSONResponse:
    """Lightweight health for the chat UI (no auth)."""
    settings = _settings(request)
    blocked = _public_disabled(settings)
    if blocked is not None:
        return blocked
    registry = getattr(request.app.state, "registry", None)
    hub = getattr(request.app.state, "hub", None)
    live_models = len(registry.live_ids) if registry else 0
    active_providers = len(hub.active_provider_ids()) if hub else 0
    return JSONResponse(
        {
            "status": "ok" if live_models and active_providers else "degraded",
            "public_chat": True,
            "live_models": live_models,
            "active_providers": active_providers,
            "rpm_limit": settings.public_chat_rpm,
        }
    )