"""Rolling retention + 1-minute rollup aggregation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nimmakai.catalog.db import NimmakaiDB

logger = logging.getLogger(__name__)

# Meta key for last rolled-up created_at watermark
_ROLLUP_WATERMARK = "analytics_rollup_watermark"


class RetentionManager:
    """
    Periodically:
      1. Aggregate new raw traces into 1-minute rollup buckets
      2. Delete raw traces older than retention_days
      3. Delete rollups older than rollup_retention_days
      4. PRAGMA optimize
    """

    def __init__(
        self,
        db: NimmakaiDB,
        *,
        retention_days: int = 7,
        rollup_retention_days: int = 90,
        interval_seconds: float = 900.0,
    ) -> None:
        self._db = db
        self.retention_days = max(1, retention_days)
        self.rollup_retention_days = max(1, rollup_retention_days)
        self.interval_seconds = max(60.0, interval_seconds)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="analytics-retention")
        logger.info(
            "analytics retention started raw=%sd rollup=%sd every=%.0fs",
            self.retention_days,
            self.rollup_retention_days,
            self.interval_seconds,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        # First cycle shortly after start
        await asyncio.sleep(5.0)
        while True:
            try:
                report = await asyncio.to_thread(self.run_cycle)
                logger.info("analytics retention cycle: %s", report)
            except Exception:
                logger.exception("analytics retention cycle failed")
            await asyncio.sleep(self.interval_seconds)

    def run_cycle(self) -> dict[str, Any]:
        now = time.time()
        rolled = self._rollup_new(now)
        deleted_traces = self._purge_traces(now)
        deleted_rollups = self._purge_rollups(now)
        with self._db._lock, contextlib.suppress(Exception):
            self._db._conn.execute("PRAGMA optimize")
        return {
            "rolled_up": rolled,
            "deleted_traces": deleted_traces,
            "deleted_rollups": deleted_rollups,
            "ts": now,
        }

    def _rollup_new(self, now: float) -> int:
        watermark_s = self._db.get_meta(_ROLLUP_WATERMARK, "0") or "0"
        try:
            watermark = float(watermark_s)
        except ValueError:
            watermark = 0.0

        # Only roll complete minutes (exclude current minute)
        until = int(now // 60) * 60
        with self._db._lock:
            rows = self._db._conn.execute(
                """
                SELECT
                    CAST(created_at / 60 AS INTEGER) * 60 AS bucket_ts,
                    COALESCE(intent, '') AS intent,
                    COALESCE(model_routed, '') AS model,
                    COALESCE(provider_id, '') AS provider,
                    COALESCE(api_key, '') AS api_key,
                    COUNT(*) AS request_count,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS error_count,
                    SUM(CASE WHEN is_stream = 1 THEN 1 ELSE 0 END) AS stream_count,
                    SUM(CASE WHEN fallback_index > 0 THEN 1 ELSE 0 END) AS fallback_count,
                    SUM(COALESCE(prompt_tokens, 0)) AS prompt_tokens_sum,
                    SUM(COALESCE(completion_tokens, 0)) AS completion_tokens_sum,
                    SUM(COALESCE(duration_ms, 0)) AS duration_sum_ms,
                    MAX(COALESCE(duration_ms, 0)) AS duration_max_ms,
                    SUM(COALESCE(upstream_ttft_ms, 0)) AS ttft_sum_ms,
                    SUM(COALESCE(estimated_cost_usd, 0)) AS cost_sum_usd,
                    MAX(created_at) AS max_created
                FROM traces
                WHERE created_at > ? AND created_at < ?
                GROUP BY 1, 2, 3, 4, 5
                """,
                (watermark, float(until)),
            ).fetchall()

            if not rows:
                return 0

            max_created = watermark
            for r in rows:
                self._db._conn.execute(
                    """
                    INSERT INTO trace_rollups (
                        bucket_ts, intent, model, provider, api_key,
                        request_count, success_count, error_count, stream_count,
                        fallback_count, prompt_tokens_sum, completion_tokens_sum,
                        duration_sum_ms, duration_max_ms, ttft_sum_ms, cost_sum_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(bucket_ts, intent, model, provider, api_key) DO UPDATE SET
                        request_count = request_count + excluded.request_count,
                        success_count = success_count + excluded.success_count,
                        error_count = error_count + excluded.error_count,
                        stream_count = stream_count + excluded.stream_count,
                        fallback_count = fallback_count + excluded.fallback_count,
                        prompt_tokens_sum = prompt_tokens_sum + excluded.prompt_tokens_sum,
                        completion_tokens_sum = completion_tokens_sum
                            + excluded.completion_tokens_sum,
                        duration_sum_ms = duration_sum_ms + excluded.duration_sum_ms,
                        duration_max_ms = MAX(duration_max_ms, excluded.duration_max_ms),
                        ttft_sum_ms = ttft_sum_ms + excluded.ttft_sum_ms,
                        cost_sum_usd = cost_sum_usd + excluded.cost_sum_usd
                    """,
                    (
                        int(r["bucket_ts"]),
                        r["intent"],
                        r["model"],
                        r["provider"],
                        r["api_key"],
                        int(r["request_count"] or 0),
                        int(r["success_count"] or 0),
                        int(r["error_count"] or 0),
                        int(r["stream_count"] or 0),
                        int(r["fallback_count"] or 0),
                        int(r["prompt_tokens_sum"] or 0),
                        int(r["completion_tokens_sum"] or 0),
                        float(r["duration_sum_ms"] or 0),
                        float(r["duration_max_ms"] or 0),
                        float(r["ttft_sum_ms"] or 0),
                        float(r["cost_sum_usd"] or 0),
                    ),
                )
                max_created = max(max_created, float(r["max_created"] or 0))

        self._db.set_meta(_ROLLUP_WATERMARK, str(max_created))
        return len(rows)

    def _purge_traces(self, now: float) -> int:
        cutoff = now - (self.retention_days * 86400)
        with self._db._lock:
            # Delete spans for old traces first
            self._db._conn.execute(
                """
                DELETE FROM trace_spans WHERE trace_id IN (
                    SELECT trace_id FROM traces WHERE created_at < ?
                )
                """,
                (cutoff,),
            )
            cur = self._db._conn.execute(
                "DELETE FROM traces WHERE created_at < ?", (cutoff,)
            )
            return cur.rowcount

    def _purge_rollups(self, now: float) -> int:
        cutoff = int(now - (self.rollup_retention_days * 86400))
        with self._db._lock:
            cur = self._db._conn.execute(
                "DELETE FROM trace_rollups WHERE bucket_ts < ?", (cutoff,)
            )
            return cur.rowcount
