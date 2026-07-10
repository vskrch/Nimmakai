"""Catalog YAML loading and alias/chain resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from nimmakai.catalog import ModelRegistry
from nimmakai.catalog.health import ModelHealthStore
from nimmakai.catalog.schema import parse_alias_value

ROOT = Path(__file__).resolve().parents[1]
YAML = ROOT / "config" / "models.yaml"


def test_parse_alias_chain() -> None:
    t = parse_alias_value("chain:coding_agentic")
    assert t.kind == "chain"
    assert t.value == "coding_agentic"


def test_parse_alias_model() -> None:
    t = parse_alias_value("org/model-name")
    assert t.kind == "model"
    assert t.value == "org/model-name"


def test_load_yaml_aliases_and_chains() -> None:
    reg = ModelRegistry.from_yaml(YAML)
    assert reg.is_alias("gpt-4o")
    target = reg.resolve_alias("gpt-4o")
    assert target.kind == "chain"
    assert target.value == "coding_agentic"
    chain = reg.chain_for_intent("coding_agentic")
    assert len(chain) >= 1
    assert "minimaxai/minimax-m2.7" in chain


def test_auto_tokens() -> None:
    reg = ModelRegistry.from_yaml(YAML)
    assert reg.is_auto("auto")
    assert reg.is_auto("nimmakai/auto")
    assert reg.is_auto("")


def test_health_reorder_bubbles_unhealthy() -> None:
    store = ModelHealthStore(min_samples=2, error_rate_threshold=0.4)
    chain = ["a", "b", "c"]
    store.record_outcome("a", success=False)
    store.record_outcome("a", success=False)
    store.record_outcome("a", success=False)
    store.record_outcome("b", success=True, latency=0.1)
    store.record_outcome("b", success=True, latency=0.1)
    reordered = store.health_reorder(chain)
    assert reordered[0] == "b"
    assert "a" in reordered


@pytest.mark.asyncio
async def test_refresh_intersects_live_ids() -> None:
    reg = ModelRegistry.from_yaml(YAML)

    class FakeUpstream:
        async def request_json(self, *args, **kwargs):
            return (
                200,
                {
                    "data": [
                        {"id": "minimaxai/minimax-m2.7"},
                        {"id": "google/gemma-4-31b-it"},
                    ]
                },
                {},
                None,
            )

    ok = await reg.refresh_from_upstream(FakeUpstream())  # type: ignore[arg-type]
    assert ok
    assert "minimaxai/minimax-m2.7" in reg.live_ids
    coding = reg.chain_for_intent("coding_agentic")
    assert coding == ["minimaxai/minimax-m2.7"]
    chat = reg.chain_for_intent("chat_fast")
    assert chat == ["google/gemma-4-31b-it"]
