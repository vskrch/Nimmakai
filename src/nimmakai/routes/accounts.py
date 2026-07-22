"""Signup, verify, login, logout, me, API keys + admin user approval."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from nimmakai.accounts.email import OutboundEmail, get_email_sender
from nimmakai.accounts.store import (
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SUSPENDED,
    AccountStore,
)
from nimmakai.auth import require_admin, require_proxy_auth, resolve_auth
from nimmakai.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["accounts"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _settings(request: Request):
    return getattr(request.app.state, "settings", None) or get_settings()


def _store(request: Request) -> AccountStore | None:
    return getattr(request.app.state, "accounts", None)


def _cookie_kwargs(settings) -> dict[str, Any]:
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": bool(getattr(settings, "session_secure_cookie", False)),
        "max_age": 30 * 24 * 3600,
        "path": "/",
    }


def _base_url(request: Request, settings) -> str:
    configured = getattr(settings, "public_base_url", None)
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.post("/auth/signup")
async def signup(request: Request) -> JSONResponse:
    store = _store(request)
    if store is None:
        return JSONResponse(
            {"error": {"message": "Accounts not initialized", "code": "unavailable"}},
            status_code=503,
        )
    settings = _settings(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"message": "Invalid JSON", "code": "invalid_json"}},
            status_code=400,
        )
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if not _EMAIL_RE.match(email):
        return JSONResponse(
            {"error": {"message": "Valid email required", "code": "invalid_email"}},
            status_code=400,
        )
    if len(password) < 8:
        return JSONResponse(
            {
                "error": {
                    "message": "Password must be at least 8 characters",
                    "code": "weak_password",
                }
            },
            status_code=400,
        )
    if store.get_user_by_email(email):
        return JSONResponse(
            {"error": {"message": "Email already registered", "code": "email_taken"}},
            status_code=409,
        )

    admin_emails = {
        e.strip().lower()
        for e in (getattr(settings, "admin_emails", None) or [])
        if e and str(e).strip()
    }
    role = "admin" if email in admin_emails else "user"
    # Admins still verify email, then auto-active on verify
    user = store.create_user(email, password, role=role)
    token = store.create_verify_token(user["id"])
    verify_url = f"{_base_url(request, settings)}/auth/verify?token={token}"
    sender = get_email_sender(
        getattr(settings, "email_backend", "stub") or "stub",
        settings=settings,
    )
    result = sender.send(
        OutboundEmail(
            to=email,
            subject="Verify your Nimmakai account",
            text=(
                f"Welcome to Nimmakai.\n\n"
                f"Verify your email:\n{verify_url}\n\n"
                f"After verification an admin must approve your account "
                f"before an API key is issued.\n"
            ),
        )
    )
    payload: dict[str, Any] = {
        "ok": True,
        "user": store.public_user(user),
        "message": "Check your email to verify. (Stub backend logs the link.)",
    }
    if (getattr(settings, "email_backend", "stub") or "stub") == "stub":
        payload["verify_url"] = verify_url
        payload["email_preview"] = result
    return JSONResponse(payload, status_code=201)


@router.get("/auth/verify")
async def verify_email(request: Request, token: str = "") -> Response:
    store = _store(request)
    settings = _settings(request)
    if store is None or not token:
        return JSONResponse(
            {"error": {"message": "Invalid token", "code": "invalid_token"}},
            status_code=400,
        )
    user_id = store.consume_verify_token(token)
    if not user_id:
        return JSONResponse(
            {"error": {"message": "Invalid or expired token", "code": "invalid_token"}},
            status_code=400,
        )
    user = store.mark_verified(user_id)
    if not user:
        return JSONResponse(
            {"error": {"message": "User not found", "code": "not_found"}},
            status_code=404,
        )

    # mark_verified only transitions STATUS_UNVERIFIED → STATUS_PENDING.
    # If the user was already verified, rejected, or suspended, the UPDATE
    # matched zero rows and the status is unchanged — reject the re-click.
    if user["status"] != STATUS_PENDING:
        return JSONResponse(
            {
                "error": {
                    "message": "Account is not pending verification",
                    "code": "already_verified",
                }
            },
            status_code=409,
        )

    # Admin emails auto-approve after verify
    issued_key = None
    if user.get("role") == "admin":
        user = store.set_status(user_id, STATUS_ACTIVE, approved_by="system")
        issued_key = store.issue_api_key(user_id)

    accept = request.headers.get("accept") or ""
    if "text/html" in accept:
        msg = (
            "Email verified. An admin will approve your account shortly."
            if not issued_key
            else "Email verified. Your admin account is active."
        )
        html = f"""<!doctype html><html><body style="font-family:system-ui;background:#09090b;color:#fff;padding:2rem">
        <h1>Nimmakai</h1><p>{msg}</p>
        <p><a style="color:#a78bfa" href="/dashboard">Open dashboard</a></p>
        </body></html>"""
        return HTMLResponse(html)

    payload: dict[str, Any] = {
        "ok": True,
        "user": store.public_user(user or {}),
        "message": (
            "Verified. Waiting for admin approval."
            if user and user["status"] == STATUS_PENDING
            else "Verified and activated."
        ),
    }
    if issued_key:
        payload["api_key"] = issued_key["api_key"]
        payload["key_prefix"] = issued_key["key_prefix"]
    return JSONResponse(payload)


@router.post("/auth/login")
async def login(request: Request) -> JSONResponse:
    store = _store(request)
    if store is None:
        return JSONResponse(
            {"error": {"message": "Accounts not initialized", "code": "unavailable"}},
            status_code=503,
        )
    settings = _settings(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"message": "Invalid JSON", "code": "invalid_json"}},
            status_code=400,
        )
    email = str(body.get("email") or "")
    password = str(body.get("password") or "")
    user = store.authenticate(email, password)
    if not user:
        return JSONResponse(
            {"error": {"message": "Invalid email or password", "code": "invalid_credentials"}},
            status_code=401,
        )
    raw = store.create_session(
        user["id"],
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    keys = store.list_keys_for_user(user["id"])
    active_prefix = next(
        (k["key_prefix"] for k in keys if k.get("revoked_at") is None), None
    )
    resp = JSONResponse(
        {
            "ok": True,
            "user": store.public_user(user),
            "key_prefix": active_prefix,
        }
    )
    cookie = getattr(settings, "session_cookie_name", "nk_session") or "nk_session"
    resp.set_cookie(cookie, raw, **_cookie_kwargs(settings))
    return resp


@router.post("/auth/logout")
async def logout(request: Request) -> JSONResponse:
    store = _store(request)
    settings = _settings(request)
    cookie = getattr(settings, "session_cookie_name", "nk_session") or "nk_session"
    raw = request.cookies.get(cookie)
    if store is not None:
        store.delete_session(raw)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(cookie, path="/")
    return resp


@router.get("/auth/me")
async def me(request: Request) -> JSONResponse:
    store = _store(request)
    settings = _settings(request)
    # Allow unauthenticated probe
    try:
        ctx = resolve_auth(request, settings)
    except Exception:
        return JSONResponse({"authenticated": False})
    if ctx.via == "none" or (not ctx.user_id and not ctx.is_admin):
        # legacy admin with only proxy key
        if ctx.is_admin and ctx.via == "legacy_proxy":
            return JSONResponse(
                {
                    "authenticated": True,
                    "user": {
                        "id": None,
                        "email": None,
                        "role": "admin",
                        "status": "active",
                    },
                    "is_admin": True,
                    "via": ctx.via,
                    "keys": [],
                }
            )
        return JSONResponse({"authenticated": False})

    user = store.get_user(ctx.user_id) if store and ctx.user_id else None
    keys = store.list_keys_for_user(ctx.user_id) if store and ctx.user_id else []
    return JSONResponse(
        {
            "authenticated": True,
            "user": store.public_user(user) if store and user else {
                "id": ctx.user_id,
                "email": ctx.email,
                "role": ctx.role,
                "status": ctx.status,
            },
            "is_admin": ctx.is_admin,
            "via": ctx.via,
            "keys": [
                {
                    "id": k["id"],
                    "key_prefix": k["key_prefix"],
                    "name": k["name"],
                    "created_at": k["created_at"],
                    "revoked_at": k["revoked_at"],
                    "last_used_at": k["last_used_at"],
                }
                for k in keys
            ],
        }
    )


@router.post("/auth/keys/rotate")
async def rotate_key(request: Request) -> JSONResponse:
    store = _store(request)
    settings = _settings(request)
    if store is None:
        return JSONResponse(
            {"error": {"message": "Accounts not initialized"}}, status_code=503
        )
    ctx = resolve_auth(request, settings)
    if not ctx.user_id or ctx.status != STATUS_ACTIVE:
        return JSONResponse(
            {"error": {"message": "Active account required", "code": "account_not_active"}},
            status_code=403,
        )
    issued = store.issue_api_key(ctx.user_id)
    return JSONResponse(
        {
            "ok": True,
            "api_key": issued["api_key"],
            "key_prefix": issued["key_prefix"],
            "message": "Copy this key now — it will not be shown again.",
        }
    )


# ── Admin user management ───────────────────────────────────────────


@router.get("/admin/users")
async def admin_list_users(request: Request) -> JSONResponse:
    store = _store(request)
    settings = _settings(request)
    require_admin(request, settings)
    if store is None:
        return JSONResponse({"users": []})
    status_f = request.query_params.get("status") or None
    users = store.list_users(status=status_f)
    return JSONResponse({"users": users})


@router.post("/admin/users/{user_id}/approve")
async def admin_approve_user(user_id: str, request: Request) -> JSONResponse:
    store = _store(request)
    settings = _settings(request)
    admin = require_admin(request, settings)
    if store is None:
        return JSONResponse(
            {"error": {"message": "Accounts not initialized"}}, status_code=503
        )
    result = store.approve_and_issue_key(
        user_id,
        approved_by=admin.email or admin.user_id or "admin",
    )
    if not result.get("ok"):
        err = result.get("error")
        if err == "not_found":
            return JSONResponse(
                {"error": {"message": "Not found", "code": "not_found"}},
                status_code=404,
            )
        return JSONResponse(
            {
                "error": {
                    "message": f"Cannot approve user in status={result.get('status')}",
                    "code": "invalid_status",
                }
            },
            status_code=400,
        )
    user = result["user"]
    if result.get("already_active"):
        return JSONResponse(
            {
                "ok": True,
                "user": store.public_user(user),
                "already_active": True,
                "keys": result.get("keys") or [],
            }
        )
    issued_key = result["api_key"]
    # Notify via email (stub or SMTP)
    if user and user.get("email") and issued_key:
        sender = get_email_sender(
            getattr(settings, "email_backend", "stub") or "stub",
            settings=settings,
        )
        sender.send(
            OutboundEmail(
                to=user["email"],
                subject="Your Nimmakai account was approved",
                text=(
                    "Your account is active.\n\n"
                    f"API key (save now):\n{issued_key}\n\n"
                    "Use it as Authorization: Bearer <key> with the gateway.\n"
                    f"Dashboard: {_base_url(request, settings)}/dashboard\n"
                ),
            )
        )
    return JSONResponse(
        {
            "ok": True,
            "user": store.public_user(user or {}),
            "api_key": issued_key,
            "key_prefix": result.get("key_prefix"),
            "message": "User approved. API key issued (also emailed via stub).",
        }
    )


@router.post("/admin/users/{user_id}/reject")
async def admin_reject_user(user_id: str, request: Request) -> JSONResponse:
    store = _store(request)
    settings = _settings(request)
    require_admin(request, settings)
    if store is None:
        return JSONResponse(
            {"error": {"message": "Accounts not initialized"}}, status_code=503
        )
    user = store.set_status(user_id, STATUS_REJECTED)
    if not user:
        return JSONResponse(
            {"error": {"message": "Not found"}}, status_code=404
        )
    store.delete_sessions_for_user(user_id)
    return JSONResponse({"ok": True, "user": store.public_user(user)})


@router.post("/admin/users/{user_id}/suspend")
async def admin_suspend_user(user_id: str, request: Request) -> JSONResponse:
    store = _store(request)
    settings = _settings(request)
    require_admin(request, settings)
    if store is None:
        return JSONResponse(
            {"error": {"message": "Accounts not initialized"}}, status_code=503
        )
    user = store.set_status(user_id, STATUS_SUSPENDED)
    if not user:
        return JSONResponse(
            {"error": {"message": "Not found"}}, status_code=404
        )
    store.delete_sessions_for_user(user_id)
    return JSONResponse({"ok": True, "user": store.public_user(user)})
