from __future__ import annotations

from pathlib import Path
import pytest
from potato.catalog.db import PotatoDB
from potato.catalog.model_pools import ModelPoolStore


def test_model_pool_store_gating_logic(tmp_path: Path):
    db_path = tmp_path / "test_pools.db"
    db = PotatoDB(db_path)
    store = ModelPoolStore(db)
    store.load()

    # 1. Unrestricted model defaults to True
    assert store.is_allowed("qwen/qwen3.5-122b-a10b", "chat_fast", is_auto_router=True) is True

    # 2. Restrict expensive model: allow only coding_agentic & reasoning, block auto router
    store.set_config(
        model_id="deepseek/deepseek-r1",
        allowed_intents=["coding_agentic", "reasoning"],
        excluded_intents=[],
        allow_auto_router=False,
        note="Expensive frontier model reserved for explicit coding/best endpoints",
    )

    # Auto router request -> blocked
    assert store.is_allowed("deepseek/deepseek-r1", "coding_agentic", is_auto_router=True) is False

    # Explicit potato/coding endpoint request -> allowed
    assert store.is_allowed("deepseek/deepseek-r1", "coding_agentic", is_auto_router=False) is True

    # Explicit chat_fast request -> blocked by allowed_intents white list
    assert store.is_allowed("deepseek/deepseek-r1", "chat_fast", is_auto_router=False) is False

    # Persistence check
    store2 = ModelPoolStore(db)
    store2.load()
    cfg = store2.get("deepseek/deepseek-r1")
    assert cfg is not None
    assert cfg.allow_auto_router is False
    assert "coding_agentic" in cfg.allowed_intents

    # Delete config
    store2.delete_config("deepseek/deepseek-r1")
    assert store2.get("deepseek/deepseek-r1") is None
    assert store2.is_allowed("deepseek/deepseek-r1", "chat_fast", is_auto_router=True) is True
