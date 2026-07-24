"""Production request logging + durable ring file + live callback."""

from __future__ import annotations

import logging
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("nimmakai.request")

META_KEY_ENABLED = "request_file_logging_enabled"
DEFAULT_MAX_ENTRIES = 20_000
DEFAULT_FILENAME = "request_logs.txt"


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
    user_id: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.ts))
        return d

    def format_line(self) -> str:
        """Single-line text record for the durable log file."""
        parts = [
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.ts)),
            f"req={self.id}",
            f"{self.method} {self.path}",
            f"status={self.status if self.status is not None else '-'}",
            f"ms={self.duration_ms:.0f}" if self.duration_ms is not None else "ms=?",
        ]
        if self.client:
            parts.append(f"client={self.client}")
        if self.user_id:
            parts.append(f"user={self.user_id}")
        if self.model_requested:
            parts.append(f"req_model={self.model_requested}")
        if self.model_routed:
            parts.append(f"routed={self.model_routed}")
        if self.provider:
            parts.append(f"provider={self.provider}")
        if self.intent:
            parts.append(f"intent={self.intent}")
        if self.route_mode:
            parts.append(f"mode={self.route_mode}")
        if self.stream is not None:
            parts.append(f"stream={self.stream}")
        if self.fallback_index:
            parts.append(f"fallback={self.fallback_index}")
        if self.error:
            # Keep one line — collapse newlines
            err = str(self.error).replace("\n", " ").replace("\r", " ")[:400]
            parts.append(f"error={err}")
        return " ".join(parts)


class RequestLogStore:
    """
    In-memory ring + optional durable text file (last N lines) beside the DB.

    File path defaults to ``<sqlite_dir>/request_logs.txt``. Toggle via
    ``set_enabled`` (persisted in sqlite meta when a db handle is bound).
    """

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.max_entries = max(1, int(max_entries))
        self._items: deque[RequestLog] = deque(maxlen=self.max_entries)
        self._lock = threading.Lock()
        self._file_path: Path | None = None
        self._file_enabled: bool = True
        self._appends_since_trim: int = 0
        self._db: Any | None = None
        self._on_add: Callable[[RequestLog], None] | None = None

    def configure(
        self,
        *,
        max_entries: int | None = None,
        file_path: Path | str | None = None,
        enabled: bool | None = None,
        db: Any | None = None,
        on_add: Callable[[RequestLog], None] | None = None,
    ) -> None:
        with self._lock:
            if max_entries is not None:
                self.max_entries = max(1, int(max_entries))
                # Resize deque preserving newest
                items = list(self._items)[: self.max_entries]
                self._items = deque(items, maxlen=self.max_entries)
            if file_path is not None:
                self._file_path = Path(file_path)
            if db is not None:
                self._db = db
                stored = db.get_meta(META_KEY_ENABLED)
                if stored is not None:
                    self._file_enabled = stored.strip().lower() in {
                        "1",
                        "true",
                        "yes",
                        "on",
                    }
            if enabled is not None:
                self._file_enabled = bool(enabled)
            if on_add is not None:
                self._on_add = on_add

    @property
    def enabled(self) -> bool:
        return self._file_enabled

    @property
    def file_path(self) -> Path | None:
        return self._file_path

    @property
    def count(self) -> int:
        return len(self._items)

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._file_enabled = bool(enabled)
            if self._db is not None:
                try:
                    self._db.set_meta(
                        META_KEY_ENABLED, "true" if self._file_enabled else "false"
                    )
                except Exception:
                    logger.exception("failed to persist request logging flag")
            if self._file_enabled and self._file_path is not None:
                # Rewrite current ring so enabling starts from a consistent file
                self._rewrite_file_unlocked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._file_enabled,
                "max_entries": self.max_entries,
                "memory_count": len(self._items),
                "file_path": str(self._file_path) if self._file_path else None,
                "file_exists": bool(
                    self._file_path and self._file_path.is_file()
                ),
            }

    def add(self, entry: RequestLog) -> None:
        cb: Callable[[RequestLog], None] | None
        with self._lock:
            self._items.appendleft(entry)
            if self._file_enabled and self._file_path is not None:
                self._append_file_unlocked(entry)
            cb = self._on_add
        if cb is not None:
            try:
                cb(entry)
            except Exception:
                logger.debug("request log on_add failed", exc_info=True)

    def list(
        self,
        *,
        limit: int = 50,
        path_prefix: str | None = None,
        errors_only: bool = False,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with self._lock:
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

    def _append_file_unlocked(self, entry: RequestLog) -> None:
        assert self._file_path is not None
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._file_path, "a", encoding="utf-8") as f:
                f.write(entry.format_line() + "\n")
            self._appends_since_trim += 1
            if self._appends_since_trim >= 500:
                self._trim_file_unlocked()
                self._appends_since_trim = 0
        except Exception:
            logger.exception("failed to append request log file")

    def _trim_file_unlocked(self) -> None:
        """Keep only the last ``max_entries`` lines on disk."""
        if self._file_path is None or not self._file_path.is_file():
            return
        try:
            with open(self._file_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if len(lines) <= self.max_entries:
                return
            keep = lines[-self.max_entries :]
            tmp = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(keep)
            tmp.replace(self._file_path)
        except Exception:
            logger.exception("failed to trim request log file")

    def _rewrite_file_unlocked(self) -> None:
        if self._file_path is None:
            return
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            # Memory is newest-first; file should be chronological oldest→newest
            lines = [e.format_line() + "\n" for e in reversed(self._items)]
            tmp = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(lines)
            tmp.replace(self._file_path)
            self._appends_since_trim = 0
        except Exception:
            logger.exception("failed to rewrite request log file")


# Process-global store (one per worker)
request_logs = RequestLogStore()


def setup_logging(level: str = "info") -> None:
    """Configure structured-ish stdout logging (Heroku drains this)."""
    root = logging.getLogger()
    log_level = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(log_level)
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

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def log_request_line(entry: RequestLog) -> None:
    """Emit one compact production log line to stdout."""
    line = entry.format_line()
    if entry.error or (entry.status and entry.status >= 500):
        logger.error(line)
    elif entry.status and entry.status >= 400:
        logger.warning(line)
    else:
        logger.info(line)


def default_log_file_path(sqlite_path: str | Path) -> Path:
    """Place request_logs.txt next to the SQLite DB (same data directory)."""
    return Path(sqlite_path).expanduser().resolve().parent / DEFAULT_FILENAME
