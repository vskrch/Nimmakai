"""Tests for dynamic model cost lookup from models.dev API."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from nimmakai.analytics.cost import estimate_cost, list_default_rates, lookup_rates
from nimmakai.analytics.models_cost import ModelsDevCostCache

# ── fixtures ────────────────────────────────────────────────────────

SAMPLE_API_RESPONSE = {
    "openai": {
        "id": "openai",
        "models": {
            "gpt-4o": {
                "id": "gpt-4o",
                "cost": {"input": 2.5, "output": 10.0},
            },
            "gpt-4o-mini": {
                "id": "gpt-4o-mini",
                "cost": {"input": 0.15, "output": 0.6},
            },
            "o3": {"id": "o3", "cost": {"input": 2.0, "output": 8.0}},
        },
    },
    "anthropic": {
        "id": "anthropic",
        "models": {
            "claude-sonnet-4": {
                "id": "claude-sonnet-4",
                "cost": {"input": 3.0, "output": 15.0},
            },
            "claude-opus-4": {
                "id": "claude-opus-4",
                "cost": {"input": 15.0, "output": 75.0},
            },
        },
    },
    "deepseek": {
        "id": "deepseek",
        "models": {
            "deepseek-chat": {
                "id": "deepseek-chat",
                "cost": {"input": 0.14, "output": 0.28},
            },
            "deepseek-r1": {
                "id": "deepseek-r1",
                "cost": {"input": 0.55, "output": 2.19},
            },
        },
    },
    "nvidia": {
        "id": "nvidia",
        "models": {
            "z-ai/glm-5.2": {
                "id": "z-ai/glm-5.2",
                "cost": {"input": 0, "output": 0},
            },
            "microsoft/phi-4-mini": {
                "id": "microsoft/phi-4-mini",
                "cost": {"input": 0, "output": 0},
            },
        },
    },
    "free-provider": {
        "id": "free-provider",
        "models": {
            "some-model": {"id": "some-model"},  # no cost key
            "other-model": {
                "id": "other-model",
                "cost": "invalid",
            },
        },
    },
}


def _make_cache(data: dict = SAMPLE_API_RESPONSE) -> ModelsDevCostCache:
    """Create a cache pre-loaded with mock data."""
    cache = ModelsDevCostCache(url="http://localhost:99999/no-such-url", ttl_seconds=3600)
    mock_resp = MagicMock()
    mock_resp.json.return_value = data
    mock_resp.raise_for_status.return_value = None
    with patch("nimmakai.analytics.models_cost.httpx.get", return_value=mock_resp):
        cache._ensure_loaded()
    return cache


# ── ModelsDevCostCache tests ────────────────────────────────────────


class TestModelsDevCostCache:
    def test_fetch_parses_costs(self):
        cache = _make_cache()
        assert cache.is_loaded
        assert len(cache.all_rates()) > 0

    def test_lookup_exact_namespaced_id(self):
        cache = _make_cache()
        assert cache.lookup("openai/gpt-4o") == (2.5, 10.0)
        assert cache.lookup("anthropic/claude-sonnet-4") == (3.0, 15.0)
        assert cache.lookup("deepseek/deepseek-chat") == (0.14, 0.28)

    def test_lookup_nvidia_nested_model(self):
        cache = _make_cache()
        # Canonical provider/model (model_id already namespaced)
        assert cache.lookup("nvidia/z-ai/glm-5.2") == (0.0, 0.0)
        assert cache.lookup("nvidia/microsoft/phi-4-mini") == (0.0, 0.0)
        # Lab/model form without gateway provider prefix
        assert cache.lookup("z-ai/glm-5.2") == (0.0, 0.0)
        assert cache.lookup("microsoft/phi-4-mini") == (0.0, 0.0)

    def test_lookup_strips_gateway_namespace(self):
        """Nimmakai ids like nim/deepseek-chat should resolve via bare name."""
        cache = _make_cache()
        assert cache.lookup("nim/deepseek-chat") == (0.14, 0.28)
        assert cache.lookup("zen/gpt-4o") == (2.5, 10.0)
        assert cache.lookup("deepseek-chat") == (0.14, 0.28)

    def test_lookup_case_insensitive(self):
        cache = _make_cache()
        assert cache.lookup("OpenAI/GPT-4O") == (2.5, 10.0)
        assert cache.lookup("  DeepSeek/DeepSeek-Chat  ") == (0.14, 0.28)

    def test_lookup_missing_model_returns_none(self):
        cache = _make_cache()
        assert cache.lookup("openai/nonexistent-model") is None
        assert cache.lookup("totally-unknown-provider/model") is None

    def test_models_without_cost_excluded(self):
        cache = _make_cache()
        rates = cache.all_rates()
        assert "free-provider/some-model" not in rates
        assert "free-provider/other-model" not in rates

    def test_all_rates_returns_copy(self):
        cache = _make_cache()
        rates1 = cache.all_rates()
        rates2 = cache.all_rates()
        assert rates1 == rates2
        rates1["x/y"] = (1.0, 2.0)
        assert "x/y" not in rates2

    def test_invalidation_forces_refetch(self):
        cache = _make_cache()
        assert cache.is_loaded
        cache.invalidate()
        assert cache._is_expired()

    def test_ttl_expires(self):
        cache = ModelsDevCostCache(url="http://localhost:99999/no-such-url", ttl_seconds=0)
        with patch("nimmakai.analytics.models_cost.httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = SAMPLE_API_RESPONSE
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp
            cache._ensure_loaded()
            assert mock_get.call_count == 1
            # With ttl=0, next call should re-fetch
            cache._ensure_loaded()
            assert mock_get.call_count == 2

    def test_network_error_graceful(self):
        cache = ModelsDevCostCache(
            url="http://localhost:99999/no-such-url", ttl_seconds=3600
        )
        err_patch = patch(
            "nimmakai.analytics.models_cost.httpx.get",
            side_effect=Exception("network error"),
        )
        with err_patch:
            cache._ensure_loaded()
        assert not cache.is_loaded
        assert cache.all_rates() == {}

    def test_malformed_json_graceful(self):
        cache = ModelsDevCostCache(url="http://localhost:99999/no-such-url", ttl_seconds=3600)
        mock_resp = MagicMock()
        mock_resp.json.return_value = "not a dict"
        mock_resp.raise_for_status.return_value = None
        with patch("nimmakai.analytics.models_cost.httpx.get", return_value=mock_resp):
            cache._ensure_loaded()
        assert not cache.is_loaded

    def test_thread_safety(self):
        cache = _make_cache()
        results: list[tuple[float, float] | None] = []

        def _lookup(model_id: str) -> None:
            results.append(cache.lookup(model_id))

        threads = [threading.Thread(target=_lookup, args=("openai/gpt-4o",)) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 20
        assert all(r == (2.5, 10.0) for r in results)


# ── integration with cost.py ────────────────────────────────────────


class TestCostIntegration:
    def test_dynamic_primary_source(self):
        cache = _make_cache()
        with patch("nimmakai.analytics.cost.lookup_dynamic", side_effect=cache.lookup):
            rates = lookup_rates("openai/gpt-4o")
            assert rates == (2.5, 10.0)

    def test_free_pattern_overrides_dynamic(self):
        cache = _make_cache()
        with patch("nimmakai.analytics.cost.lookup_dynamic", side_effect=cache.lookup):
            # groq/ should be free regardless of models.dev data
            rates = lookup_rates("groq/some-model")
            assert rates == (0.0, 0.0)

    def test_override_overrides_dynamic(self):
        cache = _make_cache()
        overrides = {"openai/gpt-4o": (999.0, 999.0)}
        with patch("nimmakai.analytics.cost.lookup_dynamic", side_effect=cache.lookup):
            rates = lookup_rates("openai/gpt-4o", overrides=overrides)
            assert rates == (999.0, 999.0)

    def test_hardcoded_fallback_when_dynamic_misses(self):
        with patch("nimmakai.analytics.cost.lookup_dynamic", return_value=None):
            # Should fall back to hardcoded MODEL_COST_PER_M
            rates = lookup_rates("gpt-4o")
            assert rates == (2.50, 10.0)

    def test_hardcoded_fallback_fuzzy_match(self):
        with patch("nimmakai.analytics.cost.lookup_dynamic", return_value=None):
            rates = lookup_rates("gpt-4o-2024-08-06")
            assert rates == (2.50, 10.0)

    def test_unknown_model_returns_zero(self):
        with patch("nimmakai.analytics.cost.lookup_dynamic", return_value=None):
            rates = lookup_rates("completely-unknown-model")
            assert rates == (0.0, 0.0)

    def test_estimate_cost_uses_dynamic(self):
        cache = _make_cache()
        with patch("nimmakai.analytics.cost.lookup_dynamic", side_effect=cache.lookup):
            cost = estimate_cost("openai/gpt-4o", 1_000_000, 1_000_000)
            assert cost == pytest.approx(2.5 + 10.0)

    def test_list_default_rates_includes_dynamic(self):
        cache = _make_cache()
        with patch("nimmakai.analytics.cost.all_dynamic_rates", side_effect=cache.all_rates):
            rates = list_default_rates()
            model_ids = {r["model_id"] for r in rates}
            # Should contain models from both sources
            assert "openai/gpt-4o" in model_ids
            assert "gpt-4o" in model_ids  # hardcoded fallback

    def test_list_default_rates_dynamic_overrides_hardcoded(self):
        cache = _make_cache()
        with patch("nimmakai.analytics.cost.all_dynamic_rates", side_effect=cache.all_rates):
            rates = list_default_rates()
            by_id = {r["model_id"]: r for r in rates}
            # Dynamic key "deepseek/deepseek-chat" uses models.dev value
            assert by_id["deepseek/deepseek-chat"]["input_per_m"] == 0.14
            # Hardcoded key "deepseek-chat" retains its own value
            assert by_id["deepseek-chat"]["input_per_m"] == 0.27


# ── live API smoke test (skipped in CI) ─────────────────────────────


@pytest.mark.skipif(
    not pytest.importorskip("httpx"),
    reason="httpx not installed",
)
def test_live_models_dev_fetch():
    """Verify the real API is reachable and returns expected structure.

    Skipped when network is unavailable.
    """
    import httpx

    try:
        resp = httpx.get("https://models.dev/api.json", timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        pytest.skip("models.dev API unreachable")

    assert isinstance(data, dict)
    assert len(data) > 10
    # Spot-check known providers
    assert "openai" in data
    openai_models = data["openai"].get("models", {})
    assert "gpt-4o" in openai_models
    gpt4o = openai_models["gpt-4o"]
    cost = gpt4o.get("cost", {})
    assert "input" in cost
    assert "output" in cost
    assert isinstance(cost["input"], (int, float))
    assert isinstance(cost["output"], (int, float))
