"""Production request logging + in-memory recent log ring for /admin/logs."""

from __future__ import annotations

import logging
import sys
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("nimmakai.request")


@dataclass
class RequestLog:
    id: str
    ts: float
    method: str
    path: str
    status: int | None = None
    duration_ms: float | None = None
    client: str | None = None
    model_requested: str | None = None
    model_routed: str | None = None
    provider: str | None = None
    intent: str | None = None
    route_mode: str | None = None
    stream: bool | None = None
    fallback_index: int | None = None
    error: str | None = None
    user_agent: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.ts))
        return d


class RequestLogStore:
    """Thread-safe ring buffer of recent requests (for dashboard /admin/logs)."""

    def __init__(self, max_entries: int = 200) -> None:
        self.max_entries = max_entries
        self._items: deque[RequestLog] = deque(maxlen=max_entries)

    def add(self, entry: RequestLog) -> None:
        self._items.appendleft(entry)

    def list(
        self,
        *,
        limit: int = 50,
        path_prefix: str | None = None,
        errors_only: bool = False,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in self._items:
            if path_prefix and not item.path.startswith(path_prefix):
                continue
            if errors_only and not (
                item.error
                or (item.status is not None and item.status >= 400)
            ):
                continue
            out.append(item.to_dict())
            if len(out) >= limit:
                break
        return out


# Process-global store (one per dyno)
request_logs = RequestLogStore()


def setup_logging(level: str = "info") -> None:
    """Configure structured-ish stdout logging (Heroku drains this)."""
    root = logging.getLogger()
    log_level = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(log_level)
    # Avoid duplicate handlers under uvicorn multi-worker
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "_nimmakai", False)
        for h in root.handlers
    ):
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(log_level)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        handler._nimmakai = True  # type: ignore[attr-defined]
        root.addHandler(handler)

    # Quieter third-party noise
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def log_request_line(entry: RequestLog) -> None:
    """Emit one compact production log line."""
    parts = [
        f"req={entry.id}",
        f"{entry.method} {entry.path}",
        f"status={entry.status}",
        f"ms={entry.duration_ms:.0f}" if entry.duration_ms is not None else "ms=?",
    ]
    if entry.model_requested:
        parts.append(f"req_model={entry.model_requested}")
    if entry.model_routed:
        parts.append(f"routed={entry.model_routed}")
    if entry.provider:
        parts.append(f"provider={entry.provider}")
    if entry.intent:
        parts.append(f"intent={entry.intent}")
    if entry.route_mode:
        parts.append(f"mode={entry.route_mode}")
    if entry.stream is not None:
        parts.append(f"stream={entry.stream}")
    if entry.fallback_index:
        parts.append(f"fallback={entry.fallback_index}")
    if entry.error:
        parts.append(f"error={entry.error}")
    line = " ".join(parts)
    if entry.error or (entry.status and entry.status >= 500):
        logger.error(line)
    elif entry.status and entry.status >= 400:
        logger.warning(line)
    else:
        logger.info(line)
