"""Rotating durable request logs + toggle."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nimmakai.logging_setup import (
    RequestLog,
    RequestLogStore,
    default_log_dir,
    default_log_file_path,
)


def test_default_log_dir_beside_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "data" / "nimmakai.db"
    db.parent.mkdir()
    assert default_log_dir(db) == tmp_path / "data" / "request_logs"
    day = time.strftime("%Y-%m-%d", time.gmtime())
    assert default_log_file_path(db) == tmp_path / "data" / "request_logs" / f"requests-{day}.log"


def test_dated_append_and_memory_ring(tmp_path: Path) -> None:
    log_dir = tmp_path / "request_logs"
    store = RequestLogStore(max_entries=5)
    store.configure(log_dir=log_dir, enabled=True, max_entries=5, max_file_bytes=50 * 1024 * 1024)
    ts = time.time()
    for i in range(8):
        store.add(
            RequestLog(
                id=f"r{i}",
                ts=ts + i,
                method="POST",
                path="/v1/chat/completions",
                status=200,
                duration_ms=10.0,
                model_routed=f"m{i}",
            )
        )
    day = time.strftime("%Y-%m-%d", time.gmtime(ts))
    path = log_dir / f"requests-{day}.log"
    assert path.is_file()
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 8  # perpetual on disk — not trimmed to memory ring
    assert "req=r7" in lines[-1]
    assert store.count == 5  # memory still capped


def test_rotate_at_max_bytes(tmp_path: Path) -> None:
    log_dir = tmp_path / "request_logs"
    store = RequestLogStore(max_entries=100)
    # Tiny limit so one fat line forces rotation
    store.configure(log_dir=log_dir, enabled=True, max_file_bytes=80)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    active = log_dir / f"requests-{day}.log"
    # Seed a large enough file (configure already created the dir)
    active.write_text("x" * 100 + "\n", encoding="utf-8")
    store.add(
        RequestLog(
            id="after",
            ts=time.time(),
            method="GET",
            path="/health",
            status=200,
            duration_ms=1.0,
        )
    )
    rotated = log_dir / f"requests-{day}.1.log"
    assert rotated.is_file()
    assert rotated.stat().st_size >= 100
    assert active.is_file()
    assert "req=after" in active.read_text()


def test_purge_older_than_retention(tmp_path: Path) -> None:
    log_dir = tmp_path / "request_logs"
    log_dir.mkdir()
    old_day = (datetime.now(UTC).date() - timedelta(days=100)).isoformat()
    keep_day = (datetime.now(UTC).date() - timedelta(days=10)).isoformat()
    old = log_dir / f"requests-{old_day}.log"
    keep = log_dir / f"requests-{keep_day}.log"
    old.write_text("old\n")
    keep.write_text("keep\n")
    # Also a rotated old shard
    old_rot = log_dir / f"requests-{old_day}.1.log"
    old_rot.write_text("oldrot\n")

    store = RequestLogStore()
    store.configure(log_dir=log_dir, enabled=True, retention_days=90)
    assert not old.exists()
    assert not old_rot.exists()
    assert keep.exists()


def test_disable_stops_file_writes(tmp_path: Path) -> None:
    log_dir = tmp_path / "request_logs"
    store = RequestLogStore(max_entries=100)
    store.configure(log_dir=log_dir, enabled=True)
    store.add(
        RequestLog(id="a", ts=time.time(), method="GET", path="/health", status=200, duration_ms=1)
    )
    day = time.strftime("%Y-%m-%d", time.gmtime())
    path = log_dir / f"requests-{day}.log"
    assert path.is_file()
    size1 = path.stat().st_size
    store.set_enabled(False)
    store.add(
        RequestLog(id="b", ts=time.time(), method="GET", path="/health", status=200, duration_ms=1)
    )
    assert path.stat().st_size == size1
    assert "req=b" not in path.read_text()
    assert store.enabled is False


def test_status_reports_rotation_fields(tmp_path: Path) -> None:
    log_dir = tmp_path / "request_logs"
    store = RequestLogStore()
    store.configure(
        log_dir=log_dir,
        enabled=True,
        max_file_bytes=50 * 1024 * 1024,
        retention_days=90,
    )
    store.add(
        RequestLog(id="s", ts=time.time(), method="GET", path="/health", status=200, duration_ms=1)
    )
    st = store.status()
    assert st["enabled"] is True
    assert st["max_file_bytes"] == 50 * 1024 * 1024
    assert st["retention_days"] == 90
    assert st["log_dir"] == str(log_dir)
    assert st["file_count"] >= 1
    assert st["total_bytes"] > 0
