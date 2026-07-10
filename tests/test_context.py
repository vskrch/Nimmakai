"""Dynamic context window extraction."""

from __future__ import annotations

from nimmakai.catalog.context import (
    enrich_model_dict,
    extract_context_length,
    merge_context,
    parse_context_from_text,
)


def test_extract_top_level() -> None:
    assert extract_context_length({"id": "x", "context_length": 131072}) == 131072
    assert extract_context_length({"max_model_len": 32768}) == 32768


def test_extract_nested_meta() -> None:
    assert (
        extract_context_length({"id": "x", "meta": {"context_length": 262144}})
        == 262144
    )


def test_extract_unknown_omits() -> None:
    assert extract_context_length({"id": "org/model", "object": "model"}) is None
    assert extract_context_length(None) is None


def test_reject_absurd_values() -> None:
    assert extract_context_length({"context_length": 8}) is None
    assert extract_context_length({"context_length": 99_000_000}) is None


def test_parse_docs_text() -> None:
    assert parse_context_from_text("Supports 128K context for long agents") == 128_000
    assert parse_context_from_text("context length: 131072 tokens") == 131072
    assert parse_context_from_text("up to 1M tokens") == 1_000_000
    assert parse_context_from_text("a nice coding model") is None


def test_merge_prefers_larger() -> None:
    assert merge_context(32_768, 131_072) == 131_072
    assert merge_context(131_072, 32_768) == 131_072
    assert merge_context(None, 8192) == 8192


def test_enrich_never_shrinks_upstream() -> None:
    item = {"id": "m", "context_length": 200_000}
    out = enrich_model_dict(item, 100_000)
    assert out["context_length"] == 200_000
    out2 = enrich_model_dict({"id": "m"}, 131_072)
    assert out2["context_length"] == 131_072
    assert out2["max_model_len"] == 131_072
