"""Admin model-pool enable/disable customization."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from nimmakai.balancer import KeyPool
from nimmakai.catalog.hub import ProviderHub
from nimmakai.catalog.preferences import UserPreferences
from nimmakai.catalog.providers import ProviderStore
from nimmakai.catalog.registry import ModelRegistry
from nimmakai.config import Settings
from nimmakai.main import create_app
from nimmakai.routing import Intent, IntentResult, ModelSelector, RoutingStats
from nimmakai.safety import AccountGuard

AUTH = {"Authorization": "Bearer any"}
_temp_dirs: list = []


def _app_with_live_models(model_ids: list[str]):
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
        sqlite_seed_free_presets=False,
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
        seed_free_presets=False,
    )
    hub = ProviderHub(store, settings)
    registry = ModelRegistry.from_settings(settings)
    registry.bind_db(store._db)
    registry._original_ids = {m.lower(): m for m in model_ids}
    registry.live_ids = {m.lower() for m in model_ids}
    registry.ladder.provider_ids = {m.split("/", 1)[0].lower() for m in model_ids}
    registry.recompute_rankings(persist=True)
    app.state.hub = hub
    app.state.registry = registry
    app.state.upstream = None
    app.state.selector = None
    app.state.fallback = None
    app.state.guard = AccountGuard(settings, pool)
    app.state.routing_stats = RoutingStats()
    app.state.preferences = UserPreferences()
    return app, registry, settings


def test_set_model_enabled_filters_active_pool():
    _app, registry, _settings = _app_with_live_models(
        ["zen/mimo-v2.5-free", "zen/big-pickle", "groq/llama-3"]
    )
    assert "zen/mimo-v2.5-free" in registry.active_live_ids()
    result = registry.set_model_enabled("zen/mimo-v2.5-free", False)
    assert result["enabled"] is False
    assert "zen/mimo-v2.5-free" not in registry.active_live_ids()
    assert "zen/mimo-v2.5-free" in registry.live_ids
    assert "zen/mimo-v2.5-free" in registry.disabled_models
    for mid in registry.chain_for_intent("coding_agentic"):
        assert mid != "zen/mimo-v2.5-free"
    registry.set_model_enabled("zen/mimo-v2.5-free", True)
    assert "zen/mimo-v2.5-free" in registry.active_live_ids()


def test_set_model_enabled_handles_mixed_case_live_ids():
    """SambaNova-style providers return ids with uppercase letters.

    The UI sends the exact id from /catalog, but registry.set_model_enabled
    normalizes the input to lowercase. It must still match the live id.
    """
    _app, registry, _settings = _app_with_live_models(
        ["sambanova/Meta-Llama-3.1-8B-Instruct"]
    )
    assert "sambanova/meta-llama-3.1-8b-instruct" in registry.live_ids
    # Frontend sends the exact mixed-case id shown in the model picker
    result = registry.set_model_enabled(
        "sambanova/Meta-Llama-3.1-8B-Instruct", False
    )
    assert result["enabled"] is False
    assert "sambanova/meta-llama-3.1-8b-instruct" not in registry.active_live_ids()
    assert "sambanova/meta-llama-3.1-8b-instruct" in registry.disabled_models
    # Original-case id preserved for upstream round-trip
    assert registry.original_id("sambanova/meta-llama-3.1-8b-instruct") == "sambanova/Meta-Llama-3.1-8B-Instruct"


def test_filter_available_never_fail_opens_disabled():
    _app, registry, _ = _app_with_live_models(
        ["zen/mimo-v2.5-free", "zen/big-pickle"]
    )
    registry.set_model_enabled("zen/mimo-v2.5-free", False)
    registry.set_model_enabled("zen/big-pickle", False)
    out = registry._filter_available(
        ["zen/mimo-v2.5-free", "zen/big-pickle", "missing/x"]
    )
    assert out == []
    registry.set_model_enabled("zen/big-pickle", True)
    out2 = registry._filter_available(["zen/mimo-v2.5-free", "zen/big-pickle"])
    assert out2 == ["zen/big-pickle"]


def test_disabled_not_known_for_routing():
    _app, registry, settings = _app_with_live_models(["zen/mimo-v2.5-free"])
    registry.set_model_enabled("zen/mimo-v2.5-free", False)
    assert registry.is_known("zen/mimo-v2.5-free") is False
    assert registry.resolve_live_id("zen/mimo-v2.5-free") is None
    assert (
        registry.resolve_live_id("zen/mimo-v2.5-free", include_disabled=True)
        == "zen/mimo-v2.5-free"
    )
    selector = ModelSelector(registry, settings)
    intent = IntentResult(intent=Intent.CODING_AGENTIC, confidence=1.0, rule_id="test")
    with pytest.raises(ValueError, match="model_disabled"):
        selector.resolve("zen/mimo-v2.5-free", intent)


def test_executor_chain_strips_disabled_passthrough():
    """Even a hand-built decision must not execute disabled models."""
    from nimmakai.routing.fallback import FallbackExecutor
    from nimmakai.routing.intents import Intent
    from nimmakai.routing.selector import RouteDecision

    _app, registry, settings = _app_with_live_models(
        ["zen/mimo-v2.5-free", "zen/big-pickle"]
    )
    registry.set_model_enabled("zen/mimo-v2.5-free", False)
    executor = FallbackExecutor(
        upstream=None,  # type: ignore[arg-type]
        registry=registry,
        settings=settings,
        hub=None,
    )
    decision = RouteDecision(
        chain=["zen/mimo-v2.5-free", "zen/big-pickle"],
        mode="passthrough_with_fallback",
        intent=Intent.CODING_AGENTIC,
        rule_id="test",
        requested_model="zen/mimo-v2.5-free",
    )
    chain = executor._chain(decision)
    assert "zen/mimo-v2.5-free" not in chain
    assert chain[0] == "zen/big-pickle"


def test_explicit_embedding_model_leads_chain():
    _app, registry, settings = _app_with_live_models(
        ["nim/embed-a", "nim/embed-b"]
    )
    # Seed embeddings intent chain with A first
    registry.dynamic_chains["embeddings"] = ["nim/embed-a", "nim/embed-b"]
    selector = ModelSelector(registry, settings)
    intent = IntentResult(intent=Intent.EMBEDDINGS, confidence=1.0, rule_id="test")
    decision = selector.resolve("nim/embed-b", intent)
    assert decision.chain[0] == "nim/embed-b"


def test_disable_rolls_back_memory_when_sqlite_write_fails(monkeypatch):
    _app, registry, _ = _app_with_live_models(
        ["zen/mimo-v2.5-free", "zen/big-pickle"]
    )

    def fail_write(_key, _value):
        raise OSError("disk full")

    monkeypatch.setattr(registry._db, "set_meta", fail_write)

    with pytest.raises(OSError, match="disk full"):
        registry.set_model_enabled("zen/mimo-v2.5-free", False)
    assert registry.disabled_models == set()
    assert registry.is_model_enabled("zen/mimo-v2.5-free") is True

    with pytest.raises(OSError, match="disk full"):
        registry.set_models_enabled(disable=["zen/mimo-v2.5-free", "zen/big-pickle"])
    assert registry.disabled_models == set()


def test_disabled_models_persist_across_bind():
    app, registry, settings = _app_with_live_models(
        ["zen/mimo-v2.5-free", "groq/llama-3"]
    )
    registry.set_model_enabled("groq/llama-3", False)
    # New registry + same sqlite
    reg2 = ModelRegistry.from_settings(settings)
    from nimmakai.catalog.db import get_db

    reg2.live_ids = set(registry.live_ids)
    reg2.bind_db(get_db(settings.sqlite_path))
    assert "groq/llama-3" in reg2.disabled_models
    assert "groq/llama-3" not in reg2.active_live_ids()
    assert app is not None


@pytest.mark.asyncio
async def test_admin_set_enabled_and_catalog_fields():
    app, registry, _ = _app_with_live_models(
        ["zen/mimo-v2.5-free", "zen/big-pickle"]
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        cat = await c.get("/catalog", headers=AUTH)
        assert cat.status_code == 200
        body = cat.json()
        assert "zen/mimo-v2.5-free" in body["live_ids"]
        assert body["disabled_models"] == []

        r = await c.post(
            "/admin/models/set-enabled",
            headers=AUTH,
            json={"model_id": "zen/mimo-v2.5-free", "enabled": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert r.json()["enabled"] is False

        cat2 = await c.get("/catalog", headers=AUTH)
        assert "zen/mimo-v2.5-free" in cat2.json()["disabled_models"]

        bulk = await c.post(
            "/admin/models/bulk-enabled",
            headers=AUTH,
            json={"provider_id": "zen", "enable_all": True},
        )
        assert bulk.status_code == 200
        assert "zen/mimo-v2.5-free" not in registry.disabled_models

        models = await c.get("/v1/models", headers=AUTH)
        assert models.status_code == 200
        ids = {m["id"] for m in models.json()["data"]}
        assert "zen/mimo-v2.5-free" in ids
