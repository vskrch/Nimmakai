"""Provider namespacing and store."""

from __future__ import annotations

from pathlib import Path

from nimmakai.catalog.providers import (
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
    )
    assert "nim" in store.providers
    assert store.providers["nim"].resolved_keys() == ["nvapi-test"]
    assert store.providers["nim"].builtin is True


def test_upsert_overlay(tmp_path: Path) -> None:
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text("providers: []\n", encoding="utf-8")
    overlay = tmp_path / "overlay.json"
    store = ProviderStore.load(
        yaml_path, overlay, nim_api_keys=["k1"], nim_base_url="https://n/v1"
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
    store2 = ProviderStore.load(
        yaml_path, overlay, nim_api_keys=["k1"], nim_base_url="https://n/v1"
    )
    assert "groq" in store2.providers
    assert store2.providers["groq"].resolved_keys() == ["gsk-test"]
