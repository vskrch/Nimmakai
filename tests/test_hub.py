"""ProviderHub tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from nimmakai.catalog.hub import ProviderHub
from nimmakai.catalog.providers import ProviderConfig, ProviderStore
from nimmakai.config import Settings

NIM_YAML = (
    "providers:\n"
    "  - id: nim\n"
    "    base_url: https://integrate.api.nvidia.com/v1\n"
    "    enabled: true\n"
    "    builtin: true\n"
)


@pytest.fixture
def settings():
    return Settings(
        nim_api_keys=["test-key"],
        proxy_api_keys=["test-proxy"],
        allow_insecure_auth=False,
        nim_base_url="https://integrate.api.nvidia.com/v1",
        nim_rpm_limit=40,
        nim_rpd_limit=2000,
        nim_max_in_flight_per_key=3,
    )


@pytest.fixture
def store(tmp_path: Path):
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text(NIM_YAML, encoding="utf-8")
    overlay = tmp_path / "overlay.json"
    return ProviderStore.load(
        yaml_path,
        overlay,
        nim_base_url="https://integrate.api.nvidia.com/v1",
        nim_api_keys=["test-key"],
        sqlite_path=tmp_path / "hub.db",
        seed_free_presets=False,
    )


@pytest.mark.asyncio
async def test_hub_start_creates_nim_runtime(store, settings):
    hub = ProviderHub(store, settings)
    await hub.start()
    assert "nim" in hub.runtimes
    assert hub.runtimes["nim"].config.enabled
    await hub.stop()


@pytest.mark.asyncio
async def test_hub_upsert_provider(store, settings):
    hub = ProviderHub(store, settings)
    await hub.start()
    cfg = ProviderConfig(
        id="groq",
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        api_keys=["gsk-test"],
        enabled=True,
    )
    masked = await hub.upsert_provider(cfg)
    assert masked["id"] == "groq"
    assert masked["key_count"] == 1
    assert "groq" in hub.runtimes
    await hub.stop()


@pytest.mark.asyncio
async def test_hub_remove_provider(store, settings):
    hub = ProviderHub(store, settings)
    await hub.start()
    cfg = ProviderConfig(
        id="groq",
        base_url="https://api.groq.com/v1",
        api_keys=["k"],
    )
    await hub.upsert_provider(cfg)
    assert "groq" in hub.runtimes
    ok = await hub.remove_provider("groq")
    assert ok
    assert "groq" not in hub.runtimes
    await hub.stop()


@pytest.mark.asyncio
async def test_hub_remove_builtin_disables(store, settings):
    hub = ProviderHub(store, settings)
    await hub.start()
    assert "nim" in hub.runtimes
    ok = await hub.remove_provider("nim")
    assert ok
    # Builtin disable: runtime is removed from hub.runtimes
    assert "nim" not in hub.runtimes
    # But store config is disabled (not deleted)
    assert store.providers["nim"].enabled is False
    await hub.stop()


@pytest.mark.asyncio
async def test_hub_client_for_model(store, settings):
    hub = ProviderHub(store, settings)
    await hub.start()
    client, pid, mid = hub.client_for_model("nim/some-model")
    assert pid == "nim"
    assert mid == "some-model"
    client, pid, mid = hub.client_for_model("nvidia/some-model")
    assert pid == "nim"
    assert mid == "nvidia/some-model"
    await hub.stop()


def test_hub_namespace(store, settings):
    hub = ProviderHub(store, settings)
    assert hub.namespace("groq", "llama-3.3-70b") == "groq/llama-3.3-70b"
