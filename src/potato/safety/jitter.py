"""Request jitter before upstream calls."""

from __future__ import annotations

import asyncio
import random


async def apply_jitter(*, enabled: bool, min_ms: float, max_ms: float) -> float:
    """Sleep a random delay; returns seconds slept."""
    if not enabled:
        return 0.0
    lo = max(0.0, min_ms)
    hi = max(lo, max_ms)
    delay = random.uniform(lo, hi) / 1000.0
    if delay > 0:
        await asyncio.sleep(delay)
    return delay
