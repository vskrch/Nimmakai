"""Per-provider circuit breaker (half-open/open/closed states).

Open after 5 consecutive failures across all models for a provider.
Half-open: allow 1 request every 30s to probe.
Close on success.
"""

from __future__ import annotations

import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class ProviderCircuitBreaker:
    """Per-provider circuit breaker with half-open probing."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        cooldown_multiplier: float = 2.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.cooldown_multiplier = cooldown_multiplier
        self._state: dict[str, BreakerState] = {}
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._last_probe: dict[str, float] = {}

    def allow(self, provider_id: str) -> bool:
        """True if a request may be sent to this provider."""
        pid = provider_id.lower()
        state = self._state.get(pid, BreakerState.CLOSED)
        if state == BreakerState.CLOSED:
            return True
        if state == BreakerState.HALF_OPEN:
            return True
        # OPEN: check if cooldown has elapsed → transition to half-open
        until = self._open_until.get(pid, 0)
        if time.monotonic() >= until:
            self._state[pid] = BreakerState.HALF_OPEN
            self._last_probe[pid] = time.monotonic()
            logger.info("circuit half-open → probing provider %s", pid)
            return True
        return False

    def fail(self, provider_id: str) -> None:
        """Record a failure from this provider."""
        pid = provider_id.lower()
        self._failures[pid] = self._failures.get(pid, 0) + 1
        f = self._failures[pid]
        state = self._state.get(pid, BreakerState.CLOSED)

        if state == BreakerState.HALF_OPEN:
            # Probing failed → re-open with exponential backoff
            backoff = self.recovery_timeout * (
                self.cooldown_multiplier ** min(f - self.failure_threshold, 4)
            )
            self._state[pid] = BreakerState.OPEN
            self._open_until[pid] = time.monotonic() + backoff
            logger.warning(
                "circuit re-opened for provider %s (backoff=%.0fs, failures=%s)",
                pid, backoff, f,
            )
            return

        if state == BreakerState.CLOSED and f >= self.failure_threshold:
            self._state[pid] = BreakerState.OPEN
            self._open_until[pid] = time.monotonic() + self.recovery_timeout
            logger.warning(
                "circuit opened for provider %s (%s consecutive failures)",
                pid, f,
            )

    def succeed(self, provider_id: str) -> None:
        """Record a success → close the circuit."""
        pid = provider_id.lower()
        self._state[pid] = BreakerState.CLOSED
        self._failures[pid] = 0
        self._open_until.pop(pid, None)
        if pid in self._last_probe:
            logger.info("circuit closed for provider %s (probe succeeded)", pid)

    def state(self, provider_id: str) -> BreakerState:
        return self._state.get(provider_id.lower(), BreakerState.CLOSED)

    def reset(self, provider_id: str) -> None:
        pid = provider_id.lower()
        self._state[pid] = BreakerState.CLOSED
        self._failures[pid] = 0
        self._open_until.pop(pid, None)

    def snapshot(self) -> dict[str, dict]:
        now = time.monotonic()
        out: dict[str, dict] = {}
        for pid in set(self._state.keys()) | set(self._failures.keys()):
            state = self._state.get(pid, BreakerState.CLOSED)
            out[pid] = {
                "state": state.value,
                "failures": self._failures.get(pid, 0),
                "open_until": max(0, round(self._open_until.get(pid, 0) - now, 1)),
            }
        return out
