"""Free provider presets and env bootstrap."""

from __future__ import annotations

from pathlib import Path

from nimmakai.catalog.presets import (
    get_preset,
    list_presets,
    speed_prior_for_provider,
)
from nimmakai.catalog.providers import ProviderStore


def test_list_presets_includes_free_openai_compatible() -> None:
    presets = list_presets()
    ids = {p["id"] for p in presets}
    assert "groq" in ids
    assert "cerebras" in ids
    assert "openrouter" in ids
    assert "deepseek" in ids
    assert "custom" in ids
    groq = get_preset("groq")
    assert groq is not None
    assert groq["base_url"].endswith("/v1") or "/openai/v1" in groq["base_url"]
    assert groq["free_tier"] is True
    deepseek = get_preset("deepseek")
    assert deepseek is not None
    assert deepseek["base_url"].startswith("https://api.deepseek.com")
    assert deepseek["free_tier"] is True


def test_speed_priors_favor_ultra_fast_free_backends() -> None:
    assert speed_prior_for_provider("cerebras") > speed_prior_for_provider("nim")
    assert speed_prior_for_provider("groq") > 1.0
    assert speed_prior_for_provider("deepseek") > 1.0
    assert speed_prior_for_provider("unknown-xyz") == 1.0


def test_env_bootstrap_registers_groq(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEYS", "gsk-test-key-1,gsk-test-key-2")
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text("providers: []\n", encoding="utf-8")
    overlay = tmp_path / "overlay.json"
    store = ProviderStore.load(
        yaml_path,
        overlay,
        nim_api_keys=["nvapi-x"],
        nim_base_url="https://integrate.api.nvidia.com/v1",
        sqlite_path=tmp_path / "p.db",
        seed_free_presets=False,
    )
    assert "groq" in store.providers
    assert store.providers["groq"].base_url.startswith("https://api.groq.com")
    assert store.providers["groq"].resolved_keys() == [
        "gsk-test-key-1",
        "gsk-test-key-2",
    ]
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
