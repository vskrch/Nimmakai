"""SQLite DDL for analytics traces, spans, and rollups."""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

ANALYTICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id    TEXT NOT NULL,
    created_at  REAL NOT NULL,

    method      TEXT NOT NULL DEFAULT 'POST',
    path        TEXT NOT NULL,
    client_ip   TEXT,
    api_key     TEXT,
    user_id     TEXT,
    user_agent  TEXT,

    model_requested TEXT,
    intent          TEXT,
    intent_confidence REAL,
    intent_rule_id    TEXT,
    route_mode        TEXT,

    model_routed    TEXT,
    provider_id     TEXT,
    chain_json      TEXT,
    fallback_index  INTEGER DEFAULT 0,
    chain_length    INTEGER DEFAULT 1,

    status_code   INTEGER,
    success       INTEGER DEFAULT 1,
    error_message TEXT,
    is_stream     INTEGER DEFAULT 0,

    duration_ms     REAL,
    classify_ms     REAL,
    route_ms        REAL,
    upstream_ttft_ms REAL,
    upstream_total_ms REAL,

    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cached_tokens     INTEGER DEFAULT 0,
    total_tokens      INTEGER DEFAULT 0,

    estimated_cost_usd REAL DEFAULT 0.0,

    message_count   INTEGER DEFAULT 0,
    has_tools       INTEGER DEFAULT 0,
    has_images      INTEGER DEFAULT 0,
    tool_count      INTEGER DEFAULT 0,
    char_length     INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_traces_created_at ON traces(created_at);
CREATE INDEX IF NOT EXISTS idx_traces_trace_id ON traces(trace_id);
CREATE INDEX IF NOT EXISTS idx_traces_model ON traces(model_routed);
CREATE INDEX IF NOT EXISTS idx_traces_provider ON traces(provider_id);
CREATE INDEX IF NOT EXISTS idx_traces_intent ON traces(intent);
CREATE INDEX IF NOT EXISTS idx_traces_api_key ON traces(api_key);
CREATE INDEX IF NOT EXISTS idx_traces_user_id ON traces(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status_code);
CREATE INDEX IF NOT EXISTS idx_traces_success ON traces(success, created_at);
CREATE INDEX IF NOT EXISTS idx_traces_recent ON traces(created_at DESC, success);

CREATE TABLE IF NOT EXISTS trace_spans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id    TEXT NOT NULL,
    span_type   TEXT NOT NULL,
    model_id    TEXT,
    provider_id TEXT,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    duration_ms REAL,
    status_code INTEGER,
    success     INTEGER DEFAULT 1,
    error_message TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_spans_trace ON trace_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_type ON trace_spans(span_type, started_at);

CREATE TABLE IF NOT EXISTS trace_rollups (
    bucket_ts     INTEGER NOT NULL,
    intent        TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    provider      TEXT NOT NULL DEFAULT '',
    api_key       TEXT NOT NULL DEFAULT '',

    request_count   INTEGER DEFAULT 0,
    success_count   INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    stream_count    INTEGER DEFAULT 0,
    fallback_count  INTEGER DEFAULT 0,

    prompt_tokens_sum     INTEGER DEFAULT 0,
    completion_tokens_sum INTEGER DEFAULT 0,

    duration_sum_ms     REAL DEFAULT 0,
    duration_max_ms     REAL DEFAULT 0,
    ttft_sum_ms         REAL DEFAULT 0,

    cost_sum_usd        REAL DEFAULT 0.0,

    PRIMARY KEY (bucket_ts, intent, model, provider, api_key)
);

CREATE INDEX IF NOT EXISTS idx_rollups_bucket ON trace_rollups(bucket_ts);
CREATE INDEX IF NOT EXISTS idx_rollups_model ON trace_rollups(model, bucket_ts);

CREATE TABLE IF NOT EXISTS cost_overrides (
    model_id TEXT PRIMARY KEY,
    input_per_m REAL NOT NULL DEFAULT 0.0,
    output_per_m REAL NOT NULL DEFAULT 0.0,
    updated_at REAL NOT NULL DEFAULT 0
);
"""


def migrate_analytics(conn: sqlite3.Connection) -> None:
    """Apply analytics schema idempotently on an existing connection."""
    conn.executescript(ANALYTICS_SCHEMA)
    logger.debug("analytics schema migrated")
