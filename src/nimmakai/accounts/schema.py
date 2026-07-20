"""SQLite DDL for multi-tenant users, API keys, sessions, email tokens."""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

ACCOUNTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'user',  -- user | admin
    status          TEXT NOT NULL DEFAULT 'unverified',
    -- unverified | pending_approval | active | rejected | suspended
    created_at      REAL NOT NULL,
    verified_at     REAL,
    approved_at     REAL,
    approved_by     TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS api_keys (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_prefix      TEXT NOT NULL,          -- sk-nk-xxxx (display)
    key_hash        TEXT NOT NULL UNIQUE,  -- sha256 hex
    name            TEXT NOT NULL DEFAULT 'default',
    created_at      REAL NOT NULL,
    revoked_at      REAL,
    last_used_at    REAL
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,
    created_at      REAL NOT NULL,
    expires_at      REAL NOT NULL,
    user_agent      TEXT,
    ip              TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_hash ON sessions(token_hash);

CREATE TABLE IF NOT EXISTS email_tokens (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose         TEXT NOT NULL,  -- verify_email | password_reset
    token_hash      TEXT NOT NULL UNIQUE,
    created_at      REAL NOT NULL,
    expires_at      REAL NOT NULL,
    used_at         REAL
);

CREATE INDEX IF NOT EXISTS idx_email_tokens_hash ON email_tokens(token_hash);
"""


def migrate_accounts(conn: sqlite3.Connection) -> None:
    conn.executescript(ACCOUNTS_SCHEMA)
    # analytics: user_id on traces (idempotent)
    cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(traces)").fetchall()
    }
    if cols and "user_id" not in cols:
        conn.execute("ALTER TABLE traces ADD COLUMN user_id TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_traces_user_id ON traces(user_id, created_at)"
        )
        logger.info("added traces.user_id column")
    logger.debug("accounts schema migrated")
