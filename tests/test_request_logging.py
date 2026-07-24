"""Durable request log ring + toggle."""

from __future__ import annotations

from pathlib import Path

from nimmakai.logging_setup import RequestLog, RequestLogStore, default_log_file_path


def test_default_log_path_beside_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "data" / "nimmakai.db"
    db.parent.mkdir()
    assert default_log_file_path(db) == tmp_path / "data" / "request_logs.txt"


def test_file_ring_writes_and_trims(tmp_path: Path) -> None:
    path = tmp_path / "request_logs.txt"
    store = RequestLogStore(max_entries=5)
    store.configure(file_path=path, enabled=True, max_entries=5)
    for i in range(8):
        store.add(
            RequestLog(
                id=f"r{i}",
                ts=1_700_000_000 + i,
                method="POST",
                path="/v1/chat/completions",
                status=200,
                duration_ms=10.0,
                model_routed=f"m{i}",
            )
        )
    assert path.is_file()
    store._trim_file_unlocked()  # force trim
    lines = path.read_text().strip().splitlines()
    assert len(lines) <= 5
    assert "req=r7" in lines[-1]
    assert store.count == 5


def test_disable_stops_file_writes(tmp_path: Path) -> None:
    path = tmp_path / "request_logs.txt"
    store = RequestLogStore(max_entries=100)
    store.configure(file_path=path, enabled=True)
    store.add(
        RequestLog(id="a", ts=1.0, method="GET", path="/health", status=200, duration_ms=1)
    )
    assert path.is_file()
    size1 = path.stat().st_size
    store.set_enabled(False)
    store.add(
        RequestLog(id="b", ts=2.0, method="GET", path="/health", status=200, duration_ms=1)
    )
    assert path.stat().st_size == size1 or "req=b" not in path.read_text()
    assert store.enabled is False
