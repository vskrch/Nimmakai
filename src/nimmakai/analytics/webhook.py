"""Optional webhook broadcast for completed traces."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from nimmakai.analytics.models import TraceRecord

logger = logging.getLogger(__name__)


class WebhookBroadcaster:
    """
    Batches traces and POSTs JSON to ANALYTICS_WEBHOOK_URL.
    Fire-and-forget from the writer flush hook — never blocks request path.
    """

    def __init__(
        self,
        url: str,
        *,
        batch_size: int = 50,
        max_retries: int = 3,
        timeout: float = 10.0,
    ) -> None:
        self.url = url.strip()
        self.batch_size = max(1, batch_size)
        self.max_retries = max(1, max_retries)
        self.timeout = timeout
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    def on_flush(self, batch: list[TraceRecord]) -> None:
        """Sync hook called from TraceWriter — schedule async send."""
        payloads = [t.to_summary() for t in batch]
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._enqueue(payloads))

    async def _enqueue(self, payloads: list[dict[str, Any]]) -> None:
        async with self._lock:
            self._buffer.extend(payloads)
            while len(self._buffer) >= self.batch_size:
                chunk = self._buffer[: self.batch_size]
                del self._buffer[: self.batch_size]
                await self._send(chunk)

    async def flush(self) -> None:
        async with self._lock:
            if self._buffer:
                chunk = list(self._buffer)
                self._buffer.clear()
                await self._send(chunk)

    async def _send(self, traces: list[dict[str, Any]]) -> None:
        if not self.url or not traces:
            return
        body = {"traces": traces, "count": len(traces)}
        delay = 0.5
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(self.url, json=body)
                    if resp.status_code < 400:
                        return
                    logger.warning(
                        "analytics webhook status=%s attempt=%s",
                        resp.status_code,
                        attempt + 1,
                    )
            except Exception:
                logger.exception(
                    "analytics webhook failed attempt=%s", attempt + 1
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)
