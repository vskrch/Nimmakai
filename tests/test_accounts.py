"""Multi-tenant accounts: signup → verify → approve → API key + scoped analytics."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from nimmakai.accounts.store import AccountStore
from nimmakai.analytics.models import TraceRecord
from nimmakai.analytics.writer import TraceWriter
from nimmakai.balancer import KeyPool
from nimmakai.catalog.db import get_db
from nimmakai.catalog.hub import ProviderHub
from nimmakai.catalog.preferences import UserPreferences
from nimmakai.catalog.providers import ProviderStore
from nimmakai.config import Settings
from nimmakai.main import _init_accounts, _init_analytics, create_app
from nimmakai.routing import RoutingStats
from nimmakai.safety import AccountGuard

_temp_dirs: list[tempfile.TemporaryDirectory] = []


def _make_app(*, admin_emails: list[str] | None = None):
    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    settings = Settings(
        proxy_api_keys=["sk-admin-breakglass"],
        allow_insecure_auth=False,
        nim_api_keys=["test-key-1"],
        nim_base_url="https://integrate.api.nvidia.com/v1",
        providers_overlay_path=str(Path(td.name) / "providers.json"),
        catalog_snapshot_path=str(Path(td.name) / "catalog_snapshot.json"),
        sqlite_path=str(Path(td.name) / "nimmakai.db"),
        sqlite_seed_free_presets=False,
        analytics_enabled=True,
        analytics_flush_interval=0.05,
        analytics_batch_size=5,
        admin_emails=admin_emails or ["admin@example.com"],
        email_backend="stub",
        public_base_url="http://testserver",
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
    _init_accounts(app, settings)
    _init_analytics(app, settings)
    return app, settings


@pytest.mark.asyncio
async def test_signup_verify_approve_issues_key():
    app, _ = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/auth/signup",
            json={"email": "user@example.com", "password": "password123"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["user"]["status"] == "unverified"
        assert "verify_url" in body
        token = body["verify_url"].split("token=")[-1]

        v = await client.get(f"/auth/verify?token={token}")
        assert v.status_code == 200
        assert v.json()["user"]["status"] == "pending_approval"

        # User cannot call /v1 yet
        bad = await client.get(
            "/v1/models", headers={"Authorization": "Bearer sk-nk-fake"}
        )
        assert bad.status_code == 401

        # Admin approves with break-glass key
        users = await client.get(
            "/admin/users?status=pending_approval",
            headers={"Authorization": "Bearer sk-admin-breakglass"},
        )
        assert users.status_code == 200
        uid = users.json()["users"][0]["id"]

        appr = await client.post(
            f"/admin/users/{uid}/approve",
            headers={"Authorization": "Bearer sk-admin-breakglass"},
        )
        assert appr.status_code == 200, appr.text
        api_key = appr.json()["api_key"]
        assert api_key.startswith("sk-nk-")

        # Active key auth works (stats requires auth)
        stats = await client.get(
            "/stats", headers={"Authorization": f"Bearer {api_key}"}
        )
        assert stats.status_code == 200

        # Pending/inactive keys rejected — rotate requires active user session
        me = await client.get(
            "/auth/me", headers={"Authorization": f"Bearer {api_key}"}
        )
        assert me.status_code == 200
        assert me.json()["authenticated"] is True
        assert me.json()["user"]["status"] == "active"


@pytest.mark.asyncio
async def test_admin_email_auto_activates_on_verify():
    app, _ = _make_app(admin_emails=["boss@example.com"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/auth/signup",
            json={"email": "boss@example.com", "password": "password123"},
        )
        assert r.status_code == 201
        token = r.json()["verify_url"].split("token=")[-1]
        v = await client.get(f"/auth/verify?token={token}")
        assert v.status_code == 200
        data = v.json()
        assert data["user"]["status"] == "active"
        assert data["user"]["role"] == "admin"
        assert data["api_key"].startswith("sk-nk-")


@pytest.mark.asyncio
async def test_login_session_and_me():
    app, settings = _make_app()
    store: AccountStore = app.state.accounts
    user = store.create_user("a@b.co", "password123", role="user", status="active")
    store.issue_api_key(user["id"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/auth/login", json={"email": "a@b.co", "password": "password123"}
        )
        assert login.status_code == 200
        cookie = settings.session_cookie_name
        assert cookie in login.cookies

        me = await client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["authenticated"] is True
        assert me.json()["user"]["email"] == "a@b.co"


@pytest.mark.asyncio
async def test_suspended_admin_session_loses_proxy_and_admin_access():
    app, _settings = _make_app()
    store: AccountStore = app.state.accounts
    admin = store.create_user(
        "suspended-admin@example.com",
        "password123",
        role="admin",
        status="active",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/auth/login",
            json={"email": "suspended-admin@example.com", "password": "password123"},
        )
        assert login.status_code == 200
        assert (await client.get("/stats")).status_code == 200
        assert (await client.get("/admin/users")).status_code == 200

        store.set_status(admin["id"], "suspended")

        proxy = await client.get("/stats")
        assert proxy.status_code == 403
        assert proxy.json()["error"]["code"] == "account_not_active"
        admin_api = await client.get("/admin/users")
        assert admin_api.status_code == 403
        assert admin_api.json()["error"]["code"] == "account_not_active"


@pytest.mark.asyncio
async def test_suspend_endpoint_revokes_sessions():
    app, _settings = _make_app()
    store: AccountStore = app.state.accounts
    admin = store.create_user(
        "revoker@example.com",
        "password123",
        role="admin",
        status="active",
    )
    victim = store.create_user(
        "victim@example.com",
        "password123",
        role="user",
        status="active",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as admin_c:
        login = await admin_c.post(
            "/auth/login",
            json={"email": "revoker@example.com", "password": "password123"},
        )
        assert login.status_code == 200

        async with AsyncClient(transport=transport, base_url="http://test") as victim_c:
            vlogin = await victim_c.post(
                "/auth/login",
                json={"email": "victim@example.com", "password": "password123"},
            )
            assert vlogin.status_code == 200
            assert (await victim_c.get("/auth/me")).json()["authenticated"] is True

            sus = await admin_c.post(f"/admin/users/{victim['id']}/suspend")
            assert sus.status_code == 200

            me = await victim_c.get("/auth/me")
            # Session cookie revoked → unauthenticated
            assert me.json().get("authenticated") is not True


@pytest.mark.asyncio
async def test_bearer_api_key_overrides_stale_session_cookie():
    """Explicit Bearer wins over cookie so break-glass keys work in the browser."""
    app, _settings = _make_app()
    store: AccountStore = app.state.accounts
    admin = store.create_user(
        "bearer-admin@example.com",
        "password123",
        role="admin",
        status="active",
    )
    issued = store.issue_api_key(admin["id"])["api_key"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Establish a session for a different suspended user
        other = store.create_user(
            "stale@example.com", "password123", role="user", status="active"
        )
        await client.post(
            "/auth/login",
            json={"email": "stale@example.com", "password": "password123"},
        )
        store.set_status(other["id"], "suspended")

        # Cookie alone would be 403; Bearer admin key must succeed
        r = await client.get(
            "/stats",
            headers={"Authorization": f"Bearer {issued}"},
        )
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_analytics_scoped_to_user():
    app, _ = _make_app()
    store: AccountStore = app.state.accounts
    u1 = store.create_user("u1@ex.com", "password123", status="active")
    u2 = store.create_user("u2@ex.com", "password123", status="active")
    k1 = store.issue_api_key(u1["id"])["api_key"]
    k2 = store.issue_api_key(u2["id"])["api_key"]

    writer: TraceWriter = app.state.trace_writer
    await writer.start()
    now = time.time()
    try:
        for tid, uid in (("t-u1", u1["id"]), ("t-u2", u2["id"])):
            writer.enqueue(
                TraceRecord(
                    trace_id=tid,
                    created_at=now,
                    path="/v1/chat/completions",
                    user_id=uid,
                    model_routed="zen/mimo",
                    status_code=200,
                    success=True,
                    duration_ms=10,
                )
            )
        deadline = time.time() + 3
        while writer.flushed < 2 and time.time() < deadline:
            await asyncio.sleep(0.05)
        assert writer.flushed >= 2
    finally:
        await writer.stop()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.get(
            "/analytics/traces", headers={"Authorization": f"Bearer {k1}"}
        )
        assert r1.status_code == 200
        ids1 = {t["trace_id"] for t in r1.json()["traces"]}
        assert ids1 == {"t-u1"}

        r2 = await client.get(
            "/analytics/traces", headers={"Authorization": f"Bearer {k2}"}
        )
        ids2 = {t["trace_id"] for t in r2.json()["traces"]}
        assert ids2 == {"t-u2"}

        # Admin sees both
        adm = await client.get(
            "/analytics/traces",
            headers={"Authorization": "Bearer sk-admin-breakglass"},
        )
        ids_a = {t["trace_id"] for t in adm.json()["traces"]}
        assert ids_a == {"t-u1", "t-u2"}


@pytest.mark.asyncio
async def test_non_admin_cannot_list_users():
    app, _ = _make_app()
    store: AccountStore = app.state.accounts
    u = store.create_user("u@ex.com", "password123", status="active")
    key = store.issue_api_key(u["id"])["api_key"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(
            "/admin/users", headers={"Authorization": f"Bearer {key}"}
        )
        assert r.status_code == 403


def test_account_store_password_and_key_roundtrip():
    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    db = get_db(Path(td.name) / "t.db")
    store = AccountStore(db)
    u = store.create_user("x@y.z", "secretpass")
    assert store.authenticate("x@y.z", "secretpass")
    assert store.authenticate("x@y.z", "wrong") is None
    raw = store.create_verify_token(u["id"])
    assert store.consume_verify_token(raw) == u["id"]
    assert store.consume_verify_token(raw) is None  # one-time


def test_concurrent_approve_issues_one_key():
    """Two approve_and_issue_key calls must not leave two valid plaintext keys."""
    import concurrent.futures

    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    db = get_db(Path(td.name) / "approve.db")
    store = AccountStore(db)
    u = store.create_user("race@ex.com", "password123", status="pending_approval")

    results: list[dict] = []

    def _approve():
        results.append(
            store.approve_and_issue_key(u["id"], approved_by="admin-a")
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_approve) for _ in range(2)]
        for f in futs:
            f.result()

    winners = [r for r in results if r.get("ok") and r.get("api_key")]
    assert len(winners) == 1, results
    # Exactly one non-revoked key in DB
    keys = [k for k in store.list_keys_for_user(u["id"]) if not k.get("revoked_at")]
    assert len(keys) == 1
    user = store.get_user(u["id"])
    assert user and user["status"] == "active"
