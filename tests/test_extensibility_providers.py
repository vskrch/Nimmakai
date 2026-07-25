"""New provider presets — Ollama Cloud + OpenCode Go (NMK-EXT-101/102)."""

from __future__ import annotations

from nimmakai.catalog.presets import (
    ENV_PROVIDER_BOOTSTRAP,
    PROVIDER_SPEED_PRIOR_COLDSTART,
    get_preset,
    list_presets,
    speed_prior_for_provider,
)


def test_ollama_cloud_preset():
    p = get_preset("ollama")
    assert p is not None
    assert p["base_url"] == "https://api.ollama.com/v1"
    assert p["api_keys_env"] == "OLLAMA_CLOUD_API_KEYS"
    assert p["free_tier"] is True
    assert "openai-compatible" in p["tags"]


def test_opencode_go_preset():
    p = get_preset("opencode_go")
    assert p is not None
    assert p["base_url"] == "https://opencode.ai/zen/go/v1"
    assert p["api_keys_env"] == "OPENCODE_GO_API_KEYS"
    assert p["free_tier"] is True
    assert "https://opencode.ai/zen/go/v1" in p["description"]
    assert "openai-compatible" in p["tags"]


def test_new_providers_in_list():
    ids = {p["id"] for p in list_presets()}
    assert "ollama" in ids
    assert "opencode_go" in ids


def test_new_providers_speed_priors():
    assert speed_prior_for_provider("ollama") == 1.15
    assert speed_prior_for_provider("opencode_go") == 1.28
    # Unknown provider still returns 1.0
    assert speed_prior_for_provider("unknown") == 1.0


def test_new_providers_in_env_bootstrap():
    env_map = dict(ENV_PROVIDER_BOOTSTRAP)
    assert env_map.get("OLLAMA_CLOUD_API_KEYS") == "ollama"
    assert env_map.get("OPENCODE_GO_API_KEYS") == "opencode_go"


def test_new_providers_in_coldstart_priors():
    assert "ollama" in PROVIDER_SPEED_PRIOR_COLDSTART
    assert "opencode_go" in PROVIDER_SPEED_PRIOR_COLDSTART