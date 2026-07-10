"""Session → key_id sticky affinity (soft bias)."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Any


class StickySessionStore:
    def __init__(self, *, ttl_seconds: float = 1800.0, max_size: int = 4096) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._map: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def get(self, session_id: str | None) -> str | None:
        if not session_id:
            return None
        now = time.monotonic()
        item = self._map.get(session_id)
        if item is None:
            return None
        key_id, expires = item
        if now > expires:
            self._map.pop(session_id, None)
            return None
        self._map.move_to_end(session_id)
        return key_id

    def put(self, session_id: str | None, key_id: str) -> None:
        if not session_id:
            return
        now = time.monotonic()
        self._map[session_id] = (key_id, now + self.ttl_seconds)
        self._map.move_to_end(session_id)
        while len(self._map) > self.max_size:
            self._map.popitem(last=False)

    def resolve_session_id(
        self,
        headers: dict[str, str] | Any,
        *,
        proxy_token: str | None = None,
        body: dict | None = None,
    ) -> str | None:
        def _h(name: str) -> str | None:
            if hasattr(headers, "get"):
                v = headers.get(name) or headers.get(name.lower())
                if v:
                    return str(v)
                for k in getattr(headers, "keys", lambda: [])():
                    if str(k).lower() == name.lower():
                        return str(headers[k])
            return None

        # Only sticky when the client opts in — never pin all traffic to one
        # NIM key via the shared proxy API key (that defeats multi-key balance).
        explicit = _h("x-nimmakai-session")
        if explicit:
            return explicit

        chat_id = _h("x-cursor-chat-id") or _h("x-chat-id")
        if chat_id:
            basis = f"{proxy_token or 'anon'}:{chat_id}"
            return hashlib.sha256(basis.encode()).hexdigest()[:32]

        return None
