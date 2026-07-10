"""Backoff helpers."""

from __future__ import annotations

from nimmakai.safety.backoff import compute_backoff_seconds


def test_exponential_grows_then_caps() -> None:
    # Disable randomness influence by checking structure via many samples' min
    d0 = compute_backoff_seconds(0, base=0.5, cap=16.0)
    d3 = compute_backoff_seconds(3, base=0.5, cap=16.0)
    d10 = compute_backoff_seconds(10, base=0.5, cap=16.0)
    assert 0.5 <= d0 <= 0.5 * 1.2
    assert 4.0 <= d3 <= 4.0 * 1.2
    assert 16.0 <= d10 <= 16.0 * 1.2


def test_retry_after_wins() -> None:
    d = compute_backoff_seconds(0, base=0.5, cap=16.0, retry_after=30.0)
    assert d >= 30.0
