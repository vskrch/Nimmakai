"""Provider namespacing and store."""

from __future__ import annotations

from pathlib import Path

from potato.catalog.providers import (
    ProviderConfig,
    ProviderStore,
    namespace_model,
    scoring_model_id,
    split_provider_model,
)


def test_namespace_and_split() -> None:
    ids = {"nim", "groq"}
    assert namespace_model("groq", "llama-3.3-70b") == "groq/llama-3.3-70b"
    assert split_provider_model("groq/llama-3.3-70b", ids) == ("groq", "llama-3.3-70b")
    assert split_provider_model("qwen/qwen3.5", ids) == ("nim", "qwen/qwen3.5")
    assert scoring_model_id("nim/qwen/qwen3.5", ids) == "qwen/qwen3.5"


def test_provider_store_nim_from_env(tmp_path: Path) -> None:
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text(
        "providers:\n  - id: nim\n    base_url: https://example.com/v1\n    enabled: true\n",
        encoding="utf-8",
    )
    overlay = tmp_path / "overlay.json"
    store = ProviderStore.load(
        yaml_path,
        overlay,
        nim_base_url="https://integrate.api.nvidia.com/v1",
        nim_api_keys=["nvapi-test"],
        sqlite_path=tmp_path / "t.db",
        seed_free_presets=False,
    )
    assert "nim" in store.providers
    assert store.providers["nim"].resolved_keys() == ["nvapi-test"]
    assert store.providers["nim"].builtin is True


def test_upsert_overlay(tmp_path: Path) -> None:
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text("providers: []\n", encoding="utf-8")
    overlay = tmp_path / "overlay.json"
    db = tmp_path / "t.db"
    store = ProviderStore.load(
        yaml_path,
        overlay,
        nim_api_keys=["k1"],
        nim_base_url="https://n/v1",
        sqlite_path=db,
        seed_free_presets=False,
    )
    store.upsert(
        ProviderConfig(
            id="groq",
            name="Groq",
            base_url="https://api.groq.com/openai/v1",
            api_keys=["gsk-test"],
            enabled=True,
        )
    )
    assert overlay.is_file()
    assert db.is_file()
    store2 = ProviderStore.load(
        yaml_path,
        overlay,
        nim_api_keys=["k1"],
        nim_base_url="https://n/v1",
        sqlite_path=db,
        seed_free_presets=False,
    )
    assert "groq" in store2.providers
    assert store2.providers["groq"].resolved_keys() == ["gsk-test"]


# ── NIM provider parity + placeholder filtering ──────────────────────


def test_nim_has_api_keys_env_like_every_other_provider(tmp_path: Path) -> None:
    """NIM must wire api_keys_env so resolved_keys() reads the live env."""
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text("providers: []\n", encoding="utf-8")
    store = ProviderStore.load(
        yaml_path,
        tmp_path / "o.json",
        nim_api_keys=["nvapi-real"],
        nim_base_url="https://n/v1",
        sqlite_path=tmp_path / "t.db",
        seed_free_presets=False,
    )
    nim = store.providers["nim"]
    assert nim.api_keys_env == "NIM_API_KEYS"
    assert nim.builtin is True


def test_nim_env_keys_merge_with_admin_ui_keys(tmp_path: Path) -> None:
    """Env NIM_API_KEYS MERGES with admin-UI-saved key, not overwrites it."""
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text("providers: []\n", encoding="utf-8")
    db = tmp_path / "t.db"
    store = ProviderStore.load(
        yaml_path,
        tmp_path / "o.json",
        nim_api_keys=[],
        nim_base_url="https://n/v1",
        sqlite_path=db,
        seed_free_presets=False,
    )
    store.upsert(
        ProviderConfig(
            id="nim",
            name="NVIDIA NIM",
            base_url="https://n/v1",
            api_keys=["nvapi-from-ui"],
            api_keys_env="NIM_API_KEYS",
            enabled=True,
            builtin=True,
        )
    )
    store2 = ProviderStore.load(
        yaml_path,
        tmp_path / "o.json",
        nim_api_keys=["nvapi-from-env"],
        nim_base_url="https://n/v1",
        sqlite_path=db,
        seed_free_presets=False,
    )
    keys = store2.providers["nim"].resolved_keys()
    assert "nvapi-from-ui" in keys
    assert "nvapi-from-env" in keys


def test_nim_env_empty_preserves_admin_ui_key(tmp_path: Path) -> None:
    """When env NIM_API_KEYS is empty, the admin-UI-saved key is kept."""
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text("providers: []\n", encoding="utf-8")
    db = tmp_path / "t.db"
    store = ProviderStore.load(
        yaml_path,
        tmp_path / "o.json",
        nim_api_keys=[],
        nim_base_url="https://n/v1",
        sqlite_path=db,
        seed_free_presets=False,
    )
    store.upsert(
        ProviderConfig(
            id="nim",
            name="NVIDIA NIM",
            base_url="https://n/v1",
            api_keys=["nvapi-saved-via-ui"],
            api_keys_env="NIM_API_KEYS",
            enabled=True,
            builtin=True,
        )
    )
    store2 = ProviderStore.load(
        yaml_path,
        tmp_path / "o.json",
        nim_api_keys=[],
        nim_base_url="https://n/v1",
        sqlite_path=db,
        seed_free_presets=False,
    )
    keys = store2.providers["nim"].resolved_keys()
    assert "nvapi-saved-via-ui" in keys


def test_placeholder_keys_filtered_from_resolved() -> None:
    """dummy-key / nvapi-key-1 etc. are filtered so they never burn auth budget."""
    cfg = ProviderConfig(
        id="nim",
        name="NIM",
        base_url="https://n/v1",
        api_keys=["dummy-key", "nvapi-key-1", "nvapi-real-abc"],
        api_keys_env="NIM_API_KEYS",
        enabled=True,
    )
    keys = cfg.resolved_keys()
    assert "dummy-key" not in keys
    assert "nvapi-key-1" not in keys
    assert "nvapi-real-abc" in keys


def test_placeholder_filter_case_insensitive() -> None:
    cfg = ProviderConfig(
        id="nim",
        name="NIM",
        base_url="https://n/v1",
        api_keys=["DUMMY-KEY", "PLACEHOLDER", "real-key-xyz"],
    )
    keys = cfg.resolved_keys()
    assert "DUMMY-KEY" not in keys
    assert "PLACEHOLDER" not in keys
    assert "real-key-xyz" in keys
