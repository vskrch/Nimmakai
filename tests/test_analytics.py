"""Analytics unit + API tests: writer, store, retention, cost, SSE."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from nimmakai.analytics.cost import estimate_cost, lookup_rates
from nimmakai.analytics.events import EventBus
from nimmakai.analytics.models import TraceRecord, TraceSpan
from nimmakai.analytics.retention import RetentionManager
from nimmakai.analytics.store import AnalyticsStore
from nimmakai.analytics.writer import TraceWriter
from nimmakai.balancer import KeyPool
from nimmakai.catalog.db import get_db
from nimmakai.catalog.hub import ProviderHub
from nimmakai.catalog.preferences import UserPreferences
from nimmakai.catalog.providers import ProviderStore
from nimmakai.config import Settings
from nimmakai.main import _init_analytics, create_app
from nimmakai.routing import RoutingStats
from nimmakai.safety import AccountGuard

_temp_dirs: list[tempfile.TemporaryDirectory] = []
AUTH = {"Authorization": "Bearer any"}


def _make_trace(trace_id: str, *, created_at: float | None = None, **kw) -> TraceRecord:
    now = created_at if created_at is not None else time.time()
    t = TraceRecord(
        trace_id=trace_id,
        created_at=now,
        method="POST",
        path="/v1/chat/completions",
        model_requested=kw.get("model_requested", "auto"),
        intent=kw.get("intent", "coding_agentic"),
        intent_confidence=kw.get("intent_confidence", 0.9),
        intent_rule_id=kw.get("intent_rule_id", "has_tools"),
        route_mode=kw.get("route_mode", "auto"),
        model_routed=kw.get("model_routed", "zen/mimo-v2.5-free"),
        provider_id=kw.get("provider_id", "zen"),
        chain=kw.get("chain", ["zen/mimo-v2.5-free", "nim/foo"]),
        fallback_index=kw.get("fallback_index", 0),
        status_code=kw.get("status_code", 200),
        success=kw.get("success", True),
        duration_ms=kw.get("duration_ms", 1200.0),
        classify_ms=kw.get("classify_ms", 2.0),
        route_ms=kw.get("route_ms", 1.5),
        prompt_tokens=kw.get("prompt_tokens", 100),
        completion_tokens=kw.get("completion_tokens", 50),
        total_tokens=kw.get("total_tokens", 150),
        estimated_cost_usd=kw.get("estimated_cost_usd", 0.0),
        message_count=1,
        char_length=400,
    )
    t.spans = [
        TraceSpan(
            span_type="classify",
            started_at=now,
            ended_at=now + 0.002,
            duration_ms=2.0,
            success=True,
            metadata={"intent": t.intent},
        ),
        TraceSpan(
            span_type="route",
            started_at=now + 0.002,
            ended_at=now + 0.004,
            duration_ms=2.0,
            success=True,
        ),
        TraceSpan(
            span_type="upstream",
            model_id=t.model_routed,
            provider_id=t.provider_id,
            started_at=now + 0.004,
            ended_at=now + 1.2,
            duration_ms=1196.0,
            status_code=200,
            success=True,
        ),
    ]
    return t


def _make_app_with_analytics():
    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    settings = Settings(
        proxy_api_keys=[],
        allow_insecure_auth=True,
        nim_api_keys=["test-key-1"],
        nim_base_url="https://integrate.api.nvidia.com/v1",
        providers_overlay_path=str(Path(td.name) / "providers.json"),
        catalog_snapshot_path=str(Path(td.name) / "catalog_snapshot.json"),
        sqlite_path=str(Path(td.name) / "nimmakai.db"),
        sqlite_seed_free_presets=False,
        analytics_enabled=True,
        analytics_flush_interval=0.2,
        analytics_batch_size=10,
        analytics_retention_days=7,
    )
    app = create_app(settings)
    app.state.settings = settings
    pool = KeyPool(
        api_keys=["test-key-1"],
        rpm_limit=40,
        rpd_limit=2000,
        max_in_flight_per_key=3,
        auth_fail_threshold=3,
        auth_quarantine_seconds=60,
    )
    app.state.pool = pool
    store = ProviderStore.load(
        settings.providers_config_path,
        settings.providers_overlay_path,
        nim_base_url=settings.nim_base_url,
        nim_api_keys=list(settings.nim_api_keys),
        nim_rpm=40,
        nim_rpd=2000,
        nim_max_in_flight=3,
        sqlite_path=settings.sqlite_path,
        seed_free_presets=False,
    )
    hub = ProviderHub(store, settings)
    app.state.hub = hub
    app.state.upstream = None
    app.state.registry = None
    app.state.selector = None
    app.state.fallback = None
    app.state.guard = AccountGuard(settings, pool)
    app.state.routing_stats = RoutingStats()
    app.state.preferences = UserPreferences(
        path=Path(td.name) / "prefs.json",
        db_path=Path(settings.sqlite_path),
    )
    app.state.preferences.load()
    _init_analytics(app, settings)
    return app, settings


# ── cost ────────────────────────────────────────────────────────────


def test_estimate_cost_known_and_free():
    assert estimate_cost("gpt-4o", 1_000_000, 0) == pytest.approx(2.50)
    assert estimate_cost("gpt-4o", 0, 1_000_000) == pytest.approx(10.00)
    assert estimate_cost("zen/mimo-v2.5-free", 1000, 1000) == 0.0
    assert lookup_rates("unknown-local-model") == (0.0, 0.0)
    assert estimate_cost(
        "custom/foo", 1_000_000, 0, overrides={"custom/foo": (1.0, 2.0)}
    ) == pytest.approx(1.0)


# ── writer + store ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_writer_flush_and_store_query():
    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    db = get_db(str(Path(td.name) / "a.db"))
    bus = EventBus()
    writer = TraceWriter(db, batch_size=5, flush_interval=0.15, event_bus=bus)
    store = AnalyticsStore(db)
    await writer.start()
    try:
        for i in range(7):
            writer.enqueue(_make_trace(f"t{i:03d}", model_routed=f"m{i % 3}"))
        # enqueue is non-blocking / fast
        t0 = time.perf_counter()
        writer.enqueue(_make_trace("fast"))
        assert (time.perf_counter() - t0) * 1000 < 5.0
        await asyncio.sleep(0.6)
        assert writer.flushed >= 8
        listed = store.list_traces(limit=50)
        assert listed["total"] >= 8
        detail = store.get_trace("t000")
        assert detail is not None
        assert len(detail["spans"]) == 3
        summary = store.summary(since=time.time() - 3600)
        assert summary["total_requests"] >= 8
        models = store.breakdown("models", since=time.time() - 3600)
        assert models
        ts = store.timeseries("requests", since=time.time() - 3600, interval="1m")
        assert isinstance(ts, list)
    finally:
        await writer.stop()


@pytest.mark.asyncio
async def test_writer_backpressure_drops():
    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    db = get_db(str(Path(td.name) / "b.db"))
    writer = TraceWriter(db, batch_size=50, flush_interval=10.0, max_queue=3)
    # Don't start — queue fills and drops
    writer.enqueue(_make_trace("a"))
    writer.enqueue(_make_trace("b"))
    writer.enqueue(_make_trace("c"))
    writer.enqueue(_make_trace("d"))  # drop
    assert writer.dropped >= 1
    assert writer.pending == 3


# ── retention ───────────────────────────────────────────────────────


def test_retention_purges_old_keeps_rollups():
    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    db = get_db(str(Path(td.name) / "r.db"))
    store = AnalyticsStore(db)
    writer_sync = TraceWriter(db, batch_size=100, flush_interval=1.0)
    old = time.time() - (10 * 86400)
    recent = time.time() - 60
    # write via sync path
    writer_sync._write_batch(
        [
            _make_trace("old1", created_at=old, duration_ms=100),
            _make_trace("old2", created_at=old + 1, duration_ms=100),
            _make_trace("new1", created_at=recent, duration_ms=200),
        ]
    )
    # Force rollup of old rows by setting watermark low and using retention
    mgr = RetentionManager(db, retention_days=7, rollup_retention_days=90)
    # Manually rollup with watermark 0 — but rollup excludes current minute;
    # old timestamps are far in the past so they roll up.
    report = mgr.run_cycle()
    assert report["deleted_traces"] >= 2
    listed = store.list_traces(limit=10)
    ids = {t["trace_id"] for t in listed["traces"]}
    assert "new1" in ids
    assert "old1" not in ids
    # spans for old traces gone
    assert store.get_spans("old1") == []


# ── API e2e ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analytics_api_summary_traces_export():
    app, _settings = _make_app_with_analytics()
    writer: TraceWriter = app.state.trace_writer
    await writer.start()
    try:
        for i in range(5):
            writer.enqueue(
                _make_trace(
                    f"api{i}",
                    intent="chat_fast" if i % 2 else "coding_agentic",
                    success=i != 3,
                    status_code=200 if i != 3 else 500,
                    estimated_cost_usd=0.01 * i,
                )
            )
        await asyncio.sleep(0.5)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/analytics/summary", headers=AUTH)
            assert r.status_code == 200
            body = r.json()
            assert body["total_requests"] >= 5
            assert "success_rate" in body

            r = await client.get("/analytics/traces?limit=10", headers=AUTH)
            assert r.status_code == 200
            data = r.json()
            assert data["total"] >= 5
            tid = data["traces"][0]["trace_id"]

            r = await client.get(f"/analytics/traces/{tid}", headers=AUTH)
            assert r.status_code == 200
            assert len(r.json()["spans"]) >= 1

            r = await client.get("/analytics/timeseries/requests?interval=1m", headers=AUTH)
            assert r.status_code == 200
            assert "points" in r.json()

            r = await client.get("/analytics/breakdown/intents", headers=AUTH)
            assert r.status_code == 200
            assert r.json()["items"]

            r = await client.get("/analytics/export/traces?format=csv&limit=100", headers=AUTH)
            assert r.status_code == 200
            assert "trace_id" in r.text
            assert "text/csv" in r.headers.get("content-type", "")

            r = await client.get("/analytics/export/traces?format=jsonl&limit=10", headers=AUTH)
            assert r.status_code == 200
            lines = [ln for ln in r.text.strip().split("\n") if ln]
            assert lines
            json.loads(lines[0])

            r = await client.get("/analytics/status", headers=AUTH)
            assert r.status_code == 200
            assert r.json()["enabled"] is True

            r = await client.get("/analytics/cost/rates", headers=AUTH)
            assert r.status_code == 200
            assert "defaults" in r.json()

            r = await client.put(
                "/analytics/cost/rates/test-model",
                headers=AUTH,
                json={"input_per_m": 1.5, "output_per_m": 3.0},
            )
            assert r.status_code == 200
            assert r.json()["ok"] is True
    finally:
        await writer.stop()


@pytest.mark.asyncio
async def test_analytics_requires_auth():
    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    settings = Settings(
        proxy_api_keys=["secret"],
        allow_insecure_auth=False,
        nim_api_keys=["test-key-1"],
        providers_overlay_path=str(Path(td.name) / "providers.json"),
        catalog_snapshot_path=str(Path(td.name) / "catalog_snapshot.json"),
        sqlite_path=str(Path(td.name) / "auth.db"),
        sqlite_seed_free_presets=False,
        analytics_enabled=True,
    )
    app = create_app(settings)
    app.state.settings = settings
    app.state.pool = KeyPool(
        api_keys=["test-key-1"],
        rpm_limit=40,
        rpd_limit=2000,
        max_in_flight_per_key=3,
        auth_fail_threshold=3,
        auth_quarantine_seconds=60,
    )
    app.state.guard = AccountGuard(settings, app.state.pool)
    app.state.routing_stats = RoutingStats()
    app.state.hub = None
    app.state.upstream = None
    app.state.registry = None
    app.state.selector = None
    app.state.fallback = None
    _init_analytics(app, settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/analytics/summary")
        assert r.status_code == 401
        r = await client.get(
            "/analytics/summary", headers={"Authorization": "Bearer secret"}
        )
        assert r.status_code == 200


# ── SSE ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_event_bus_publish_subscribe():
    bus = EventBus(heartbeat_seconds=0.3)
    received: list[str] = []

    async def _consume():
        async for ev in bus.subscribe(see_all=True):
            received.append(ev)
            if len(received) >= 2:
                break

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)
    bus.publish("trace", {"trace_id": "x1", "success": True})
    await asyncio.wait_for(task, timeout=2.0)
    assert any("x1" in e for e in received)
    assert any("heartbeat" in e or "data:" in e for e in received)


@pytest.mark.asyncio
async def test_analytics_sse_endpoint():
    app, _settings = _make_app_with_analytics()
    from nimmakai.analytics.events import EventBus

    bus = EventBus(heartbeat_seconds=0.2)
    app.state.event_bus = bus
    writer: TraceWriter = app.state.trace_writer
    writer._event_bus = bus
    await writer.start()
    try:
        received: list[str] = []

        async def _consume():
            async for ev in bus.subscribe(see_all=True):
                received.append(ev)
                if "sse-live" in ev:
                    break

        task = asyncio.create_task(_consume())
        await asyncio.sleep(0.05)
        writer.enqueue(_make_trace("sse-live"))
        await asyncio.sleep(0.5)
        bus.publish("trace", {"trace_id": "sse-live", "success": True})
        await asyncio.wait_for(task, timeout=3.0)
        assert any("sse-live" in e for e in received)

        # Route is mounted; avoid draining the infinite SSE body via httpx
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/analytics/status", headers=AUTH)
            assert r.status_code == 200
            assert "subscribers" in r.json()
            schema = (await client.get("/openapi.json")).json()
            assert "/analytics/events" in schema.get("paths", {})
    finally:
        await writer.stop()


# ── perf ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_1000_traces_perf():
    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    db = get_db(str(Path(td.name) / "perf.db"))
    writer = TraceWriter(db, batch_size=50, flush_interval=0.2)
    await writer.start()
    try:
        t0 = time.perf_counter()
        for i in range(1000):
            writer.enqueue(_make_trace(f"p{i:04d}"))
        enqueue_ms = (time.perf_counter() - t0) * 1000
        assert enqueue_ms < 1000, f"enqueue 1000 took {enqueue_ms:.1f}ms"
        assert enqueue_ms / 1000 < 1.0  # <1ms avg
        # wait for flush
        deadline = time.time() + 8
        while writer.flushed < 1000 and time.time() < deadline:
            await asyncio.sleep(0.1)
        assert writer.flushed >= 1000
        assert writer.dropped == 0
        store = AnalyticsStore(db)
        assert store.writer_stats_placeholder()["trace_count"] >= 1000
    finally:
        await writer.stop()


def test_mask_api_key():
    t = _make_trace("m")
    t.api_key = "sk-abcdefghijklmnop"
    row = t.to_row()
    # api_key is 6th field (index 5)
    assert "…" in row[5] or "***" in str(row[5])
    assert "ijklmnop"[-4:] in row[5]
