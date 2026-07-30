"""Multi-tenant accounts: signup → verify → approve → API key + scoped analytics."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from potato.accounts.store import AccountStore
from potato.analytics.models import TraceRecord
from potato.analytics.writer import TraceWriter
from potato.balancer import KeyPool
from potato.catalog.db import get_db
from potato.catalog.hub import ProviderHub
from potato.catalog.preferences import UserPreferences
from potato.catalog.providers import ProviderStore
from potato.config import Settings
from potato.main import _init_accounts, _init_analytics, create_app
from potato.routing import RoutingStats
from potato.safety import AccountGuard

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
        sqlite_path=str(Path(td.name) / "potato.db"),
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
        bad = await client.get("/v1/models", headers={"Authorization": "Bearer sk-nk-fake"})
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
        stats = await client.get("/stats", headers={"Authorization": f"Bearer {api_key}"})
        assert stats.status_code == 200

        # Pending/inactive keys rejected — rotate requires active user session
        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {api_key}"})
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
    store.create_user(
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
        other = store.create_user("stale@example.com", "password123", role="user", status="active")
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
        r1 = await client.get("/analytics/traces", headers={"Authorization": f"Bearer {k1}"})
        assert r1.status_code == 200
        ids1 = {t["trace_id"] for t in r1.json()["traces"]}
        assert ids1 == {"t-u1"}

        r2 = await client.get("/analytics/traces", headers={"Authorization": f"Bearer {k2}"})
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
        r = await client.get("/admin/users", headers={"Authorization": f"Bearer {key}"})
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
        results.append(store.approve_and_issue_key(u["id"], approved_by="admin-a"))

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


@pytest.mark.asyncio
async def test_admin_list_and_revoke_user_keys():
    """Admin 'Keys & Info' modal + per-key revoke must work end-to-end.

    Regression: routes called store.list_api_keys()/revoke_api_key() which did
    not exist, causing 500s on GET /admin/users/{id}/keys and the revoke POST.
    """
    app, _ = _make_app()
    store: AccountStore = app.state.accounts
    store.create_user("admin@ex.com", "password123", role="admin", status="active")
    target = store.create_user("target@ex.com", "password123", role="user", status="active")
    issued = store.issue_api_key(target["id"])
    kid = issued["id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "admin@ex.com", "password": "password123"}
        )

        # List keys for the target user
        r = await client.get(f"/admin/users/{target['id']}/keys")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user_id"] == target["id"]
        assert len(body["keys"]) == 1
        assert body["keys"][0]["id"] == kid
        assert body["keys"][0]["key_prefix"] == issued["key_prefix"]

        # Revoke the key
        rev = await client.post(
            f"/admin/users/{target['id']}/keys/{kid}/revoke"
        )
        assert rev.status_code == 200, rev.text
        assert rev.json()["ok"] is True

        # Second revoke is idempotent-not-found (already revoked)
        rev2 = await client.post(
            f"/admin/users/{target['id']}/keys/{kid}/revoke"
        )
        assert rev2.status_code == 404

        # Key now shows revoked in the list
        r2 = await client.get(f"/admin/users/{target['id']}/keys")
        assert r2.json()["keys"][0]["revoked_at"] is not None


def test_revoke_api_key_scoped_to_owner():
    """revoke_api_key must not touch keys owned by a different user."""
    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    db = get_db(Path(td.name) / "revoke.db")
    store = AccountStore(db)
    a = store.create_user("a@ex.com", "password123", status="active")
    b = store.create_user("b@ex.com", "password123", status="active")
    ka = store.issue_api_key(a["id"])
    store.issue_api_key(b["id"])

    # Try to revoke A's key using B's user_id → must fail (no row updated)
    assert store.revoke_api_key(b["id"], ka["id"]) is False
    # A's key is still active
    a_keys = store.list_keys_for_user(a["id"])
    assert a_keys[0]["revoked_at"] is None
    # Revoking with the correct owner works
    assert store.revoke_api_key(a["id"], ka["id"]) is True
    a_keys = store.list_keys_for_user(a["id"])
    assert a_keys[0]["revoked_at"] is not None
    # B's key untouched
    b_keys = store.list_keys_for_user(b["id"])
    assert b_keys[0]["revoked_at"] is None


@pytest.mark.asyncio
async def test_auth_me_includes_connection_endpoints():
    """/auth/me returns connection.base_url + endpoint paths so the user/keys
    page can render real URLs instead of a hardcoded host."""
    app, settings = _make_app()
    store: AccountStore = app.state.accounts
    store.create_user("conn@ex.com", "password123", role="user", status="active")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "conn@ex.com", "password": "password123"}
        )
        me = await client.get("/auth/me")
        assert me.status_code == 200
        body = me.json()
        assert body["authenticated"] is True
        conn = body.get("connection")
        assert conn is not None, "connection block missing from /auth/me"
        assert conn["base_url"] == settings.public_base_url
        eps = conn["endpoints"]
        assert eps["openai_chat_completions"].endswith("/v1/chat/completions")
        assert eps["openai_models"].endswith("/v1/models")
        assert eps["anthropic_messages"].endswith("/v1/messages")
        assert eps["health"].endswith("/health")
        assert eps["dashboard"].endswith("/dashboard")


def test_keys_are_never_hard_deleted_only_revoked():
    """Invariant: we never DELETE api_keys rows — we only set revoked_at.

    Covers rotation (issue_api_key revokes prior keys), explicit revoke, and
    admin approve_and_issue_key. After all operations every key ever issued
    must still be present as a row, with revoked_at set on the inactive ones.
    Protects against accidental hard-deletes and admin-key deletion.
    """
    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    db = get_db(Path(td.name) / "invariant.db")
    store = AccountStore(db)
    u = store.create_user("inv@ex.com", "password123", role="user", status="active")

    k1 = store.issue_api_key(u["id"])
    k2 = store.issue_api_key(u["id"])  # rotation: revokes k1, inserts k2
    assert store.revoke_api_key(u["id"], k2["id"]) is True

    # approve_and_issue_key on a re-approved user also revokes + inserts
    store.set_status(u["id"], "suspended")
    store.approve_and_issue_key(u["id"], approved_by="admin")

    rows = store.list_keys_for_user(u["id"])
    # Every key ever issued is still a row — none were hard-deleted
    ids = {r["id"] for r in rows}
    assert {k1["id"], k2["id"]}.issubset(ids), ids
    # k1 and k2 are revoked (inactive), the approve-issued key is active
    active = [r for r in rows if r["revoked_at"] is None]
    assert len(active) == 1
    # No row has been physically removed
    assert len(rows) == 3
