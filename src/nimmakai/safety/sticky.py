"""Session → key_id + model stickiness (OpenRouter-style auto-router pins)."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass
class StickyBinding:
    key_id: str | None = None
    model_id: str | None = None
    expires: float = 0.0


class StickySessionStore:
    """
    Soft affinity for multi-turn chats.

    - key_id: prefer same API key (account safety / cache locality)
    - model_id: prefer same routed model (OpenRouter auto-router session pin)
    """

    def __init__(self, *, ttl_seconds: float = 1800.0, max_size: int = 4096) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._map: OrderedDict[str, StickyBinding] = OrderedDict()

    def _get_binding(self, session_id: str | None) -> StickyBinding | None:
        if not session_id:
            return None
        now = time.monotonic()
        item = self._map.get(session_id)
        if item is None:
            return None
        if now > item.expires:
            self._map.pop(session_id, None)
            return None
        self._map.move_to_end(session_id)
        return item

    def get(self, session_id: str | None) -> str | None:
        """Return sticky key_id (legacy API)."""
        b = self._get_binding(session_id)
        return b.key_id if b else None

    def get_model(self, session_id: str | None) -> str | None:
        b = self._get_binding(session_id)
        return b.model_id if b else None

    def put(self, session_id: str | None, key_id: str) -> None:
        """Update sticky key only (preserve model pin)."""
        if not session_id or not key_id:
            return
        now = time.monotonic()
        prev = self._map.get(session_id)
        model = prev.model_id if prev and now <= prev.expires else None
        self._map[session_id] = StickyBinding(
            key_id=key_id, model_id=model, expires=now + self.ttl_seconds
        )
        self._map.move_to_end(session_id)
        while len(self._map) > self.max_size:
            self._map.popitem(last=False)

    def put_model(self, session_id: str | None, model_id: str) -> None:
        """Pin routed model for this session (OpenRouter auto-router)."""
        if not session_id or not model_id:
            return
        now = time.monotonic()
        prev = self._map.get(session_id)
        key = prev.key_id if prev and now <= prev.expires else None
        self._map[session_id] = StickyBinding(
            key_id=key, model_id=model_id, expires=now + self.ttl_seconds
        )
        self._map.move_to_end(session_id)
        while len(self._map) > self.max_size:
            self._map.popitem(last=False)

    def put_both(
        self,
        session_id: str | None,
        *,
        key_id: str | None = None,
        model_id: str | None = None,
    ) -> None:
        if not session_id:
            return
        now = time.monotonic()
        prev = self._map.get(session_id)
        prev_ok = prev is not None and now <= prev.expires
        self._map[session_id] = StickyBinding(
            key_id=key_id if key_id else (prev.key_id if prev_ok else None),
            model_id=model_id if model_id else (prev.model_id if prev_ok else None),
            expires=now + self.ttl_seconds,
        )
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

        # OpenRouter: body.session_id or x-session-id
        if body:
            sid = body.get("session_id") or body.get("sessionId")
            if sid:
                return str(sid).strip() or None

        explicit = (
            _h("x-nimmakai-session")
            or _h("x-session-id")
            or _h("X-Session-Id")
        )
        if explicit:
            return explicit

        chat_id = (
            _h("x-cursor-chat-id")
            or _h("x-chat-id")
            or _h("x-opencode-session")
            or _h("x-cline-session")
            or _h("x-kiro-session")
            or _h("x-codeium-session")
            or _h("x-windsurf-session")
            or _h("x-cascade-session")
        )
        if chat_id:
            basis = f"{proxy_token or 'anon'}:{chat_id}"
            return hashlib.sha256(basis.encode()).hexdigest()[:32]

        # Implicit conversation fingerprint (OpenRouter-style):
        # hash first system + first user message so multi-turn sticks without header.
        if body and isinstance(body.get("messages"), list):
            msgs = body["messages"]
            sys0 = ""
            user0 = ""
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                role = str(m.get("role") or "")
                content = m.get("content")
                if isinstance(content, list):
                    # multimodal — take text parts
                    parts = [
                        str(p.get("text") or "")
                        for p in content
                        if isinstance(p, dict)
                    ]
                    content = " ".join(parts)
                text = str(content or "")[:400]
                if role == "system" and not sys0:
                    sys0 = text
                elif role == "user" and not user0:
                    user0 = text
                if sys0 and user0:
                    break
            if user0:
                basis = f"{proxy_token or 'anon'}:{sys0}|{user0}"
                return "fp:" + hashlib.sha256(basis.encode()).hexdigest()[:28]

        return None
