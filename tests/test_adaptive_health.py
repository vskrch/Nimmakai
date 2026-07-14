"""Adaptive health reorder — best + currently responding first."""

from __future__ import annotations

from nimmakai.catalog.health import ModelHealthStore


def test_adaptive_promotes_recent_success() -> None:
    h = ModelHealthStore()
    chain = ["model-a", "model-b", "model-c"]
    # a is best sticky but failing
    for _ in range(3):
        h.record_outcome("model-a", success=False, status_code=503)
    # b is responding well
    for _ in range(3):
        h.record_outcome("model-b", success=True, latency=0.2, tokens=50)
    out = h.health_reorder(chain)
    assert out[0] == "model-b"
    assert "model-a" in out  # still available as fallback


def test_cooldown_demotes() -> None:
    h = ModelHealthStore(model_cooldown_seconds=60.0)
    chain = ["x", "y"]
    h.record_outcome("x", success=False, status_code=404, unavailable=True)
    out = h.health_reorder(chain)
    assert out[0] == "y"
    assert out[-1] == "x"


def test_success_clears_cooldown() -> None:
    h = ModelHealthStore(model_cooldown_seconds=60.0)
    h.record_outcome("m", success=False, status_code=503)
    assert h.is_unhealthy("m")
    h.record_outcome("m", success=True, latency=0.3)
    assert not h.is_unhealthy("m")
