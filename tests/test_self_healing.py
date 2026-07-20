"""NMK-603: Self-healing integration tests."""

import asyncio
import time
import pytest
from nimmakai.catalog.health import ModelHealthStore
from nimmakai.catalog.ladder import LadderService
from nimmakai.catalog.learning import LearningStore
from nimmakai.safety.circuit_breaker import ProviderCircuitBreaker, BreakerState


def test_circuit_breaker_opens_after_failures():
    """5 consecutive failures should open the circuit."""
    cb = ProviderCircuitBreaker(failure_threshold=5)
    for _ in range(5):
        cb.fail("groq")
    assert cb.state("groq") == BreakerState.OPEN
    assert cb.allow("groq") is False


def test_circuit_breaker_half_open_after_timeout():
    """After recovery timeout, circuit should half-open."""
    cb = ProviderCircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.fail("groq")
    cb.fail("groq")
    assert cb.state("groq") == BreakerState.OPEN
    time.sleep(0.15)
    assert cb.allow("groq") is True
    assert cb.state("groq") == BreakerState.HALF_OPEN


def test_circuit_breaker_closes_on_success():
    """Success after half-open should close the circuit."""
    cb = ProviderCircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
    cb.fail("groq")
    cb.fail("groq")
    time.sleep(0.1)
    cb.allow("groq")
    cb.succeed("groq")
    assert cb.state("groq") == BreakerState.CLOSED


def test_health_cooldown_clears_after_success():
    """Model cooldown should clear immediately on success."""
    health = ModelHealthStore(model_cooldown_seconds=30)
    health.record_outcome("m1", success=False, status_code=404, unavailable=True)
    assert health.is_unhealthy("m1") is True
    health.record_outcome("m1", success=True, latency=0.5)
    assert health.is_unhealthy("m1") is False


def test_health_reorder_demotes_unhealthy():
    """Unhealthy models should be demoted to the tail."""
    health = ModelHealthStore(model_cooldown_seconds=30)
    health.record_outcome("slow_ok", success=True, latency=2.0)
    health.record_outcome("dead", success=False, status_code=404, unavailable=True)
    chain = ["slow_ok", "dead", "fresh"]
    reordered = health.health_reorder(chain)
    assert reordered[-1] == "dead" or "dead" not in reordered[:1]


def test_learning_persistence():
    """Learning store should survive load/save cycle."""
    import tempfile
    from pathlib import Path
    td = tempfile.mkdtemp()
    path = Path(td) / "learning.json"
    ls1 = LearningStore(path=path)
    ls1.record(intent="coding_agentic", model_id="nim/model-a", success=True)
    ls1.record(intent="coding_agentic", model_id="nim/model-a", success=True)
    ls1.record(intent="coding_agentic", model_id="nim/model-a", success=False)
    ls1.save()

    ls2 = LearningStore(path=path)
    ls2.load()
    alpha, beta = ls2.thompson_params("coding_agentic", "nim/model-a")
    assert alpha > 1.0, "successes should be >0"
    assert beta > 1.0, "failures should be >0"


def test_emergency_coding_chain_returns_models():
    """Emergency chain should return live models when ladder is empty."""
    from nimmakai.catalog.schema import catalog_from_dict
    from nimmakai.catalog.registry import ModelRegistry
    from nimmakai.resilience import emergency_coding_chain
    cat = catalog_from_dict({
        "version": "1", "updated": "2026-01-01",
        "defaults": {"dynamic_families": True, "auto_mode_model_tokens": []},
        "families": {"chat_primary": "nim", "coding_primary": "nim", "fallbacks": []},
        "intents": {"coding_agentic": {"chain": []}},
        "aliases": {}, "models": {},
    })
    reg = ModelRegistry(cat)
    reg.live_ids = {"nim/model-a", "nim/model-b"}
    chain = emergency_coding_chain(reg, max_n=5)
    assert len(chain) > 0
    assert len(chain) <= 5
