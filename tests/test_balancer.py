"""Unit tests for key pool selection and rate limiting."""

from __future__ import annotations

import pytest

from nimmakai.balancer import KeyPool


@pytest.mark.asyncio
async def test_acquire_rotates_across_keys() -> None:
    pool = KeyPool(api_keys=["k1", "k2", "k3"], rpm_limit=100)
    seen: set[str] = set()
    for _ in range(60):
        key = await pool.acquire()
        seen.add(key.api_key)
        await pool.release(key, success=True, latency=0.1)
    assert seen == {"k1", "k2", "k3"}


@pytest.mark.asyncio
async def test_rpm_limit_blocks_then_frees() -> None:
    pool = KeyPool(api_keys=["only"], rpm_limit=2, window_seconds=60)
    a = await pool.acquire()
    b = await pool.acquire()
    await pool.release(a, success=True, latency=0.05)
    await pool.release(b, success=True, latency=0.05)

    with pytest.raises(RuntimeError, match="rate-limited"):
        await pool.acquire(max_wait=0.4)


@pytest.mark.asyncio
async def test_cooldown_after_429() -> None:
    pool = KeyPool(api_keys=["k1", "k2"], rpm_limit=50, cooldown_seconds=2.0)
    key = await pool.acquire()
    await pool.release(key, success=False, rate_limited=True)
    # acquire should prefer the other key while first is cooling down
    other = await pool.acquire()
    assert other.api_key != key.api_key
    assert any(s["cooling_down"] for s in pool.snapshot())
    await pool.release(other, success=True, latency=0.1)


@pytest.mark.asyncio
async def test_snapshot_masks_secrets() -> None:
    pool = KeyPool(api_keys=["nvapi-super-secret-key-value-12345"], rpm_limit=10)
    snap = pool.snapshot()
    assert len(snap) == 1
    assert "super-secret" not in snap[0]["masked_key"]
    assert snap[0]["masked_key"].startswith("nvapi-su")


@pytest.mark.asyncio
async def test_prefer_faster_key() -> None:
    pool = KeyPool(api_keys=["slow", "fast"], rpm_limit=100)
    # Warm stats: mark first key as slow, second as fast
    slow = pool._keys[0]
    fast = pool._keys[1]
    slow.ewma_latency = 5.0
    fast.ewma_latency = 0.1
    slow.success_count = 10
    fast.success_count = 10

    picks = []
    for _ in range(20):
        k = await pool.acquire()
        picks.append(k.api_key)
        await pool.release(k, success=True, latency=0.1 if k.api_key == "fast" else 5.0)

    assert picks.count("fast") > picks.count("slow")
