"""Non-blocking async batch writer for analytics traces."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from nimmakai.analytics.models import SPAN_INSERT_SQL, TRACE_INSERT_SQL, TraceRecord

if TYPE_CHECKING:
    from nimmakai.analytics.events import EventBus
    from nimmakai.catalog.db import NimmakaiDB

logger = logging.getLogger(__name__)


class TraceWriter:
    """
    Collects TraceRecords in an asyncio.Queue and flushes to SQLite in batches.

    ``enqueue()`` is fire-and-forget (put_nowait) — never blocks the request path.
    """

    def __init__(
        self,
        db: NimmakaiDB,
        *,
        batch_size: int = 50,
        flush_interval: float = 1.0,
        max_queue: int = 5000,
        event_bus: EventBus | None = None,
        on_flush: Any | None = None,
    ) -> None:
        self._db = db
        self._batch_size = max(1, batch_size)
        self._flush_interval = max(0.1, flush_interval)
        self._queue: asyncio.Queue[TraceRecord] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task[None] | None = None
        self._stopped = False
        self._dropped = 0
        self._flushed = 0
        self._event_bus = event_bus
        self._on_flush = on_flush  # optional webhook / otel hook

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def flushed(self) -> int:
        return self._flushed

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._flush_loop(), name="analytics-writer")
        logger.info(
            "analytics writer started batch=%s flush=%.1fs",
            self._batch_size,
            self._flush_interval,
        )

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # Final drain
        batch = self._drain(limit=10_000)
        if batch:
            try:
                await asyncio.to_thread(self._write_batch, batch)
                self._flushed += len(batch)
                self._publish_batch(batch)
            except Exception:
                logger.exception(
                    "analytics final drain failed (n=%s); dropping", len(batch)
                )
                self._dropped += len(batch)
        logger.info(
            "analytics writer stopped flushed=%s dropped=%s",
            self._flushed,
            self._dropped,
        )

    def enqueue(self, trace: TraceRecord) -> None:
        """Never blocks. Drops under backpressure and increments counter."""
        try:
            self._queue.put_nowait(trace)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % 100 == 1:
                logger.warning(
                    "analytics queue full — dropped %s traces", self._dropped
                )

    async def _flush_loop(self) -> None:
        while not self._stopped:
            batch: list[TraceRecord] = []
            try:
                item = await asyncio.wait_for(
                    self._queue.get(), timeout=self._flush_interval
                )
                batch.append(item)
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                # Drain remaining before exit
                batch.extend(self._drain(limit=10_000))
                if batch:
                    await asyncio.to_thread(self._write_batch, batch)
                    self._publish_batch(batch)
                raise

            while len(batch) < self._batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            if batch:
                try:
                    await asyncio.to_thread(self._write_batch, batch)
                    self._flushed += len(batch)
                    self._publish_batch(batch)
                    if self._on_flush is not None:
                        try:
                            self._on_flush(batch)
                        except Exception:
                            logger.exception("analytics on_flush hook failed")
                except Exception:
                    logger.exception("analytics batch write failed (n=%s)", len(batch))
                    # Back off then re-enqueue once; permanent failures drop.
                    await asyncio.sleep(min(2.0, self._flush_interval))
                    for trace in batch:
                        try:
                            self._queue.put_nowait(trace)
                        except asyncio.QueueFull:
                            self._dropped += 1
                    # Avoid tight fail loops: wait a full interval before next drain
                    await asyncio.sleep(self._flush_interval)

    def _drain(self, *, limit: int) -> list[TraceRecord]:
        out: list[TraceRecord] = []
        while len(out) < limit:
            try:
                out.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return out

    def _write_batch(self, batch: list[TraceRecord]) -> None:
        conn = self._db._conn
        with self._db._lock:
            conn.execute("BEGIN")
            try:
                for trace in batch:
                    conn.execute(TRACE_INSERT_SQL, trace.to_row())
                    for span in trace.spans:
                        conn.execute(SPAN_INSERT_SQL, span.to_row(trace.trace_id))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _publish_batch(self, batch: list[TraceRecord]) -> None:
        if self._event_bus is None:
            return
        for trace in batch:
            self._event_bus.publish("trace", trace.to_summary())

    def stats(self) -> dict[str, Any]:
        return {
            "pending": self.pending,
            "flushed": self._flushed,
            "dropped": self._dropped,
            "running": self._task is not None and not self._stopped,
            "ts": time.time(),
        }
