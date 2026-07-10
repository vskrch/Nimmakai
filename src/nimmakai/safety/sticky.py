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
                # case-insensitive for Starlette Headers
                v = headers.get(name) or headers.get(name.lower())
                if v:
                    return str(v)
                # try title variants
                for k in getattr(headers, "keys", lambda: [])():
                    if str(k).lower() == name.lower():
                        return str(headers[k])
            return None

        explicit = _h("x-nimmakai-session")
        if explicit:
            return explicit

        chat_id = _h("x-cursor-chat-id") or _h("x-chat-id")
        if proxy_token and chat_id:
            return hashlib.sha256(f"{proxy_token}:{chat_id}".encode()).hexdigest()[:32]

        if proxy_token:
            return hashlib.sha256(proxy_token.encode()).hexdigest()[:32]

        if body:
            messages = body.get("messages")
            if isinstance(messages, list) and messages:
                first = messages[0]
                if isinstance(first, dict) and first.get("role") == "system":
                    content = first.get("content") or ""
                    if isinstance(content, str) and content:
                        return hashlib.sha256(content[:512].encode()).hexdigest()[:32]
        return None
