"""Multi-key pool with RPM limiting and response-rate aware selection."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class KeyStats:
    """Live stats for one upstream API key."""

    key_id: str
    api_key: str
    # Sliding window of request timestamps (monotonic)
    request_times: deque[float] = field(default_factory=deque)
    # EWMA of latency in seconds (successes only)
    ewma_latency: float = 1.0
    success_count: int = 0
    error_count: int = 0
    rate_limit_hits: int = 0
    # Monotonic time until which this key is cooling down (after 429)
    cooldown_until: float = 0.0
    in_flight: int = 0

    def mask(self) -> str:
        k = self.api_key
        if len(k) <= 12:
            return "***"
        return f"{k[:8]}...{k[-4:]}"


class KeyPool:
    """
    Rotates across NVIDIA NIM API keys.

    Selection goals:
    1. Never pick a key that would exceed RPM (with safety factor).
    2. Prefer keys with lower recent latency / fewer failures (response-rate aware).
    3. Spread load (shuffle + weighted pick) so no single account is hammered.
    """

    def __init__(
        self,
        api_keys: list[str],
        rpm_limit: float = 36.0,
        cooldown_seconds: float = 60.0,
        window_seconds: float = 60.0,
    ) -> None:
        if not api_keys:
            raise ValueError("At least one NIM API key is required (NIM_API_KEYS)")

        self.rpm_limit = rpm_limit
        self.cooldown_seconds = cooldown_seconds
        self.window_seconds = window_seconds
        self._lock = asyncio.Lock()
        self._keys: list[KeyStats] = [
            KeyStats(key_id=f"key-{i}", api_key=k) for i, k in enumerate(api_keys)
        ]
        # Round-robin offset for fair first-pick when scores tie
        self._rr = 0

    def __len__(self) -> int:
        return len(self._keys)

    def _prune(self, stats: KeyStats, now: float) -> None:
        cutoff = now - self.window_seconds
        while stats.request_times and stats.request_times[0] < cutoff:
            stats.request_times.popleft()

    def _rpm_used(self, stats: KeyStats, now: float) -> int:
        self._prune(stats, now)
        return len(stats.request_times)

    def _is_available(self, stats: KeyStats, now: float) -> bool:
        if now < stats.cooldown_until:
            return False
        return self._rpm_used(stats, now) < self.rpm_limit

    def _score(self, stats: KeyStats, now: float) -> float:
        """
        Higher is better.
        Factors: remaining RPM headroom, inverse latency, low error rate, low in-flight.
        """
        used = self._rpm_used(stats, now)
        headroom = max(0.0, self.rpm_limit - used - stats.in_flight * 0.5)
        # Latency: prefer faster keys (cap to avoid division blowups)
        latency = max(0.05, stats.ewma_latency)
        latency_score = 1.0 / latency

        total = stats.success_count + stats.error_count
        success_rate = (stats.success_count / total) if total else 1.0

        # Soft penalty for in-flight concurrency on the same key
        concurrency_penalty = 1.0 / (1.0 + stats.in_flight)

        return headroom * latency_score * (0.3 + 0.7 * success_rate) * concurrency_penalty

    async def acquire(self, max_wait: float = 30.0) -> KeyStats:
        """
        Pick the best available key. Waits briefly if all keys are at RPM limit.
        Raises RuntimeError if nothing becomes available within max_wait.
        """
        deadline = time.monotonic() + max_wait
        while True:
            async with self._lock:
                now = time.monotonic()
                available = [k for k in self._keys if self._is_available(k, now)]
                if available:
                    # Weighted random over all available keys (score = headroom ×
                    # inverse latency × success × concurrency). Equal scores →
                    # uniform shuffle; healthier / faster keys get more weight.
                    weights = [max(0.01, self._score(k, now)) for k in available]
                    pick = random.choices(available, weights=weights, k=1)[0]
                    pick.in_flight += 1
                    pick.request_times.append(now)
                    logger.debug(
                        "acquired %s rpm=%s/%s in_flight=%s score=%.3f",
                        pick.key_id,
                        self._rpm_used(pick, now),
                        self.rpm_limit,
                        pick.in_flight,
                        self._score(pick, now),
                    )
                    return pick

            # All saturated — wait for the next window slot
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "All NIM API keys are rate-limited or cooling down. "
                    "Add more keys or wait for the RPM window to free up."
                )
            await asyncio.sleep(0.25)

    async def release(
        self,
        stats: KeyStats,
        *,
        success: bool,
        latency: float | None = None,
        rate_limited: bool = False,
        status_code: int | None = None,
    ) -> None:
        async with self._lock:
            stats.in_flight = max(0, stats.in_flight - 1)
            if rate_limited or status_code == 429:
                stats.rate_limit_hits += 1
                stats.error_count += 1
                stats.cooldown_until = time.monotonic() + self.cooldown_seconds
                # Undo the RPM slot we reserved so we don't over-count after cooldown
                # (keep history accurate — the request did happen)
                logger.warning(
                    "%s hit rate limit; cooling down %.0fs",
                    stats.key_id,
                    self.cooldown_seconds,
                )
                return

            if success:
                stats.success_count += 1
                if latency is not None:
                    # EWMA with alpha=0.3
                    stats.ewma_latency = 0.7 * stats.ewma_latency + 0.3 * latency
            else:
                stats.error_count += 1

    def snapshot(self) -> list[dict]:
        now = time.monotonic()
        out = []
        for k in self._keys:
            self._prune(k, now)
            out.append(
                {
                    "id": k.key_id,
                    "masked_key": k.mask(),
                    "rpm_used": len(k.request_times),
                    "rpm_limit": self.rpm_limit,
                    "in_flight": k.in_flight,
                    "ewma_latency_s": round(k.ewma_latency, 3),
                    "success_count": k.success_count,
                    "error_count": k.error_count,
                    "rate_limit_hits": k.rate_limit_hits,
                    "cooling_down": now < k.cooldown_until,
                    "cooldown_remaining_s": max(0.0, round(k.cooldown_until - now, 1)),
                    "available": self._is_available(k, now),
                }
            )
        return out
