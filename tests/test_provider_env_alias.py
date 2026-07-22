"""Regression: OPENCODE_API_KEYS alias enables seeded zen provider."""

from __future__ import annotations

from pathlib import Path

from nimmakai.catalog.providers import ProviderStore


def test_opencode_api_keys_alias_enables_seeded_zen(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENCODE_ZEN_API_KEYS", raising=False)
    monkeypatch.setenv("OPENCODE_API_KEYS", "oc-alias-key")
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text("providers: []\n", encoding="utf-8")
    store = ProviderStore.load(
        yaml_path,
        tmp_path / "o.json",
        nim_api_keys=["k"],
        nim_base_url="https://n/v1",
        sqlite_path=tmp_path / "n.db",
        seed_free_presets=True,
    )
    assert "zen" in store.providers
    zen = store.providers["zen"]
    assert zen.enabled is True
    assert "oc-alias-key" in zen.resolved_keys()


def test_whitelist_survives_sqlite_roundtrip(tmp_path: Path) -> None:
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text(
        """
providers:
  - id: groq
    name: Groq
    base_url: https://api.groq.com/openai/v1
    api_keys: [gsk-1]
    model_whitelist: [llama]
    model_blacklist: [mixtral]
""",
        encoding="utf-8",
    )
    store = ProviderStore.load(
        yaml_path,
        tmp_path / "o.json",
        nim_api_keys=["k"],
        nim_base_url="https://n/v1",
        sqlite_path=tmp_path / "n.db",
        seed_free_presets=False,
    )
    assert store.providers["groq"].model_whitelist == ["llama"]
    assert store.providers["groq"].model_blacklist == ["mixtral"]

    store2 = ProviderStore.load(
        yaml_path,
        tmp_path / "o.json",
        nim_api_keys=["k"],
        nim_base_url="https://n/v1",
        sqlite_path=tmp_path / "n.db",
        seed_free_presets=False,
    )
    assert store2.providers["groq"].model_whitelist == ["llama"]
    assert store2.providers["groq"].model_blacklist == ["mixtral"]
