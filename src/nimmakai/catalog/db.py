"""SQLite persistence for providers, preferences, and related gateway state.

Uses the stdlib ``sqlite3`` module (no extra deps). Default path:
``.nimmakai/nimmakai.db`` — set ``SQLITE_PATH`` / ``Settings.sqlite_path``.

Free-provider *templates* (base URLs) live in ``presets.py``; once you add
keys in the admin UI they are stored here so they survive restarts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    base_url TEXT NOT NULL DEFAULT '',
    api_keys_json TEXT NOT NULL DEFAULT '[]',
    api_keys_env TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    rpm_limit REAL NOT NULL DEFAULT 40,
    rpd_limit INTEGER NOT NULL DEFAULT 2000,
    max_in_flight_per_key INTEGER NOT NULL DEFAULT 3,
    api_style TEXT NOT NULL DEFAULT 'openai',
    builtin INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS preferences (
    intent TEXT PRIMARY KEY,
    chain_json TEXT NOT NULL DEFAULT '[]',
    strict INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


class NimmakaiDB:
    """Thread-safe thin wrapper around a single SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we use explicit BEGIN
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
        logger.info("sqlite ready at %s", self.path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── meta ────────────────────────────────────────────────────────

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ── providers ───────────────────────────────────────────────────

    def list_providers(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM providers ORDER BY id"
            ).fetchall()
        return [self._provider_row(r) for r in rows]

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM providers WHERE id = ?", (provider_id.lower(),)
            ).fetchone()
        return self._provider_row(row) if row else None

    def upsert_provider(self, data: dict[str, Any]) -> None:
        pid = str(data["id"]).strip().lower()
        keys = data.get("api_keys") or []
        if isinstance(keys, str):
            keys = [k.strip() for k in keys.split(",") if k.strip()]
        payload = (
            pid,
            str(data.get("name") or pid),
            str(data.get("base_url") or "").rstrip("/"),
            json.dumps(list(keys)),
            data.get("api_keys_env"),
            1 if data.get("enabled", True) else 0,
            float(data.get("rpm_limit", 40)),
            int(data.get("rpd_limit", 2000)),
            int(data.get("max_in_flight_per_key", 3)),
            str(data.get("api_style") or "openai"),
            1 if data.get("builtin") else 0,
            float(data.get("updated_at") or time.time()),
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO providers (
                    id, name, base_url, api_keys_json, api_keys_env, enabled,
                    rpm_limit, rpd_limit, max_in_flight_per_key, api_style,
                    builtin, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    base_url = excluded.base_url,
                    api_keys_json = excluded.api_keys_json,
                    api_keys_env = excluded.api_keys_env,
                    enabled = excluded.enabled,
                    rpm_limit = excluded.rpm_limit,
                    rpd_limit = excluded.rpd_limit,
                    max_in_flight_per_key = excluded.max_in_flight_per_key,
                    api_style = excluded.api_style,
                    builtin = excluded.builtin,
                    updated_at = excluded.updated_at
                """,
                payload,
            )

    def delete_provider(self, provider_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM providers WHERE id = ?", (provider_id.lower(),)
            )
            return cur.rowcount > 0

    def replace_all_providers(self, providers: list[dict[str, Any]]) -> None:
        """Atomic rewrite used after bulk load / migration."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM providers")
                for data in providers:
                    pid = str(data["id"]).strip().lower()
                    keys = data.get("api_keys") or []
                    if isinstance(keys, str):
                        keys = [k.strip() for k in keys.split(",") if k.strip()]
                    self._conn.execute(
                        """
                        INSERT INTO providers (
                            id, name, base_url, api_keys_json, api_keys_env, enabled,
                            rpm_limit, rpd_limit, max_in_flight_per_key, api_style,
                            builtin, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            pid,
                            str(data.get("name") or pid),
                            str(data.get("base_url") or "").rstrip("/"),
                            json.dumps(list(keys)),
                            data.get("api_keys_env"),
                            1 if data.get("enabled", True) else 0,
                            float(data.get("rpm_limit", 40)),
                            int(data.get("rpd_limit", 2000)),
                            int(data.get("max_in_flight_per_key", 3)),
                            str(data.get("api_style") or "openai"),
                            1 if data.get("builtin") else 0,
                            float(data.get("updated_at") or time.time()),
                        ),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    @staticmethod
    def _provider_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            keys = json.loads(row["api_keys_json"] or "[]")
        except json.JSONDecodeError:
            keys = []
        if not isinstance(keys, list):
            keys = []
        return {
            "id": row["id"],
            "name": row["name"],
            "base_url": row["base_url"],
            "api_keys": [str(k) for k in keys],
            "api_keys_env": row["api_keys_env"],
            "enabled": bool(row["enabled"]),
            "rpm_limit": float(row["rpm_limit"]),
            "rpd_limit": int(row["rpd_limit"]),
            "max_in_flight_per_key": int(row["max_in_flight_per_key"]),
            "api_style": row["api_style"] or "openai",
            "builtin": bool(row["builtin"]),
            "updated_at": float(row["updated_at"] or 0),
        }

    # ── preferences ─────────────────────────────────────────────────

    def list_preferences(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM preferences ORDER BY intent"
            ).fetchall()
        out = []
        for row in rows:
            try:
                chain = json.loads(row["chain_json"] or "[]")
            except json.JSONDecodeError:
                chain = []
            out.append(
                {
                    "intent": row["intent"],
                    "chain": list(chain) if isinstance(chain, list) else [],
                    "strict": bool(row["strict"]),
                    "note": row["note"] or "",
                    "updated_at": float(row["updated_at"] or 0),
                }
            )
        return out

    def upsert_preference(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO preferences (intent, chain_json, strict, note, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(intent) DO UPDATE SET
                    chain_json = excluded.chain_json,
                    strict = excluded.strict,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    str(data["intent"]),
                    json.dumps(list(data.get("chain") or [])),
                    1 if data.get("strict") else 0,
                    str(data.get("note") or ""),
                    float(data.get("updated_at") or time.time()),
                ),
            )

    def delete_preference(self, intent: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM preferences WHERE intent = ?", (intent,)
            )
            return cur.rowcount > 0

    def clear_preferences(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM preferences")


# Process-wide cache so ProviderStore + UserPreferences share one connection.
_DB_CACHE: dict[str, NimmakaiDB] = {}
_DB_LOCK = threading.Lock()


def get_db(path: str | Path) -> NimmakaiDB:
    key = str(Path(path).resolve())
    with _DB_LOCK:
        db = _DB_CACHE.get(key)
        if db is None:
            db = NimmakaiDB(path)
            _DB_CACHE[key] = db
        return db
