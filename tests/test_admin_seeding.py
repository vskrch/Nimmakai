"""P0-1: first-time admin onboarding seed.

When ``admin_password`` is set, ``_init_accounts`` seeds an active admin
account with ``admin_email`` so deploy.sh installs can sign in immediately.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from potato.accounts.store import STATUS_ACTIVE, AccountStore
from potato.config import Settings
from potato.main import _init_accounts, create_app

_temp_dirs: list[tempfile.TemporaryDirectory] = []


def _td() -> str:
    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    return td.name


def _settings(**kw) -> Settings:
    base = Path(_td())
    return Settings(
        proxy_api_keys=["sk-admin-breakglass"],
        allow_insecure_auth=False,
        nim_api_keys=["test-key-1"],
        nim_base_url="https://integrate.api.nvidia.com/v1",
        providers_overlay_path=str(base / "providers.json"),
        catalog_snapshot_path=str(base / "catalog_snapshot.json"),
        sqlite_path=str(base / "potato.db"),
        sqlite_seed_free_presets=False,
        analytics_enabled=False,
        admin_emails=[],
        **kw,
    )


def test_seeds_admin_when_password_set():
    """Fresh DB + admin_password → one active admin user with an API key."""
    settings = _settings(
        admin_email="admin@localhost",
        admin_password="hunter2",
    )
    app = create_app(settings)
    _init_accounts(app, settings)

    store: AccountStore = app.state.accounts
    assert store is not None
    user = store.get_user_by_email("admin@localhost")
    assert user is not None
    assert user["role"] == "admin"
    assert user["status"] == STATUS_ACTIVE
    # Verify password authenticates
    authed = store.authenticate("admin@localhost", "hunter2")
    assert authed is not None
    # An API key was issued
    keys = store.list_keys_for_user(user["id"])
    assert len(keys) >= 1


def test_no_seed_when_password_absent():
    """No admin_password → no seed, no admin user created."""
    settings = _settings(admin_password=None)
    app = create_app(settings)
    _init_accounts(app, settings)

    store: AccountStore = app.state.accounts
    assert store is not None
    assert store.get_user_by_email("admin@localhost") is None


def test_seed_idempotent_on_restart():
    """Second call does not duplicate the user or issue a second key."""
    settings = _settings(
        admin_email="admin@localhost",
        admin_password="hunter2",
    )
    app = create_app(settings)
    _init_accounts(app, settings)
    store: AccountStore = app.state.accounts
    user_first = store.get_user_by_email("admin@localhost")
    assert user_first is not None

    # Simulate restart — call _init_accounts again
    _init_accounts(app, settings)
    user_second = store.get_user_by_email("admin@localhost")
    assert user_second is not None
    assert user_second["id"] == user_first["id"]
    assert user_second["role"] == "admin"
    assert user_second["status"] == STATUS_ACTIVE


def test_seed_promotes_existing_non_admin():
    """Existing user with role=user → promoted to admin + activated."""
    settings = _settings(
        admin_email="existing@example.com",
        admin_password="hunter2",
    )
    app = create_app(settings)
    # Pre-create a normal user before seeding
    from potato.catalog.db import get_db as _get_db

    db = _get_db(settings.sqlite_path)
    pre_store = AccountStore(db)
    pre_store.create_user(
        "existing@example.com",
        "hunter2",
        role="user",
        status="unverified",
    )

    _init_accounts(app, settings)
    store: AccountStore = app.state.accounts
    user = store.get_user_by_email("existing@example.com")
    assert user is not None
    assert user["role"] == "admin"
    assert user["status"] == STATUS_ACTIVE


def test_auth_flows_through_dashboard_signin():
    """The seeded admin can authenticate via the /auth/login endpoint."""
    import asyncio

    from httpx import ASGITransport, AsyncClient

    settings = _settings(
        admin_email="admin@localhost",
        admin_password="hunter2",
    )
    app = create_app(settings)
    _init_accounts(app, settings)

    async def _run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/auth/login",
                json={"email": "admin@localhost", "password": "hunter2"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body.get("ok") is not False
            # /auth/me should confirm authenticated + admin
            me = await client.get("/auth/me")
            assert me.status_code == 200
            me_body = me.json()
            assert me_body["authenticated"] is True
            assert me_body["is_admin"] is True

    asyncio.run(_run())
