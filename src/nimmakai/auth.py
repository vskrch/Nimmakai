"""Client-facing auth: legacy proxy keys, user API keys, dashboard sessions."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from nimmakai.config import Settings


@dataclass
class AuthContext:
    """Resolved caller identity."""

    token: str | None
    user_id: str | None = None
    email: str | None = None
    role: str = "anonymous"  # anonymous | user | admin | legacy_admin
    status: str | None = None
    is_admin: bool = False
    via: str = "none"  # session | api_key | legacy_proxy


def extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return (
            request.headers.get("x-api-key")
            or request.headers.get("api-key")
            or request.headers.get("openai-api-key")
        )
    parts = auth.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return auth.strip()


def _accounts(request: Request):
    return getattr(request.app.state, "accounts", None)


def _legacy_proxy_ok(token: str, settings: Settings) -> bool:
    if settings.accept_any_proxy_key:
        return True
    if not settings.proxy_api_keys:
        return False
    return any(hmac.compare_digest(token, k) for k in settings.proxy_api_keys)


def resolve_auth(request: Request, settings: Settings) -> AuthContext:
    """
    Resolution order:
      1. Session cookie (dashboard)
      2. Bearer sk-nk-* user API key
      3. Legacy PROXY_API_KEYS / ALLOW_INSECURE_AUTH
    """
    store = _accounts(request)
    cookie_name = getattr(settings, "session_cookie_name", "nk_session") or "nk_session"
    session_raw = request.cookies.get(cookie_name)

    if store is not None and session_raw:
        user = store.resolve_session(session_raw)
        if user:
            is_admin = user.get("role") == "admin"
            return AuthContext(
                token=None,
                user_id=user["id"],
                email=user["email"],
                role="admin" if is_admin else "user",
                status=user["status"],
                is_admin=is_admin,
                via="session",
            )

    bearer = extract_bearer(request)

    if store is not None and bearer and bearer.startswith("sk-nk-"):
        user = store.resolve_api_key(bearer)
        if user:
            is_admin = user.get("role") == "admin"
            return AuthContext(
                token=bearer,
                user_id=user["id"],
                email=user["email"],
                role="admin" if is_admin else "user",
                status=user["status"],
                is_admin=is_admin,
                via="api_key",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Invalid or inactive API key.",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
        )

    if bearer and _legacy_proxy_ok(bearer, settings):
        return AuthContext(
            token=bearer,
            user_id=None,
            email=None,
            role="legacy_admin",
            status="active",
            is_admin=True,
            via="legacy_proxy",
        )

    if not bearer:
        # No credentials
        if settings.accept_any_proxy_key and not (store and store.list_users(limit=1)):
            # insecure local with no users yet — allow empty? No, require token.
            pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Missing API key or session. Sign in or set Authorization: Bearer <key>.",
                    "type": "invalid_request_error",
                    "code": "missing_api_key",
                }
            },
        )

    # Unknown bearer and no legacy match
    if settings.accept_any_proxy_key:
        return AuthContext(
            token=bearer,
            role="legacy_admin",
            is_admin=True,
            via="legacy_proxy",
            status="active",
        )

    if not settings.proxy_api_keys and store is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": (
                        "PROXY_API_KEYS is empty. Set keys or ALLOW_INSECURE_AUTH=true "
                        "for local dev."
                    ),
                    "type": "invalid_request_error",
                    "code": "proxy_auth_not_configured",
                }
            },
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {
                "message": "Invalid API key.",
                "type": "invalid_request_error",
                "code": "invalid_api_key",
            }
        },
    )


def validate_proxy_token(
    token: str | None,
    settings: Settings,
    *,
    accounts: Any | None = None,
) -> str:
    """Legacy helper used by SSE query-token auth."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Missing API key. Set Authorization: Bearer <key>.",
                    "type": "invalid_request_error",
                    "code": "missing_api_key",
                }
            },
        )
    if settings.accept_any_proxy_key:
        return token
    if accounts is not None and token.startswith("sk-nk-"):
        user = accounts.resolve_api_key(token)
        if user:
            return token
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Invalid or inactive API key.",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
        )
    if not settings.proxy_api_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": (
                        "PROXY_API_KEYS is empty. Set keys or ALLOW_INSECURE_AUTH=true "
                        "for local dev."
                    ),
                    "type": "invalid_request_error",
                    "code": "proxy_auth_not_configured",
                }
            },
        )
    if not any(hmac.compare_digest(token, k) for k in settings.proxy_api_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Invalid API key.",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
        )
    return token


def require_proxy_auth(request: Request, settings: Settings) -> str:
    """
    Validate caller for proxy + dashboard APIs.
    Returns bearer token string when present (for trace attribution), else "".
    Attaches AuthContext to request.state.auth.
    """
    ctx = resolve_auth(request, settings)
    request.state.auth = ctx
    return ctx.token or ""


def require_admin(request: Request, settings: Settings) -> AuthContext:
    ctx = resolve_auth(request, settings)
    request.state.auth = ctx
    if not ctx.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "message": "Admin access required.",
                    "type": "invalid_request_error",
                    "code": "admin_required",
                }
            },
        )
    return ctx


def require_active_user(request: Request, settings: Settings) -> AuthContext:
    ctx = resolve_auth(request, settings)
    request.state.auth = ctx
    if ctx.is_admin:
        return ctx
    if ctx.status != "active" or not ctx.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "message": "Account is not active. Verify email and wait for admin approval.",
                    "type": "invalid_request_error",
                    "code": "account_not_active",
                    "status": ctx.status,
                }
            },
        )
    return ctx


def auth_from_request(request: Request) -> AuthContext | None:
    return getattr(request.state, "auth", None)
