"""Global in-flight concurrency gate."""

from __future__ import annotations

import asyncio
import time


class GlobalConcurrencyGate:
    def __init__(self, max_in_flight: int) -> None:
        self.max_in_flight = max(0, max_in_flight)
        self._in_flight = 0
        self._lock = asyncio.Lock()

    @property
    def in_flight(self) -> int:
        return self._in_flight

    async def acquire(self, max_wait: float = 30.0) -> None:
        if self.max_in_flight <= 0:
            return
        deadline = time.monotonic() + max_wait
        while True:
            async with self._lock:
                if self._in_flight < self.max_in_flight:
                    self._in_flight += 1
                    return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Global concurrency limit reached. Retry later "
                    "(nimmakai_pool_exhausted)."
                )
            await asyncio.sleep(0.05)

    async def release(self) -> None:
        if self.max_in_flight <= 0:
            return
        async with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
