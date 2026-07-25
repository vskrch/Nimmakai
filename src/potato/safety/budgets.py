"""UTC calendar-day request budgets (helpers; enforced in KeyPool)."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_day_key(now: datetime | None = None) -> str:
    dt = now or datetime.now(UTC)
    return dt.strftime("%Y-%m-%d")
