"""Auth failure quarantine helpers."""

from __future__ import annotations

import time


def should_quarantine(auth_failures: int, threshold: int) -> bool:
    return auth_failures >= threshold


def quarantine_until(seconds: float, now: float | None = None) -> float:
    return (now if now is not None else time.monotonic()) + seconds
