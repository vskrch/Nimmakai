"""Admin extensibility endpoints — toggles + custom catalog (NMK-EXT-501/502)."""

from __future__ import annotations

import os
import tempfile

from nimmakai.catalog.db import NimmakaiDB


def test_extensibility_features_defaults():
    """Fresh DB returns default feature toggles."""
    d = tempfile.mkdtemp()
    db = NimmakaiDB(os.path.join(d, "ext.db"))
    feats = db.get_extensibility_features()
    assert "prompt_understanding_enabled" in feats
    assert "custom_catalog_enabled" in feats
    assert feats["prompt_understanding_enabled"] is False
    assert feats["custom_catalog_enabled"] is False


def test_extensibility_features_roundtrip():
    """Set and get extensibility features."""
    d = tempfile.mkdtemp()
    db = NimmakaiDB(os.path.join(d, "ext.db"))
    db.set_extensibility_features({
        "prompt_understanding_enabled": True,
        "prompt_understanding_model": "zen/mimo-v2.5-free",
        "custom_catalog_enabled": True,
        "ollama_enabled": True,
        "opencode_go_enabled": False,
    })
    feats = db.get_extensibility_features()
    assert feats["prompt_understanding_enabled"] is True
    assert feats["prompt_understanding_model"] == "zen/mimo-v2.5-free"
    assert feats["custom_catalog_enabled"] is True
    assert feats["ollama_enabled"] is True
    assert feats["opencode_go_enabled"] is False


def test_custom_catalog_mappings_defaults():
    """Fresh DB returns empty mappings."""
    d = tempfile.mkdtemp()
    db = NimmakaiDB(os.path.join(d, "ext.db"))
    mappings = db.get_custom_catalog_mappings()
    assert mappings == {}


def test_custom_catalog_mappings_roundtrip():
    """Set and get custom catalog mappings."""
    d = tempfile.mkdtemp()
    db = NimmakaiDB(os.path.join(d, "ext.db"))
    db.set_custom_catalog_mappings({
        "coding_agentic": "zen/mimo-v2.5-free",
        "chat_fast": "nim/nemotron-3-ultra-550b",
        "reasoning": "deepseek/deepseek-r1",
    })
    mappings = db.get_custom_catalog_mappings()
    assert mappings["coding_agentic"] == "zen/mimo-v2.5-free"
    assert mappings["chat_fast"] == "nim/nemotron-3-ultra-550b"
    assert mappings["reasoning"] == "deepseek/deepseek-r1"


def test_custom_catalog_mappings_empty_values_filtered():
    """Empty values are not stored."""
    d = tempfile.mkdtemp()
    db = NimmakaiDB(os.path.join(d, "ext.db"))
    db.set_custom_catalog_mappings({"coding_agentic": "model-a", "chat_fast": ""})
    mappings = db.get_custom_catalog_mappings()
    assert "coding_agentic" in mappings
    assert "chat_fast" not in mappings