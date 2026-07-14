"""SQLite durable store for providers + preferences."""

from __future__ import annotations

from pathlib import Path

from nimmakai.catalog.db import NimmakaiDB, get_db
from nimmakai.catalog.preferences import UserPreferences
from nimmakai.catalog.providers import ProviderConfig, ProviderStore


def test_sqlite_provider_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "n.db"
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text("providers: []\n", encoding="utf-8")
    overlay = tmp_path / "overlay.json"

    store = ProviderStore.load(
        yaml_path,
        overlay,
        nim_api_keys=["nvapi-1"],
        nim_base_url="https://nim.test/v1",
        sqlite_path=db_path,
        seed_free_presets=False,
    )
    store.upsert(
        ProviderConfig(
            id="groq",
            name="Groq",
            base_url="https://api.groq.com/openai/v1",
            api_keys=["gsk-a", "gsk-b"],
            enabled=True,
        )
    )
    assert db_path.is_file()

    store2 = ProviderStore.load(
        yaml_path,
        overlay,
        nim_api_keys=["nvapi-1"],
        nim_base_url="https://nim.test/v1",
        sqlite_path=db_path,
        seed_free_presets=False,
    )
    assert "groq" in store2.providers
    assert store2.providers["groq"].resolved_keys() == ["gsk-a", "gsk-b"]
    assert store2.providers["groq"].base_url.endswith("/v1")


def test_seed_free_presets(tmp_path: Path) -> None:
    db_path = tmp_path / "seed.db"
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text("providers: []\n", encoding="utf-8")
    store = ProviderStore.load(
        yaml_path,
        tmp_path / "o.json",
        nim_api_keys=["k"],
        nim_base_url="https://n/v1",
        sqlite_path=db_path,
        seed_free_presets=True,
    )
    assert "groq" in store.providers
    assert store.providers["groq"].enabled is False  # no keys yet
    assert "api.groq.com" in store.providers["groq"].base_url
    # Second load should not re-seed / not wipe keys
    store.upsert(
        ProviderConfig(
            id="groq",
            name="Groq",
            base_url=store.providers["groq"].base_url,
            api_keys=["gsk-live"],
            api_keys_env="GROQ_API_KEYS",
            enabled=True,
        )
    )
    store3 = ProviderStore.load(
        yaml_path,
        tmp_path / "o.json",
        nim_api_keys=["k"],
        nim_base_url="https://n/v1",
        sqlite_path=db_path,
        seed_free_presets=True,
    )
    assert store3.providers["groq"].resolved_keys() == ["gsk-live"]
    assert store3.providers["groq"].enabled is True


def test_migrate_json_overlay(tmp_path: Path) -> None:
    db_path = tmp_path / "mig.db"
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text("providers: []\n", encoding="utf-8")
    overlay = tmp_path / "providers.json"
    overlay.write_text(
        """
        {"providers": [{
            "id": "cerebras",
            "name": "Cerebras",
            "base_url": "https://api.cerebras.ai/v1",
            "api_keys": ["csk-old"],
            "enabled": true,
            "rpm_limit": 30,
            "rpd_limit": 1000,
            "max_in_flight_per_key": 2,
            "api_style": "openai",
            "builtin": false
        }]}
        """,
        encoding="utf-8",
    )
    store = ProviderStore.load(
        yaml_path,
        overlay,
        nim_api_keys=["k"],
        nim_base_url="https://n/v1",
        sqlite_path=db_path,
        seed_free_presets=False,
    )
    assert store.providers["cerebras"].resolved_keys() == ["csk-old"]
    # Reload without needing the JSON content (sqlite is source of truth)
    overlay.unlink()
    store2 = ProviderStore.load(
        yaml_path,
        overlay,
        nim_api_keys=["k"],
        nim_base_url="https://n/v1",
        sqlite_path=db_path,
        seed_free_presets=False,
    )
    assert store2.providers["cerebras"].resolved_keys() == ["csk-old"]


def test_preferences_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "prefs.db"
    json_path = tmp_path / "prefs.json"
    prefs = UserPreferences(path=json_path, db_path=db_path)
    prefs.load()
    prefs.set("coding_agentic", ["groq/llama-3.3-70b"], note="test")
    prefs2 = UserPreferences(path=json_path, db_path=db_path)
    prefs2.load()
    assert prefs2.has_preference("coding_agentic")
    assert prefs2.get("coding_agentic").chain == ["groq/llama-3.3-70b"]


def test_db_singleton_same_path(tmp_path: Path) -> None:
    p = tmp_path / "one.db"
    a = get_db(p)
    b = get_db(p)
    assert a is b
