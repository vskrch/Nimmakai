"""Account safety: quarantine, sticky, jitter, budgets."""

from __future__ import annotations

import pytest

from nimmakai.balancer import KeyPool
from nimmakai.safety.jitter import apply_jitter
from nimmakai.safety.sticky import StickySessionStore
from nimmakai.upstream import parse_retry_after


@pytest.mark.asyncio
async def test_quarantine_after_auth_failures() -> None:
    pool = KeyPool(
        api_keys=["k1", "k2"],
        rpm_limit=50,
        auth_fail_threshold=2,
        auth_quarantine_seconds=60.0,
    )
    k0 = pool._keys[0]
    # Simulate two 401 releases (in_flight accounting)
    k0.in_flight = 2
    await pool.release(k0, success=False, status_code=401)
    await pool.release(k0, success=False, status_code=401)
    assert k0.auth_failures >= 2
    assert k0.quarantined_until > 0

    other = await pool.acquire()
    assert other.key_id == "key-1"
    await pool.release(other, success=True, latency=0.1)


@pytest.mark.asyncio
async def test_daily_budget_blocks_key() -> None:
    pool = KeyPool(api_keys=["only"], rpm_limit=100, rpd_limit=2, max_in_flight_per_key=5)
    a = await pool.acquire()
    await pool.release(a, success=True, latency=0.01)
    b = await pool.acquire()
    await pool.release(b, success=True, latency=0.01)
    with pytest.raises(RuntimeError, match="budget|cooling|rate-limited|quarantined"):
        await pool.acquire(max_wait=0.3)


@pytest.mark.asyncio
async def test_max_in_flight_per_key() -> None:
    pool = KeyPool(
        api_keys=["a", "b"],
        rpm_limit=100,
        max_in_flight_per_key=1,
    )
    k1 = await pool.acquire()
    k2 = await pool.acquire()
    assert {k1.api_key, k2.api_key} == {"a", "b"}
    await pool.release(k1, success=True, latency=0.01)
    await pool.release(k2, success=True, latency=0.01)


@pytest.mark.asyncio
async def test_sticky_bias() -> None:
    pool = KeyPool(api_keys=["k1", "k2"], rpm_limit=100, sticky_boost=100.0)
    preferred = "key-1"
    picks = []
    for _ in range(20):
        k = await pool.acquire(preferred_key_id=preferred)
        picks.append(k.key_id)
        await pool.release(k, success=True, latency=0.05)
    assert picks.count("key-1") > picks.count("key-0")


@pytest.mark.asyncio
async def test_jitter_disabled() -> None:
    delay = await apply_jitter(enabled=False, min_ms=100, max_ms=200)
    assert delay == 0.0


@pytest.mark.asyncio
async def test_jitter_enabled() -> None:
    delay = await apply_jitter(enabled=True, min_ms=1, max_ms=5)
    assert delay >= 0.0


def test_sticky_session_store() -> None:
    store = StickySessionStore(ttl_seconds=60)
    sid = store.resolve_session_id({"x-nimmakai-session": "abc"})
    assert sid == "abc"
    store.put(sid, "key-0")
    assert store.get(sid) == "key-0"


def test_parse_retry_after_seconds() -> None:
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after(None) is None
