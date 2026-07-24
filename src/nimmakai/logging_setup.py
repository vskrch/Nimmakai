"""Production request logging + rotating durable files + live callback."""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("nimmakai.request")

META_KEY_ENABLED = "request_file_logging_enabled"
DEFAULT_MAX_ENTRIES = 20_000
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MiB per file
DEFAULT_RETENTION_DAYS = 90  # ~3 months
DEFAULT_LOG_DIRNAME = "request_logs"
_DATE_RE = re.compile(r"^requests-(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.log$")


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
            err = str(self.error).replace("\n", " ").replace("\r", " ")[:400]
            parts.append(f"error={err}")
        return " ".join(parts)


class RequestLogStore:
    """
    In-memory ring (for Live Feed /admin/logs) + perpetual rotating files.

    Files live under ``<sqlite_dir>/request_logs/``:

    - Active: ``requests-YYYY-MM-DD.log``
    - Rotated at ``max_file_bytes`` (default 50MB): ``requests-YYYY-MM-DD.N.log``
    - Retention: delete dated files older than ``retention_days`` (default 90)

    Toggle via ``set_enabled`` (persisted in sqlite meta when a db is bound).
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        *,
        max_file_bytes: int = DEFAULT_MAX_BYTES,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        self.max_entries = max(1, int(max_entries))
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.retention_days = max(1, int(retention_days))
        self._items: deque[RequestLog] = deque(maxlen=self.max_entries)
        self._lock = threading.Lock()
        self._log_dir: Path | None = None
        self._file_enabled: bool = True
        self._appends_since_maintain: int = 0
        self._db: Any | None = None
        self._on_add: Callable[[RequestLog], None] | None = None
        self._active_path: Path | None = None
        self._active_day: str | None = None

    def configure(
        self,
        *,
        max_entries: int | None = None,
        file_path: Path | str | None = None,
        log_dir: Path | str | None = None,
        enabled: bool | None = None,
        max_file_bytes: int | None = None,
        retention_days: int | None = None,
        db: Any | None = None,
        on_add: Callable[[RequestLog], None] | None = None,
    ) -> None:
        with self._lock:
            if max_entries is not None:
                self.max_entries = max(1, int(max_entries))
                items = list(self._items)[: self.max_entries]
                self._items = deque(items, maxlen=self.max_entries)
            if max_file_bytes is not None:
                self.max_file_bytes = max(1, int(max_file_bytes))
            if retention_days is not None:
                self.retention_days = max(1, int(retention_days))
            if log_dir is not None:
                self._log_dir = Path(log_dir)
            elif file_path is not None:
                # Back-compat: treat a file path as either the log dir or a file inside it
                p = Path(file_path)
                self._log_dir = p if p.suffix == "" else p.parent
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
            if self._log_dir is not None:
                try:
                    self._log_dir.mkdir(parents=True, exist_ok=True)
                    self._purge_old_unlocked()
                except Exception:
                    logger.exception("request log dir setup failed")

    @property
    def enabled(self) -> bool:
        return self._file_enabled

    @property
    def file_path(self) -> Path | None:
        """Active append target (for status / UI)."""
        return self._active_path or self._current_active_path_unlocked()

    @property
    def log_dir(self) -> Path | None:
        return self._log_dir

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

    def status(self) -> dict[str, Any]:
        with self._lock:
            files = self._list_log_files_unlocked()
            total = 0
            for f in files:
                with suppress(OSError):
                    total += f.stat().st_size
            active = self._current_active_path_unlocked()
            active_size = 0
            if active and active.is_file():
                with suppress(OSError):
                    active_size = active.stat().st_size
            return {
                "enabled": self._file_enabled,
                "max_entries": self.max_entries,
                "memory_count": len(self._items),
                "log_dir": str(self._log_dir) if self._log_dir else None,
                "file_path": str(active) if active else None,
                "file_exists": bool(active and active.is_file()),
                "active_file_bytes": active_size,
                "max_file_bytes": self.max_file_bytes,
                "retention_days": self.retention_days,
                "file_count": len(files),
                "total_bytes": total,
            }

    def add(self, entry: RequestLog) -> None:
        cb: Callable[[RequestLog], None] | None
        with self._lock:
            self._items.appendleft(entry)
            if self._file_enabled and self._log_dir is not None:
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

    def _utc_day(self, ts: float | None = None) -> str:
        t = ts if ts is not None else time.time()
        return time.strftime("%Y-%m-%d", time.gmtime(t))

    def _current_active_path_unlocked(self) -> Path | None:
        if self._log_dir is None:
            return None
        day = self._utc_day()
        return self._log_dir / f"requests-{day}.log"

    def _append_file_unlocked(self, entry: RequestLog) -> None:
        assert self._log_dir is not None
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            day = self._utc_day(entry.ts)
            path = self._log_dir / f"requests-{day}.log"
            # New calendar day → reset active handle tracking
            if self._active_day != day:
                self._active_day = day
                self._active_path = path
            self._maybe_rotate_unlocked(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry.format_line() + "\n")
            self._active_path = path
            self._appends_since_maintain += 1
            if self._appends_since_maintain >= 200:
                self._purge_old_unlocked()
                self._appends_since_maintain = 0
        except Exception:
            logger.exception("failed to append request log file")

    def _maybe_rotate_unlocked(self, path: Path) -> None:
        """If ``path`` is at/over max size, rename to .N and start a fresh file."""
        try:
            if not path.is_file():
                return
            size = path.stat().st_size
            if size < self.max_file_bytes:
                return
            # Find next free index for this day
            day = path.name.removeprefix("requests-").removesuffix(".log")
            # day may already be YYYY-MM-DD
            n = 1
            while True:
                rotated = path.parent / f"requests-{day}.{n}.log"
                if not rotated.exists():
                    break
                n += 1
            path.rename(rotated)
            logger.info(
                "rotated request log %s → %s (size=%s >= %s)",
                path.name,
                rotated.name,
                size,
                self.max_file_bytes,
            )
        except Exception:
            logger.exception("request log rotation failed")

    def _list_log_files_unlocked(self) -> list[Path]:
        if self._log_dir is None or not self._log_dir.is_dir():
            return []
        out: list[Path] = []
        for p in self._log_dir.iterdir():
            if p.is_file() and _DATE_RE.match(p.name):
                out.append(p)
        return sorted(out)

    def _purge_old_unlocked(self) -> None:
        """Delete request log files older than retention_days (by date in name)."""
        if self._log_dir is None or not self._log_dir.is_dir():
            return
        cutoff = datetime.now(UTC).date() - timedelta(days=self.retention_days)
        deleted = 0
        for p in list(self._log_dir.iterdir()):
            if not p.is_file():
                continue
            m = _DATE_RE.match(p.name)
            if not m:
                # Legacy single-file name from older versions
                if p.name in {"request_logs.txt", "requests.log"}:
                    with suppress(OSError):
                        # Keep if still recent by mtime
                        mtime = datetime.fromtimestamp(
                            p.stat().st_mtime, tz=UTC
                        ).date()
                        if mtime < cutoff:
                            p.unlink(missing_ok=True)
                            deleted += 1
                continue
            try:
                file_day = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_day < cutoff:
                try:
                    p.unlink(missing_ok=True)
                    deleted += 1
                except OSError:
                    logger.warning("failed to delete old request log %s", p)
        if deleted:
            logger.info(
                "purged %s request log file(s) older than %s days",
                deleted,
                self.retention_days,
            )


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


def default_log_dir(sqlite_path: str | Path) -> Path:
    """Directory for rotating request logs beside the SQLite DB."""
    return Path(sqlite_path).expanduser().resolve().parent / DEFAULT_LOG_DIRNAME


def default_log_file_path(sqlite_path: str | Path) -> Path:
    """Active dated log file path for today (UTC)."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    return default_log_dir(sqlite_path) / f"requests-{day}.log"
