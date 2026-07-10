"""Client-facing API key validation (what Cursor / agents send)."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from nimmakai.config import Settings


def extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        # Some clients send api-key style headers
        return (
            request.headers.get("x-api-key")
            or request.headers.get("api-key")
            or request.headers.get("openai-api-key")
        )
    parts = auth.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return auth.strip()


def require_proxy_auth(request: Request, settings: Settings) -> str:
    """
    Validate the client API key against PROXY_API_KEYS.
    Empty PROXY_API_KEYS only accepted when ALLOW_INSECURE_AUTH=true.
    """
    token = extract_bearer(request)
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

    if token not in settings.proxy_api_keys:
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
