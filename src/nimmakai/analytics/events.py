"""Fan-out SSE event bus for real-time dashboard updates."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """
    Non-blocking publish / subscribe bus for Server-Sent Events.

    Slow consumers drop events (queue full) rather than blocking producers.
    Heartbeats are emitted by the subscribe loop every ``heartbeat_seconds``.
    """

    def __init__(self, *, max_queue: int = 100, heartbeat_seconds: float = 15.0) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._max_queue = max_queue
        self._heartbeat = heartbeat_seconds
        self._lock = asyncio.Lock()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def subscribe(self) -> AsyncIterator[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._subscribers.add(q)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=self._heartbeat)
                    yield f"data: {event}\n\n"
                except TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            async with self._lock:
                self._subscribers.discard(q)

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Fire-and-forget publish to all subscribers."""
        payload = json.dumps({"type": event_type, **(data or {})}, default=str)
        dead: list[asyncio.Queue[str]] = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass
            except Exception:
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)
