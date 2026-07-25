"""504 cascade fix — immediate advance + health cooldown (NMK-R2xx, NMK-H701)."""

from __future__ import annotations

import time

from nimmakai.catalog.health import ModelHealthStore


def test_504_cooldown():
    """Model that 504s gets a health cooldown (NMK-H701)."""
    health = ModelHealthStore(gateway_timeout_cooldown_seconds=30.0)
    health.record_outcome("bad-model", success=False, status_code=504)
    h = health._by_model.get("bad-model")
    assert h is not None
    assert h.in_cooldown(), "Model should be in cooldown after 504"


def test_504_adaptive_cooldown_grows():
    """Three consecutive 504s = 90s cooldown (3x base)."""
    health = ModelHealthStore(gateway_timeout_cooldown_seconds=30.0)
    for _ in range(3):
        health.record_outcome("bad-model", success=False, status_code=504)
    h = health._by_model.get("bad-model")
    remain = h.cooldown_until - time.monotonic()
    assert 80 <= remain <= 95, f"3x 504 should be ~90s, got {remain:.1f}"


def test_503_cooldown_unchanged():
    """503 still uses hard_fail_cooldown_seconds."""
    health = ModelHealthStore(hard_fail_cooldown_seconds=5.0)
    health.record_outcome("m", success=False, status_code=503)
    h = health._by_model.get("m")
    assert h.in_cooldown()


def test_429_cooldown_uses_config():
    """429 uses rate_limit_cooldown_seconds."""
    health = ModelHealthStore(rate_limit_cooldown_seconds=15.0)
    health.record_outcome("m", success=False, status_code=429)
    h = health._by_model.get("m")
    assert h.in_cooldown()


def test_health_config_fields():
    """All health config fields are accepted by ModelHealthStore (NMK-C102)."""
    h = ModelHealthStore(
        error_rate_threshold=0.6,
        model_cooldown_seconds=50.0,
        hard_fail_cooldown_seconds=8.0,
        max_cooldown_seconds=200.0,
        rate_limit_cooldown_seconds=20.0,
        gateway_timeout_cooldown_seconds=35.0,
        health_window_size=10,
        recent_success_window_seconds=45.0,
    )
    assert h.error_rate_threshold == 0.6
    assert h.gateway_timeout_cooldown_seconds == 35.0
    assert h.health_window_size == 10


def test_deadline_config():
    """Settings has the fixed deadline values (NMK-C104)."""
    from nimmakai.config import Settings

    s = Settings()
    assert s.request_deadline_seconds == 120.0
    assert s.upstream_timeout == 120.0
    assert s.stream_ttft_timeout_seconds == 15.0
    assert s.stream_idle_timeout_seconds == 60.0


def test_intent_budget_config():
    """Settings has per-intent attempt budgets (NMK-C103)."""
    from nimmakai.config import Settings

    s = Settings()
    assert s.intent_attempt_budget_seconds["reasoning"] == 45.0
    assert s.intent_attempt_budget_seconds["chat_fast"] == 15.0
    assert s.intent_max_fallbacks["coding_agentic"] == 10
    assert s.intent_max_fallbacks["embeddings"] == 4
    assert not hasattr(s, "coding_max_fallbacks")


def test_attempt_budget_for_intent():
    """FallbackExecutor._attempt_budget_for returns per-intent budget."""
    from nimmakai.config import Settings
    from nimmakai.routing.fallback import FallbackExecutor

    class FakeReg:
        pass

    class FakeHub:
        pass

    class FakeUpstream:
        pass

    settings = Settings()
    fe = FallbackExecutor(FakeUpstream(), FakeReg(), settings, hub=FakeHub())
    # coding_agentic: 30s
    assert fe._attempt_budget_for("coding_agentic", 100.0) == 30.0
    # reasoning: 45s
    assert fe._attempt_budget_for("reasoning", 100.0) == 45.0
    # chat_fast: 15s
    assert fe._attempt_budget_for("chat_fast", 100.0) == 15.0
    # When remaining < budget, returns remaining
    assert fe._attempt_budget_for("reasoning", 10.0) == 10.0
    # Floor at 1.0
    assert fe._attempt_budget_for("reasoning", 0.5) == 1.0


def test_max_n_for_intent():
    """FallbackExecutor._max_n_for_intent returns per-intent fallback cap."""
    from nimmakai.config import Settings
    from nimmakai.routing.fallback import FallbackExecutor

    class FakeReg:
        pass

    class FakeHub:
        pass

    class FakeUpstream:
        pass

    settings = Settings()
    fe = FallbackExecutor(FakeUpstream(), FakeReg(), settings, hub=FakeHub())
    assert fe._max_n_for_intent("coding_agentic") == 10
    assert fe._max_n_for_intent("embeddings") == 4
    assert fe._max_n_for_intent("unknown_intent") == 10  # default