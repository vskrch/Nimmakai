"""Signup, verify, login, logout, me, API keys + admin user approval."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from potato.accounts.email import OutboundEmail, get_email_sender
from potato.accounts.store import (
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SUSPENDED,
    AccountStore,
)
from potato.auth import require_admin, resolve_auth
from potato.config import get_settings

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
            subject="Verify your Potato account",
            text=(
                f"Welcome to Potato.\n\n"
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
    _settings(request)
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
    if "text/html" in accept and "application/json" not in accept:
        msg = (
            "Email verified successfully. An administrator will review and approve your account."
            if not issued_key
            else "Email verified successfully. Your admin account is active."
        )
        html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Potato Gateway — Email Verified</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #09090b; color: #f4f4f5; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 1rem; box-sizing: border-box; }}
    .card {{ background: #121215; border: 1px solid rgba(255,255,255,0.08); border-radius: 1.25rem; padding: 2.5rem; max-width: 440px; width: 100%; text-align: center; box-shadow: 0 20px 40px rgba(0,0,0,0.6); }}
    .icon {{ width: 56px; height: 56px; background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.3); border-radius: 1rem; display: flex; align-items: center; justify-content: center; margin: 0 auto 1.5rem; color: #10b981; font-size: 28px; font-weight: bold; }}
    h1 {{ font-size: 1.25rem; font-weight: 700; margin: 0 0 0.5rem; color: #fff; }}
    p {{ font-size: 0.875rem; color: #a1a1aa; line-height: 1.5; margin: 0 0 1.75rem; }}
    .btn {{ display: inline-block; background: #8b5cf6; color: #fff; text-decoration: none; padding: 0.75rem 1.5rem; border-radius: 0.75rem; font-size: 0.875rem; font-weight: 600; transition: background 0.2s; }}
    .btn:hover {{ background: #7c3aed; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✓</div>
    <h1>Email Address Verified</h1>
    <p>{msg}</p>
    <a class="btn" href="/dashboard">Open Gateway Dashboard</a>
  </div>
</body>
</html>"""
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


@router.post("/auth/resend-verification")
async def resend_verification(request: Request) -> JSONResponse:
    """Resend email verification link to an unverified user account."""
    store = _store(request)
    settings = _settings(request)
    if store is None:
        return JSONResponse({"error": {"message": "Accounts not initialized"}}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        body = {}
    email = str(body.get("email") or "").strip().lower()
    if not email:
        ctx = resolve_auth(request, settings)
        if ctx.user_id:
            u = store.get_user(ctx.user_id)
            if u:
                email = u["email"]
    if not email:
        return JSONResponse({"error": {"message": "Email is required", "code": "email_required"}}, status_code=400)
    user = store.get_user_by_email(email)
    if not user:
        return JSONResponse({"ok": True, "message": "If that email exists and is unverified, a new link has been sent."})
    if user["status"] != STATUS_UNVERIFIED:
        return JSONResponse({"ok": True, "message": "Account is already verified."})

    token = store.create_verify_token(user["id"])
    verify_url = f"{_base_url(request, settings)}/auth/verify?token={token}"
    sender = get_email_sender(
        getattr(settings, "email_backend", "stub") or "stub",
        settings=settings,
    )
    result = sender.send(
        OutboundEmail(
            to=email,
            subject="Verify your Potato account",
            text=(
                f"Welcome to Potato.\n\n"
                f"Verify your email:\n{verify_url}\n\n"
                f"After verification an admin must approve your account "
                f"before an API key is issued.\n"
            ),
        )
    )
    payload: dict[str, Any] = {
        "ok": True,
        "message": "Verification link sent. Check your email.",
    }
    if (getattr(settings, "email_backend", "stub") or "stub") == "stub":
        payload["verify_url"] = verify_url
        payload["email_preview"] = result
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
    active_prefix = next((k["key_prefix"] for k in keys if k.get("revoked_at") is None), None)
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
            "user": store.public_user(user)
            if store and user
            else {
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
        return JSONResponse({"error": {"message": "Accounts not initialized"}}, status_code=503)
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
        return JSONResponse({"error": {"message": "Accounts not initialized"}}, status_code=503)
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
                subject="Your Potato account was approved",
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
        return JSONResponse({"error": {"message": "Accounts not initialized"}}, status_code=503)
    user = store.set_status(user_id, STATUS_REJECTED)
    if not user:
        return JSONResponse({"error": {"message": "Not found"}}, status_code=404)
    store.delete_sessions_for_user(user_id)
    return JSONResponse({"ok": True, "user": store.public_user(user)})


@router.post("/admin/users/{user_id}/suspend")
async def admin_suspend_user(user_id: str, request: Request) -> JSONResponse:
    store = _store(request)
    settings = _settings(request)
    require_admin(request, settings)
    if store is None:
        return JSONResponse({"error": {"message": "Accounts not initialized"}}, status_code=503)
    user = store.set_status(user_id, STATUS_SUSPENDED)
    if not user:
        return JSONResponse({"error": {"message": "Not found"}}, status_code=404)
    store.delete_sessions_for_user(user_id)
    return JSONResponse({"ok": True, "user": store.public_user(user)})


@router.post("/admin/users/{user_id}/rotate-key")
async def admin_rotate_user_key(user_id: str, request: Request) -> JSONResponse:
    """Admin endpoint to rotate an API key for any user account."""
    store = _store(request)
    settings = _settings(request)
    require_admin(request, settings)
    if store is None:
        return JSONResponse({"error": {"message": "Accounts not initialized"}}, status_code=503)
    user = store.get_user(user_id)
    if not user:
        return JSONResponse({"error": {"message": "User not found", "code": "not_found"}}, status_code=404)
    issued = store.issue_api_key(user_id, name="rotated_by_admin")
    return JSONResponse(
        {
            "ok": True,
            "user": store.public_user(user),
            "api_key": issued["api_key"],
            "key_prefix": issued["key_prefix"],
            "message": "User API key rotated by admin. Copy new key now — it will not be shown again.",
        }
    )


@router.post("/admin/users/{user_id}/role")
async def admin_set_user_role(user_id: str, request: Request) -> JSONResponse:
    """Admin endpoint to promote or demote a user role (admin vs user)."""
    store = _store(request)
    settings = _settings(request)
    require_admin(request, settings)
    if store is None:
        return JSONResponse({"error": {"message": "Accounts not initialized"}}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "Invalid JSON"}}, status_code=400)
    role = str(body.get("role") or "").strip().lower()
    if role not in {"admin", "user"}:
        return JSONResponse({"error": {"message": "Role must be 'admin' or 'user'", "code": "invalid_role"}}, status_code=400)
    user = store.set_role(user_id, role)
    if not user:
        return JSONResponse({"error": {"message": "User not found"}}, status_code=404)
    return JSONResponse({"ok": True, "user": store.public_user(user)})


@router.get("/admin/users/{user_id}/keys")
async def admin_list_user_keys(user_id: str, request: Request) -> JSONResponse:
    """Admin endpoint to view all keys for a specific user."""
    store = _store(request)
    settings = _settings(request)
    require_admin(request, settings)
    if store is None:
        return JSONResponse({"error": {"message": "Accounts not initialized"}}, status_code=503)
    keys = store.list_api_keys(user_id)
    return JSONResponse({"user_id": user_id, "keys": keys})


@router.post("/admin/users/{user_id}/keys/{key_id}/revoke")
async def admin_revoke_user_key(user_id: str, key_id: str, request: Request) -> JSONResponse:
    """Admin endpoint to revoke a specific API key for a user."""
    store = _store(request)
    settings = _settings(request)
    require_admin(request, settings)
    if store is None:
        return JSONResponse({"error": {"message": "Accounts not initialized"}}, status_code=503)
    ok = store.revoke_api_key(user_id, key_id)
    if not ok:
        return JSONResponse({"error": {"message": "Key not found or already revoked"}}, status_code=404)
    return JSONResponse({"ok": True, "message": f"Key {key_id} revoked"})
