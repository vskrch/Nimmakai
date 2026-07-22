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

    Non-admin subscribers pass ``user_id`` and only receive traces that match
    that user. Admins pass ``see_all=True``.
    """

    def __init__(self, *, max_queue: int = 100, heartbeat_seconds: float = 15.0) -> None:
        # queue → (see_all, user_id)
        self._subscribers: dict[asyncio.Queue[str], tuple[bool, str | None]] = {}
        self._max_queue = max_queue
        self._heartbeat = heartbeat_seconds
        self._lock = asyncio.Lock()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def subscribe(
        self, *, user_id: str | None = None, see_all: bool = False
    ) -> AsyncIterator[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._subscribers[q] = (see_all, user_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=self._heartbeat)
                    yield f"data: {event}\n\n"
                except TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            async with self._lock:
                self._subscribers.pop(q, None)

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Fire-and-forget publish to matching subscribers."""
        payload_data = dict(data or {})
        payload = json.dumps({"type": event_type, **payload_data}, default=str)
        event_uid = payload_data.get("user_id")
        dead: list[asyncio.Queue[str]] = []
        for q, (see_all, filter_uid) in list(self._subscribers.items()):
            if not see_all:
                if (
                    filter_uid is None
                    or not event_uid
                    or str(event_uid) != str(filter_uid)
                ):
                    continue
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass
            except Exception:
                dead.append(q)
        for q in dead:
            self._subscribers.pop(q, None)
