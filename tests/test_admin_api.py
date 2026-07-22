"""FastAPI endpoint integration tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from nimmakai.balancer import KeyPool
from nimmakai.catalog.hub import ProviderHub
from nimmakai.catalog.preferences import UserPreferences
from nimmakai.catalog.providers import ProviderStore
from nimmakai.config import Settings
from nimmakai.main import create_app
from nimmakai.routing import RoutingStats
from nimmakai.safety import AccountGuard

_temp_dirs = []


def _make_app():
    import tempfile
    from pathlib import Path
    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)  # keep reference alive

    settings = Settings(
        proxy_api_keys=[],
        allow_insecure_auth=True,
        nim_api_keys=["test-key-1", "test-key-2"],
        nim_base_url="https://integrate.api.nvidia.com/v1",
        nim_rpm_limit=40,
        nim_rpd_limit=2000,
        nim_max_in_flight_per_key=3,
        providers_overlay_path=str(Path(td.name) / "providers.json"),
        catalog_snapshot_path=str(Path(td.name) / "catalog_snapshot.json"),
        sqlite_path=str(Path(td.name) / "nimmakai.db"),
        sqlite_seed_free_presets=False,
    )
    app = create_app(settings)
    # Manually wire state (no lifespan in tests)
    app.state.settings = settings
    pool = KeyPool(
        api_keys=["test-key-1", "test-key-2"],
        rpm_limit=settings.effective_rpm,
        rpd_limit=settings.nim_rpd_limit,
        max_in_flight_per_key=settings.nim_max_in_flight_per_key,
        auth_fail_threshold=settings.auth_fail_threshold,
        auth_quarantine_seconds=settings.auth_quarantine_seconds,
    )
    app.state.pool = pool
    store = ProviderStore.load(
        settings.providers_config_path,
        settings.providers_overlay_path,
        nim_base_url=settings.nim_base_url,
        nim_api_keys=list(settings.nim_api_keys),
        nim_rpm=settings.nim_rpm_limit,
        nim_rpd=settings.nim_rpd_limit,
        nim_max_in_flight=settings.nim_max_in_flight_per_key,
        sqlite_path=settings.sqlite_path,
        seed_free_presets=False,
    )
    hub = ProviderHub(store, settings)
    # Can't run async hub.start() here; set hub and store
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
    return app


AUTH = {"Authorization": "Bearer any"}


@pytest.mark.asyncio
async def test_health_endpoint():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert "providers" in body


@pytest.mark.asyncio
async def test_admin_logs_endpoint():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/admin/logs", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert "entries" in body
        assert isinstance(body["entries"], list)


@pytest.mark.asyncio
async def test_root_endpoint():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "nimmakai"
        assert "dashboard" in body


@pytest.mark.asyncio
async def test_auth_required():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        assert (await c.get("/health")).status_code == 200
        for p in ["/stats", "/admin/providers", "/preferences",
                  "/ladder", "/catalog"]:
            assert (await c.get(p)).status_code == 401


@pytest.mark.asyncio
async def test_list_providers():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/admin/providers", headers=AUTH)
        assert r.status_code == 200
        providers = r.json()["providers"]
        assert len(providers) >= 1
        nim = next(p for p in providers if p["id"] == "nim")
        assert nim["builtin"] is True
        assert nim["key_count"] == 2


@pytest.mark.asyncio
async def test_add_provider():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post("/admin/providers", headers=AUTH, json={
            "id": "groq",
            "name": "Groq",
            "base_url": "https://api.groq.com/openai/v1",
            "api_keys": ["gsk-test-key"],
            "rpm_limit": 30,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["provider"]["id"] == "groq"
        assert body["provider"]["key_count"] == 1


@pytest.mark.asyncio
async def test_delete_provider():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        await c.post("/admin/providers", headers=AUTH, json={
            "id": "temp",
            "base_url": "https://temp.com/v1",
            "api_keys": ["k"],
        })
        r = await c.delete("/admin/providers/temp", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r2 = await c.get("/admin/providers", headers=AUTH)
        ids = [p["id"] for p in r2.json()["providers"]]
        assert "temp" not in ids


@pytest.mark.asyncio
async def test_delete_nonexistent_provider():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.delete("/admin/providers/nonexistent", headers=AUTH)
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_add_provider_missing_fields():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post(
            "/admin/providers", headers=AUTH, json={"id": "no-url"}
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_preferences_crud():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/preferences", headers=AUTH)
        assert r.json()["preferences"] == []

        r = await c.post("/preferences", headers=AUTH, json={
            "intent": "coding_agentic",
            "chain": ["groq/llama-3.3-70b"],
            "note": "test",
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["preference"]["chain"] == ["groq/llama-3.3-70b"]

        r = await c.get("/preferences", headers=AUTH)
        prefs = r.json()["preferences"]
        assert len(prefs) == 1
        assert prefs[0]["intent"] == "coding_agentic"

        r = await c.delete("/preferences/coding_agentic", headers=AUTH)
        assert r.json()["ok"] is True

        r = await c.get("/preferences", headers=AUTH)
        assert r.json()["preferences"] == []


@pytest.mark.asyncio
async def test_preferences_invalid_intent():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post("/preferences", headers=AUTH, json={
            "intent": "bad_intent", "chain": ["x"],
        })
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_preferences_empty_chain_clears():
    """Empty chain clears a preference (reverts to intelligent routing)."""
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        await c.post("/preferences", headers=AUTH, json={
            "intent": "coding_agentic", "chain": ["nim/foo"],
        })
        r = await c.post("/preferences", headers=AUTH, json={
            "intent": "coding_agentic", "chain": [],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body.get("cleared") is True
        listed = await c.get("/preferences", headers=AUTH)
        intents = [p["intent"] for p in listed.json()["preferences"]]
        assert "coding_agentic" not in intents


@pytest.mark.asyncio
async def test_clear_all_preferences():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        await c.post("/preferences", headers=AUTH, json={
            "intent": "coding_agentic", "chain": ["a"],
        })
        await c.post("/preferences", headers=AUTH, json={
            "intent": "chat_fast", "chain": ["b"],
        })
        r = await c.delete("/preferences", headers=AUTH)
        assert r.json()["ok"] is True
        r = await c.get("/preferences", headers=AUTH)
        assert r.json()["preferences"] == []


@pytest.mark.asyncio
async def test_stats_endpoint():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/stats", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert "version" in body
        assert "routing" in body


@pytest.mark.asyncio
async def test_ladder_endpoint():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/ladder", headers=AUTH)
        assert r.status_code in {200, 503}


@pytest.mark.asyncio
async def test_provider_partial_update():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        # Create initial provider
        r = await c.post("/admin/providers", headers=AUTH, json={
            "id": "groq",
            "name": "Groq",
            "base_url": "https://api.groq.com/openai/v1",
            "api_keys": ["gsk-test-key"],
            "rpm_limit": 30.0,
        })
        assert r.status_code == 200

        # Perform partial update (toggle status only)
        r = await c.post("/admin/providers", headers=AUTH, json={
            "id": "groq",
            "enabled": False,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["provider"]["enabled"] is False
        # Verify base_url and key_count were preserved
        assert body["provider"]["base_url"] == "https://api.groq.com/openai/v1"
        assert body["provider"]["key_count"] == 1


@pytest.mark.asyncio
async def test_seeded_zen_keys_auto_enable():
    """Free presets are seeded disabled; adding keys via dashboard must enable them."""
    import tempfile
    from pathlib import Path

    td = tempfile.TemporaryDirectory()
    _temp_dirs.append(td)
    settings = Settings(
        proxy_api_keys=[],
        allow_insecure_auth=True,
        nim_api_keys=["test-key-1"],
        nim_base_url="https://integrate.api.nvidia.com/v1",
        nim_rpm_limit=40,
        nim_rpd_limit=2000,
        nim_max_in_flight_per_key=3,
        providers_overlay_path=str(Path(td.name) / "providers.json"),
        catalog_snapshot_path=str(Path(td.name) / "catalog_snapshot.json"),
        sqlite_path=str(Path(td.name) / "nimmakai.db"),
        sqlite_seed_free_presets=True,
    )
    app = create_app(settings)
    app.state.settings = settings
    pool = KeyPool(
        api_keys=["test-key-1"],
        rpm_limit=settings.effective_rpm,
        rpd_limit=settings.nim_rpd_limit,
        max_in_flight_per_key=settings.nim_max_in_flight_per_key,
    )
    app.state.pool = pool
    store = ProviderStore.load(
        settings.providers_config_path,
        settings.providers_overlay_path,
        nim_base_url=settings.nim_base_url,
        nim_api_keys=list(settings.nim_api_keys),
        nim_rpm=settings.nim_rpm_limit,
        nim_rpd=settings.nim_rpd_limit,
        nim_max_in_flight=settings.nim_max_in_flight_per_key,
        sqlite_path=settings.sqlite_path,
        seed_free_presets=True,
    )
    assert "zen" in store.providers
    assert store.providers["zen"].enabled is False
    hub = ProviderHub(store, settings)
    app.state.hub = hub
    app.state.upstream = None
    app.state.registry = None
    app.state.selector = None
    app.state.fallback = None
    app.state.guard = AccountGuard(settings, pool)
    app.state.routing_stats = RoutingStats()
    app.state.preferences = UserPreferences()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        # Dashboard-style body: keys only, no explicit enabled (regression)
        r = await c.post(
            "/admin/providers",
            headers=AUTH,
            json={
                "id": "zen",
                "name": "OpenCode Zen",
                "base_url": "https://opencode.ai/zen/v1",
                "api_keys": ["oc-zen-test-key"],
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["provider"]["enabled"] is True
        assert body["provider"]["key_count"] == 1
        assert hub.has_runtime("zen") is True
