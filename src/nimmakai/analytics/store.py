"""Query helpers for analytics API (traces, timeseries, breakdowns, summary)."""

from __future__ import annotations

import json
import math
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nimmakai.catalog.db import NimmakaiDB

_INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
}

_SORT_COLS = {
    "created_at": "created_at",
    "duration_ms": "duration_ms",
    "total_tokens": "total_tokens",
    "estimated_cost_usd": "estimated_cost_usd",
}


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    if "chain_json" in d and d["chain_json"]:
        try:
            d["chain"] = json.loads(d["chain_json"])
        except (json.JSONDecodeError, TypeError):
            d["chain"] = []
    else:
        d["chain"] = []
    d.pop("chain_json", None)
    for bkey in ("success", "is_stream", "has_tools", "has_images"):
        if bkey in d and d[bkey] is not None:
            d[bkey] = bool(d[bkey])
    return d


def _span_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["success"] = bool(d.get("success", 1))
    meta = d.pop("metadata_json", None)
    if meta:
        try:
            d["metadata"] = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = {}
    else:
        d["metadata"] = {}
    return d


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


class AnalyticsStore:
    """Read-side analytics queries against NimmakaiDB."""

    def __init__(self, db: NimmakaiDB) -> None:
        self._db = db
        self._summary_cache: tuple[float, dict[str, Any]] | None = None
        self._summary_ttl = 10.0

    # ── traces ──────────────────────────────────────────────────────

    def list_traces(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        intent: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        api_key: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        since: float | None = None,
        until: float | None = None,
        search: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> dict[str, Any]:
        limit = max(1, min(500, int(limit)))
        offset = max(0, int(offset))
        sort_col = _SORT_COLS.get(sort, "created_at")
        order_sql = "ASC" if str(order).lower() == "asc" else "DESC"

        where: list[str] = []
        params: list[Any] = []

        if intent:
            where.append("intent = ?")
            params.append(intent)
        if model:
            where.append("model_routed = ?")
            params.append(model)
        if provider:
            where.append("provider_id = ?")
            params.append(provider)
        if api_key:
            where.append("api_key LIKE ?")
            params.append(f"%{api_key}%")
        if user_id:
            where.append("user_id = ?")
            params.append(user_id)
        if since is not None:
            where.append("created_at >= ?")
            params.append(float(since))
        if until is not None:
            where.append("created_at <= ?")
            params.append(float(until))
        if status == "success":
            where.append("success = 1")
        elif status == "error":
            where.append("success = 0")
        elif status == "4xx":
            where.append("status_code >= 400 AND status_code < 500")
        elif status == "5xx":
            where.append("status_code >= 500")
        if search:
            where.append(
                "(trace_id LIKE ? OR model_routed LIKE ? OR model_requested LIKE ?"
                " OR error_message LIKE ? OR intent LIKE ?)"
            )
            q = f"%{search}%"
            params.extend([q, q, q, q, q])

        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self._db._lock:
            total = self._db._conn.execute(
                f"SELECT COUNT(*) AS n FROM traces{clause}", params
            ).fetchone()["n"]
            rows = self._db._conn.execute(
                f"SELECT * FROM traces{clause} ORDER BY {sort_col} {order_sql}"
                f" LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()

        return {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "traces": [_row_to_dict(r) for r in rows],
        }

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with self._db._lock:
            row = self._db._conn.execute(
                "SELECT * FROM traces WHERE trace_id = ? ORDER BY id DESC LIMIT 1",
                (trace_id,),
            ).fetchone()
            if not row:
                return None
            spans = self._db._conn.execute(
                "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY started_at, id",
                (trace_id,),
            ).fetchall()
        out = _row_to_dict(row)
        out["spans"] = [_span_to_dict(s) for s in spans]
        return out

    def get_spans(self, trace_id: str) -> list[dict[str, Any]]:
        with self._db._lock:
            spans = self._db._conn.execute(
                "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY started_at, id",
                (trace_id,),
            ).fetchall()
        return [_span_to_dict(s) for s in spans]

    # ── timeseries ──────────────────────────────────────────────────

    def timeseries(
        self,
        metric: str,
        *,
        since: float | None = None,
        until: float | None = None,
        interval: str = "1m",
        intent: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        now = time.time()
        until = float(until if until is not None else now)
        since = float(since if since is not None else until - 3600)
        step = _INTERVAL_SECONDS.get(interval, 60)

        where = ["created_at >= ?", "created_at <= ?"]
        params: list[Any] = [since, until]
        if intent:
            where.append("intent = ?")
            params.append(intent)
        if model:
            where.append("model_routed = ?")
            params.append(model)
        if provider:
            where.append("provider_id = ?")
            params.append(provider)
        if user_id:
            where.append("user_id = ?")
            params.append(user_id)
        clause = " AND ".join(where)

        if metric == "requests":
            sql = f"""
                SELECT
                    CAST(created_at / ? AS INTEGER) * ? AS ts,
                    COUNT(*) AS requests,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS errors,
                    AVG(duration_ms) AS avg_ms
                FROM traces WHERE {clause}
                GROUP BY 1 ORDER BY 1
            """
            with self._db._lock:
                rows = self._db._conn.execute(sql, [step, step, *params]).fetchall()
            return [dict(r) for r in rows]

        if metric == "tokens":
            sql = f"""
                SELECT
                    CAST(created_at / ? AS INTEGER) * ? AS ts,
                    SUM(prompt_tokens) AS prompt_tokens,
                    SUM(completion_tokens) AS completion_tokens,
                    SUM(cached_tokens) AS cached_tokens,
                    SUM(total_tokens) AS total_tokens
                FROM traces WHERE {clause}
                GROUP BY 1 ORDER BY 1
            """
            with self._db._lock:
                rows = self._db._conn.execute(sql, [step, step, *params]).fetchall()
            return [dict(r) for r in rows]

        if metric == "cost":
            sql = f"""
                SELECT
                    CAST(created_at / ? AS INTEGER) * ? AS ts,
                    SUM(estimated_cost_usd) AS cost_usd,
                    COUNT(*) AS requests
                FROM traces WHERE {clause}
                GROUP BY 1 ORDER BY 1
            """
            with self._db._lock:
                rows = self._db._conn.execute(sql, [step, step, *params]).fetchall()
            return [dict(r) for r in rows]

        if metric == "ttft":
            sql = f"""
                SELECT
                    CAST(created_at / ? AS INTEGER) * ? AS ts,
                    AVG(upstream_ttft_ms) AS avg_ttft_ms,
                    COUNT(upstream_ttft_ms) AS samples
                FROM traces WHERE {clause} AND upstream_ttft_ms IS NOT NULL
                GROUP BY 1 ORDER BY 1
            """
            with self._db._lock:
                rows = self._db._conn.execute(sql, [step, step, *params]).fetchall()
            return [dict(r) for r in rows]

        if metric == "latency":
            # Pull durations and compute percentiles in Python (SQLite has no PERCENTILE)
            sql = f"""
                SELECT CAST(created_at / ? AS INTEGER) * ? AS ts, duration_ms
                FROM traces WHERE {clause} AND duration_ms IS NOT NULL
                ORDER BY 1
            """
            with self._db._lock:
                rows = self._db._conn.execute(sql, [step, step, *params]).fetchall()
            buckets: dict[int, list[float]] = {}
            for r in rows:
                buckets.setdefault(int(r["ts"]), []).append(float(r["duration_ms"]))
            out = []
            for ts in sorted(buckets):
                vals = sorted(buckets[ts])
                avg = sum(vals) / len(vals)
                out.append(
                    {
                        "ts": ts,
                        "avg_ms": avg,
                        "p50_ms": _percentile(vals, 50),
                        "p95_ms": _percentile(vals, 95),
                        "p99_ms": _percentile(vals, 99),
                        "samples": len(vals),
                    }
                )
            return out

        return []

    # ── breakdowns ──────────────────────────────────────────────────

    def breakdown(
        self,
        dimension: str,
        *,
        since: float | None = None,
        until: float | None = None,
        limit: int = 50,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        now = time.time()
        until = float(until if until is not None else now)
        since = float(since if since is not None else until - 86400)
        limit = max(1, min(200, limit))
        user_clause = " AND user_id = ?" if user_id else ""
        user_params: list[Any] = [user_id] if user_id else []

        col_map = {
            "models": "model_routed",
            "providers": "provider_id",
            "api_keys": "api_key",
            "intents": "intent",
        }

        if dimension in col_map:
            col = col_map[dimension]
            sql = f"""
                SELECT
                    COALESCE({col}, '') AS key,
                    COUNT(*) AS request_count,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS error_count,
                    SUM(COALESCE(prompt_tokens, 0)) AS prompt_tokens,
                    SUM(COALESCE(completion_tokens, 0)) AS completion_tokens,
                    SUM(COALESCE(total_tokens, 0)) AS total_tokens,
                    SUM(COALESCE(estimated_cost_usd, 0)) AS cost_usd,
                    AVG(duration_ms) AS avg_latency_ms,
                    AVG(intent_confidence) AS avg_confidence,
                    SUM(CASE WHEN fallback_index > 0 THEN 1 ELSE 0 END) AS fallback_count
                FROM traces
                WHERE created_at >= ? AND created_at <= ?{user_clause}
                GROUP BY 1
                ORDER BY request_count DESC
                LIMIT ?
            """
            with self._db._lock:
                rows = self._db._conn.execute(
                    sql, (since, until, *user_params, limit)
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                n = int(d["request_count"] or 0)
                d["error_rate"] = (int(d["error_count"] or 0) / n) if n else 0.0
                out.append(d)
            return out

        if dimension == "errors":
            sql = f"""
                SELECT
                    COALESCE(error_message, 'status_' || CAST(status_code AS TEXT)) AS key,
                    status_code,
                    COUNT(*) AS request_count,
                    AVG(duration_ms) AS avg_latency_ms
                FROM traces
                WHERE created_at >= ? AND created_at <= ?
                  AND (success = 0 OR status_code >= 400){user_clause}
                GROUP BY 1, 2
                ORDER BY request_count DESC
                LIMIT ?
            """
            with self._db._lock:
                rows = self._db._conn.execute(
                    sql, (since, until, *user_params, limit)
                ).fetchall()
            return [dict(r) for r in rows]

        if dimension == "fallbacks":
            sql = f"""
                SELECT
                    fallback_index AS key,
                    COUNT(*) AS request_count,
                    AVG(duration_ms) AS avg_latency_ms,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count
                FROM traces
                WHERE created_at >= ? AND created_at <= ?{user_clause}
                GROUP BY 1
                ORDER BY 1
            """
            with self._db._lock:
                rows = self._db._conn.execute(
                    sql, (since, until, *user_params)
                ).fetchall()
            return [dict(r) for r in rows]

        return []

    # ── summary KPIs ────────────────────────────────────────────────

    def summary(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        until = float(until if until is not None else now)
        since = float(since if since is not None else until - 3600)
        cache_key_age = now
        if (
            self._summary_cache
            and since == self._summary_cache[1].get("_since")
            and until == self._summary_cache[1].get("_until")
            and user_id == self._summary_cache[1].get("_user_id")
            and (cache_key_age - self._summary_cache[0]) < self._summary_ttl
        ):
            return {k: v for k, v in self._summary_cache[1].items() if not k.startswith("_")}

        user_clause = " AND user_id = ?" if user_id else ""
        user_params: list[Any] = [user_id] if user_id else []

        with self._db._lock:
            row = self._db._conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total_requests,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                    AVG(duration_ms) AS avg_latency_ms,
                    AVG(upstream_ttft_ms) AS avg_ttft_ms,
                    SUM(COALESCE(total_tokens, 0)) AS total_tokens,
                    SUM(COALESCE(prompt_tokens, 0)) AS total_prompt_tokens,
                    SUM(COALESCE(completion_tokens, 0)) AS total_completion_tokens,
                    SUM(COALESCE(estimated_cost_usd, 0)) AS estimated_cost_usd,
                    COUNT(DISTINCT model_routed) AS unique_models,
                    COUNT(DISTINCT provider_id) AS active_providers,
                    SUM(CASE WHEN fallback_index > 0 THEN 1 ELSE 0 END) AS fallback_count
                FROM traces
                WHERE created_at >= ? AND created_at <= ?{user_clause}
                """,
                (since, until, *user_params),
            ).fetchone()

            top_model = self._db._conn.execute(
                f"""
                SELECT model_routed AS key, COUNT(*) AS n FROM traces
                WHERE created_at >= ? AND created_at <= ? AND model_routed IS NOT NULL{user_clause}
                GROUP BY 1 ORDER BY n DESC LIMIT 1
                """,
                (since, until, *user_params),
            ).fetchone()
            top_intent = self._db._conn.execute(
                f"""
                SELECT intent AS key, COUNT(*) AS n FROM traces
                WHERE created_at >= ? AND created_at <= ? AND intent IS NOT NULL{user_clause}
                GROUP BY 1 ORDER BY n DESC LIMIT 1
                """,
                (since, until, *user_params),
            ).fetchone()

            durs = [
                float(r["duration_ms"])
                for r in self._db._conn.execute(
                    f"""
                    SELECT duration_ms FROM traces
                    WHERE created_at >= ? AND created_at <= ?
                      AND duration_ms IS NOT NULL{user_clause}
                    ORDER BY created_at DESC LIMIT 50000
                    """,
                    (since, until, *user_params),
                ).fetchall()
            ]

        total = int(row["total_requests"] or 0)
        success = int(row["success_count"] or 0)
        window = max(1.0, until - since)
        sorted_durs = sorted(durs)
        result = {
            "total_requests": total,
            "success_rate": (success / total) if total else 1.0,
            "avg_latency_ms": float(row["avg_latency_ms"] or 0),
            "p95_latency_ms": _percentile(sorted_durs, 95) or 0.0,
            "avg_ttft_ms": float(row["avg_ttft_ms"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "total_prompt_tokens": int(row["total_prompt_tokens"] or 0),
            "total_completion_tokens": int(row["total_completion_tokens"] or 0),
            "estimated_cost_usd": float(row["estimated_cost_usd"] or 0),
            "unique_models": int(row["unique_models"] or 0),
            "active_providers": int(row["active_providers"] or 0),
            "top_model": top_model["key"] if top_model else None,
            "top_intent": top_intent["key"] if top_intent else None,
            "fallback_rate": (int(row["fallback_count"] or 0) / total) if total else 0.0,
            "error_rate": ((total - success) / total) if total else 0.0,
            "requests_per_minute": (total / window) * 60.0,
            "time_range": {"since": since, "until": until},
            "_since": since,
            "_until": until,
            "_user_id": user_id,
        }
        self._summary_cache = (now, result)
        return {k: v for k, v in result.items() if not k.startswith("_")}

    # ── cost overrides ──────────────────────────────────────────────

    def list_cost_overrides(self) -> list[dict[str, Any]]:
        with self._db._lock:
            rows = self._db._conn.execute(
                "SELECT model_id, input_per_m, output_per_m, updated_at"
                " FROM cost_overrides ORDER BY model_id"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_cost_override(
        self, model_id: str, input_per_m: float, output_per_m: float
    ) -> None:
        with self._db._lock:
            self._db._conn.execute(
                """
                INSERT INTO cost_overrides (model_id, input_per_m, output_per_m, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(model_id) DO UPDATE SET
                    input_per_m = excluded.input_per_m,
                    output_per_m = excluded.output_per_m,
                    updated_at = excluded.updated_at
                """,
                (model_id, float(input_per_m), float(output_per_m), time.time()),
            )

    def delete_cost_override(self, model_id: str) -> bool:
        with self._db._lock:
            cur = self._db._conn.execute(
                "DELETE FROM cost_overrides WHERE model_id = ?", (model_id,)
            )
            return cur.rowcount > 0

    def cost_overrides_map(self) -> dict[str, tuple[float, float]]:
        return {
            r["model_id"]: (float(r["input_per_m"]), float(r["output_per_m"]))
            for r in self.list_cost_overrides()
        }

    # ── export ──────────────────────────────────────────────────────

    def iter_export(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        limit: int = 10000,
        user_id: str | None = None,
    ):
        now = time.time()
        until = float(until if until is not None else now)
        since = float(since if since is not None else until - 86400)
        limit = max(1, min(100_000, int(limit)))
        user_clause = " AND user_id = ?" if user_id else ""
        params: list[Any] = [since, until]
        if user_id:
            params.append(user_id)
        params.append(limit)
        with self._db._lock:
            rows = self._db._conn.execute(
                f"""
                SELECT trace_id, created_at, model_requested, model_routed, intent,
                       intent_confidence, provider_id, status_code, duration_ms,
                       upstream_ttft_ms, prompt_tokens, completion_tokens,
                       estimated_cost_usd, fallback_index, error_message
                FROM traces
                WHERE created_at >= ? AND created_at <= ?{user_clause}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        for r in rows:
            yield dict(r)

    def writer_stats_placeholder(self) -> dict[str, Any]:
        with self._db._lock:
            n = self._db._conn.execute("SELECT COUNT(*) AS n FROM traces").fetchone()["n"]
        return {"trace_count": int(n)}
