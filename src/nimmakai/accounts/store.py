"""Account store: users, API keys, sessions, email tokens."""

from __future__ import annotations

import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from nimmakai.accounts.crypto import (
    hash_password,
    hash_token,
    new_api_key,
    new_email_token,
    new_id,
    new_session_token,
    verify_password,
)

if TYPE_CHECKING:
    from nimmakai.catalog.db import NimmakaiDB

logger = logging.getLogger(__name__)

STATUS_UNVERIFIED = "unverified"
STATUS_PENDING = "pending_approval"
STATUS_ACTIVE = "active"
STATUS_REJECTED = "rejected"
STATUS_SUSPENDED = "suspended"

VERIFY_TTL_SECONDS = 48 * 3600
SESSION_TTL_SECONDS = 30 * 24 * 3600


class AccountStore:
    def __init__(self, db: NimmakaiDB) -> None:
        self._db = db

    def create_user(
        self,
        email: str,
        password: str,
        *,
        role: str = "user",
        status: str = STATUS_UNVERIFIED,
    ) -> dict[str, Any]:
        uid = new_id("usr")
        now = time.time()
        email_n = email.strip().lower()
        with self._db._lock:
            self._db._conn.execute(
                """
                INSERT INTO users (id, email, password_hash, role, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (uid, email_n, hash_password(password), role, status, now),
            )
        return self.get_user(uid)  # type: ignore[return-value]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._db._lock:
            row = self._db._conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._db._lock:
            row = self._db._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        return dict(row) if row else None

    def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        user = self.get_user_by_email(email)
        if not user:
            return None
        if not verify_password(password, user["password_hash"]):
            return None
        return user

    def set_status(
        self,
        user_id: str,
        status: str,
        *,
        approved_by: str | None = None,
    ) -> dict[str, Any] | None:
        now = time.time()
        with self._db._lock:
            if status == STATUS_ACTIVE:
                self._db._conn.execute(
                    """
                    UPDATE users SET status = ?, approved_at = ?, approved_by = ?
                    WHERE id = ?
                    """,
                    (status, now, approved_by, user_id),
                )
            else:
                self._db._conn.execute(
                    "UPDATE users SET status = ? WHERE id = ?",
                    (status, user_id),
                )
        return self.get_user(user_id)

    def mark_verified(self, user_id: str) -> dict[str, Any] | None:
        now = time.time()
        with self._db._lock:
            self._db._conn.execute(
                """
                UPDATE users SET status = ?, verified_at = ?
                WHERE id = ? AND status = ?
                """,
                (STATUS_PENDING, now, user_id, STATUS_UNVERIFIED),
            )
        return self.get_user(user_id)

    def list_users(
        self, *, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        with self._db._lock:
            if status:
                rows = self._db._conn.execute(
                    """
                    SELECT id, email, role, status, created_at, verified_at,
                           approved_at, approved_by
                    FROM users WHERE status = ?
                    ORDER BY created_at DESC LIMIT ? OFFSET ?
                    """,
                    (status, limit, offset),
                ).fetchall()
            else:
                rows = self._db._conn.execute(
                    """
                    SELECT id, email, role, status, created_at, verified_at,
                           approved_at, approved_by
                    FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
        return [dict(r) for r in rows]

    def public_user(self, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user["id"],
            "email": user["email"],
            "role": user["role"],
            "status": user["status"],
            "created_at": user["created_at"],
            "verified_at": user.get("verified_at"),
            "approved_at": user.get("approved_at"),
        }

    def create_verify_token(self, user_id: str) -> str:
        raw, th = new_email_token()
        now = time.time()
        with self._db._lock:
            self._db._conn.execute(
                """
                INSERT INTO email_tokens
                    (id, user_id, purpose, token_hash, created_at, expires_at)
                VALUES (?, ?, 'verify_email', ?, ?, ?)
                """,
                (new_id("tok"), user_id, th, now, now + VERIFY_TTL_SECONDS),
            )
        return raw

    def consume_verify_token(self, raw: str) -> str | None:
        th = hash_token(raw)
        now = time.time()
        with self._db._lock:
            row = self._db._conn.execute(
                """
                SELECT * FROM email_tokens
                WHERE token_hash = ? AND purpose = 'verify_email' AND used_at IS NULL
                """,
                (th,),
            ).fetchone()
            if not row:
                return None
            if float(row["expires_at"]) < now:
                return None
            self._db._conn.execute(
                "UPDATE email_tokens SET used_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            return str(row["user_id"])

    def create_session(
        self,
        user_id: str,
        *,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> str:
        raw, th = new_session_token()
        now = time.time()
        with self._db._lock:
            self._db._conn.execute(
                """
                INSERT INTO sessions
                    (id, user_id, token_hash, created_at, expires_at, user_agent, ip)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("ses"),
                    user_id,
                    th,
                    now,
                    now + SESSION_TTL_SECONDS,
                    (user_agent or "")[:200] or None,
                    ip,
                ),
            )
        return raw

    def resolve_session(self, raw: str | None) -> dict[str, Any] | None:
        if not raw:
            return None
        th = hash_token(raw)
        now = time.time()
        with self._db._lock:
            row = self._db._conn.execute(
                """
                SELECT s.id AS session_id, s.expires_at, u.*
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ?
                """,
                (th,),
            ).fetchone()
            if not row:
                return None
            if float(row["expires_at"]) < now:
                return None
            return dict(row)

    def delete_session(self, raw: str | None) -> None:
        if not raw:
            return
        th = hash_token(raw)
        with self._db._lock:
            self._db._conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (th,)
            )

    def delete_sessions_for_user(self, user_id: str) -> int:
        """Revoke all dashboard sessions for a user (e.g. on suspend/reject)."""
        with self._db._lock:
            cur = self._db._conn.execute(
                "DELETE FROM sessions WHERE user_id = ?", (user_id,)
            )
            return int(cur.rowcount or 0)

    def issue_api_key(self, user_id: str, *, name: str = "default") -> dict[str, Any]:
        raw, prefix, kh = new_api_key()
        kid = new_id("key")
        now = time.time()
        with self._db._lock:
            self._db._conn.execute(
                """
                UPDATE api_keys SET revoked_at = ?
                WHERE user_id = ? AND revoked_at IS NULL
                """,
                (now, user_id),
            )
            self._db._conn.execute(
                """
                INSERT INTO api_keys
                    (id, user_id, key_prefix, key_hash, name, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (kid, user_id, prefix, kh, name, now),
            )
        return {
            "id": kid,
            "user_id": user_id,
            "key_prefix": prefix,
            "api_key": raw,
            "name": name,
            "created_at": now,
        }

    def resolve_api_key(self, raw: str | None) -> dict[str, Any] | None:
        if not raw or not raw.startswith("sk-nk-"):
            return None
        kh = hash_token(raw)
        with self._db._lock:
            row = self._db._conn.execute(
                """
                SELECT k.id AS key_id, k.key_prefix, k.revoked_at, u.*
                FROM api_keys k JOIN users u ON u.id = k.user_id
                WHERE k.key_hash = ?
                """,
                (kh,),
            ).fetchone()
            if not row:
                return None
            if row["revoked_at"] is not None:
                return None
            if row["status"] != STATUS_ACTIVE:
                return None
            # Write-behind: batch last_used_at updates to avoid blocking the
            # event loop on every authenticated request. Flush every 60s.
            self._dirty_keys.add(row["key_id"])
            if not getattr(self, "_flush_scheduled", False):
                self._schedule_flush()
            return dict(row)

    _dirty_keys: set[str] = set()
    _flush_scheduled: bool = False

    def _schedule_flush(self) -> None:
        try:
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._flush_dirty_keys()
            return
        self._flush_scheduled = True

        async def _flush_bg() -> None:
            try:
                await asyncio.to_thread(self._flush_dirty_keys)
            finally:
                self._flush_scheduled = False

        loop.create_task(_flush_bg())

    def _flush_dirty_keys(self) -> None:
        if not self._dirty_keys:
            return
        now = time.time()
        keys = set(self._dirty_keys)
        self._dirty_keys.clear()
        with self._db._lock:
            for key_id in keys:
                try:
                    self._db._conn.execute(
                        "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                        (now, key_id),
                    )
                except Exception:
                    logger.debug("failed to update last_used_at for %s", key_id)

    def list_keys_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self._db._lock:
            rows = self._db._conn.execute(
                """
                SELECT id, key_prefix, name, created_at, revoked_at, last_used_at
                FROM api_keys WHERE user_id = ? ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def approve_and_issue_key(
        self,
        user_id: str,
        *,
        approved_by: str | None = None,
        name: str = "default",
    ) -> dict[str, Any]:
        """
        Atomically activate a pending/rejected/suspended user and issue one API key.

        Concurrent approvers: only the first UPDATE wins; losers get
        ``already_active=True`` without a newly issued plaintext key.
        """
        now = time.time()
        with self._db._lock:
            self._db._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._db._conn.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                if not row:
                    self._db._conn.execute("ROLLBACK")
                    return {"ok": False, "error": "not_found"}
                user = dict(row)
                status = user["status"]
                if status == STATUS_ACTIVE:
                    keys = self._db._conn.execute(
                        """
                        SELECT id, key_prefix, name, created_at, revoked_at
                        FROM api_keys
                        WHERE user_id = ? AND revoked_at IS NULL
                        ORDER BY created_at DESC
                        """,
                        (user_id,),
                    ).fetchall()
                    self._db._conn.execute("COMMIT")
                    return {
                        "ok": True,
                        "already_active": True,
                        "user": user,
                        "keys": [dict(k) for k in keys],
                        "api_key": None,
                    }
                if status not in {
                    STATUS_PENDING,
                    STATUS_REJECTED,
                    STATUS_SUSPENDED,
                }:
                    self._db._conn.execute("ROLLBACK")
                    return {
                        "ok": False,
                        "error": "invalid_status",
                        "status": status,
                    }
                cur = self._db._conn.execute(
                    """
                    UPDATE users SET status = ?, approved_at = ?, approved_by = ?
                    WHERE id = ? AND status IN (?, ?, ?)
                    """,
                    (
                        STATUS_ACTIVE,
                        now,
                        approved_by,
                        user_id,
                        STATUS_PENDING,
                        STATUS_REJECTED,
                        STATUS_SUSPENDED,
                    ),
                )
                if cur.rowcount != 1:
                    # Lost the race — another approver won
                    keys = self._db._conn.execute(
                        """
                        SELECT id, key_prefix, name, created_at, revoked_at
                        FROM api_keys
                        WHERE user_id = ? AND revoked_at IS NULL
                        ORDER BY created_at DESC
                        """,
                        (user_id,),
                    ).fetchall()
                    user2 = self._db._conn.execute(
                        "SELECT * FROM users WHERE id = ?", (user_id,)
                    ).fetchone()
                    self._db._conn.execute("COMMIT")
                    return {
                        "ok": True,
                        "already_active": True,
                        "user": dict(user2) if user2 else user,
                        "keys": [dict(k) for k in keys],
                        "api_key": None,
                    }
                # Issue key inside same transaction
                raw, prefix, kh = new_api_key()
                kid = new_id("key")
                self._db._conn.execute(
                    """
                    UPDATE api_keys SET revoked_at = ?
                    WHERE user_id = ? AND revoked_at IS NULL
                    """,
                    (now, user_id),
                )
                self._db._conn.execute(
                    """
                    INSERT INTO api_keys
                        (id, user_id, key_prefix, key_hash, name, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (kid, user_id, prefix, kh, name, now),
                )
                user_out = self._db._conn.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                self._db._conn.execute("COMMIT")
                return {
                    "ok": True,
                    "already_active": False,
                    "user": dict(user_out) if user_out else user,
                    "api_key": raw,
                    "key_prefix": prefix,
                    "key_id": kid,
                }
            except Exception:
                with suppress(Exception):
                    self._db._conn.execute("ROLLBACK")
                raise
