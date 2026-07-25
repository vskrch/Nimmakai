"""IntelFetcher — multi-source model intelligence fetcher."""

from __future__ import annotations

from nimmakai.catalog.intel_fetcher import (
    IntelBundle,
    _merge_bundles,
    _normalize_slug,
    _safe_float,
    _safe_int,
)


def test_normalize_slug():
    """Strip provider prefix + date suffix."""
    assert _normalize_slug("openai/gpt-4o-2024-11-20") == "gpt-4o"
    assert _normalize_slug("meta-llama/llama-3.1-70b") == "llama-3.1-70b"
    assert _normalize_slug("gpt-4o") == "gpt-4o"
    assert _normalize_slug("") == ""
    assert _normalize_slug("mimo-v2.5-free") == "mimo-v2.5-free"


def test_safe_float():
    assert _safe_float(82.0) == 82.0
    assert _safe_float("82") == 82.0
    assert _safe_float(None) is None
    assert _safe_float("not-a-number") is None
    assert _safe_float(float("nan")) is None
    assert _safe_float(float("inf")) is None


def test_safe_int():
    assert _safe_int(42) == 42
    assert _safe_int("42") == 42
    assert _safe_int(None) is None
    assert _safe_int("not-a-number") is None


def test_intel_bundle_merge():
    """Merge fills gaps from other; never overwrites non-None values."""
    a = IntelBundle(model_slug="model-x", aa_intelligence_idx=82.0, supports_tools=True)
    b = IntelBundle(model_slug="model-x", aa_tps=120.0, supports_vision=True)
    a.merge_from(b)
    assert a.aa_intelligence_idx == 82.0  # kept
    assert a.aa_tps == 120.0  # filled from b
    assert a.supports_tools is True  # kept
    assert a.supports_vision is True  # filled from b
    assert "openrouter" not in a.sources  # neither had sources


def test_intel_bundle_merge_sources():
    """Merge combines source lists."""
    a = IntelBundle(model_slug="x", sources=["openrouter"])
    b = IntelBundle(model_slug="x", sources=["arena"])
    a.merge_from(b)
    assert "openrouter" in a.sources
    assert "arena" in a.sources


def test_merge_bundles_priority():
    """First source with a value for a field wins (highest priority first)."""
    s1 = {
        "model-a": IntelBundle(
            model_slug="model-a", aa_intelligence_idx=90.0, sources=["aa"]
        )
    }
    s2 = {
        "model-a": IntelBundle(
            model_slug="model-a", aa_intelligence_idx=50.0, aa_tps=100.0, sources=["hf"]
        )
    }
    merged = _merge_bundles([s1, s2])
    bundle = merged["model-a"]
    assert bundle.aa_intelligence_idx == 90.0  # s1 wins (higher priority)
    assert bundle.aa_tps == 100.0  # filled from s2


def test_merge_bundles_new_slug():
    """Slugs only in later sources get added."""
    s1 = {"model-a": IntelBundle(model_slug="model-a", aa_intelligence_idx=90.0)}
    s2 = {"model-b": IntelBundle(model_slug="model-b", arena_elo=1100.0)}
    merged = _merge_bundles([s1, s2])
    assert "model-a" in merged
    assert "model-b" in merged
    assert merged["model-b"].arena_elo == 1100.0


def test_intel_fetcher_disk_cache_roundtrip(tmp_path):
    """IntelFetcher disk cache save/load roundtrip."""
    from nimmakai.catalog.intel_fetcher import IntelFetcher

    cache_path = tmp_path / "intel_cache.json"
    fetcher = IntelFetcher(cache_path=cache_path, ttl_hours=6.0)

    bundles = {
        "gpt-4o": IntelBundle(
            model_slug="gpt-4o",
            aa_intelligence_idx=93.0,
            supports_tools=True,
            context_length=128000,
            sources=["openrouter", "aa"],
        )
    }
    fetcher._save_disk_cache(bundles)

    loaded = fetcher._load_disk_cache()
    assert loaded is not None
    assert "gpt-4o" in loaded
    b = loaded["gpt-4o"]
    assert b.aa_intelligence_idx == 93.0
    assert b.supports_tools is True
    assert b.context_length == 128000


def test_intel_fetcher_disk_cache_missing(tmp_path):
    """Load returns None when cache file doesn't exist."""
    from nimmakai.catalog.intel_fetcher import IntelFetcher

    fetcher = IntelFetcher(cache_path=tmp_path / "nonexistent.json")
    assert fetcher._load_disk_cache() is None


def test_intel_fetcher_mem_cache_ttl(tmp_path):
    """Memory cache serves within TTL without re-fetching."""
    from nimmakai.catalog.intel_fetcher import IntelFetcher

    fetcher = IntelFetcher(cache_path=tmp_path / "intel.json", ttl_hours=999.0)
    # Manually set mem cache
    fetcher._mem_cache = {"x": IntelBundle(model_slug="x", aa_intelligence_idx=50.0)}
    fetcher._mem_cache_at = __import__("time").time()
    # fetch_all should return mem cache without hitting network
    import asyncio
    result = asyncio.run(fetcher.fetch_all())
    assert "x" in result
    assert result["x"].aa_intelligence_idx == 50.0