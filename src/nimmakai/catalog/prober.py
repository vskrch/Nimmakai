"""RPM-safe model probes and disk snapshot for fail-safe routing."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nimmakai.upstream import UpstreamClient

logger = logging.getLogger(__name__)


class ProbeBudget:
    """Limit probe calls so we do not clog free-tier RPM."""

    def __init__(self, max_per_hour: int = 8) -> None:
        self.max_per_hour = max(0, max_per_hour)
        self._times: list[float] = []

    def _prune(self, now: float) -> None:
        cutoff = now - 3600.0
        self._times = [t for t in self._times if t >= cutoff]

    def remaining(self) -> int:
        self._prune(time.monotonic())
        return max(0, self.max_per_hour - len(self._times))

    def consume(self) -> bool:
        if self.max_per_hour <= 0:
            return False
        self._prune(time.monotonic())
        if len(self._times) >= self.max_per_hour:
            return False
        self._times.append(time.monotonic())
        return True


async def probe_models(
    upstream: UpstreamClient,
    model_ids: list[str],
    budget: ProbeBudget,
    *,
    timeout_hint: float = 15.0,
) -> dict[str, str]:
    """
    Probe candidates with tiny chat completions.
    Returns map model_id → status: ok | rate_limited | unavailable | error | skipped
    """
    results: dict[str, str] = {}
    for mid in model_ids:
        if not budget.consume():
            results[mid] = "skipped"
            logger.info("probe budget exhausted; skipping remaining")
            break
        try:
            status, body, _headers, _key = await upstream.request_json(
                "POST",
                "/chat/completions",
                json_body={
                    "model": mid,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 8,
                },
                max_retries=1,
            )
            if status == 200:
                results[mid] = "ok"
            elif status == 429:
                results[mid] = "rate_limited"  # hosted
            elif status in {404, 403}:
                results[mid] = "unavailable"
            else:
                results[mid] = f"error_{status}"
            logger.info("probe %s → %s", mid, results[mid])
        except Exception as exc:
            results[mid] = "error"
            logger.warning("probe %s failed: %s", mid, exc)
        await asyncio.sleep(0.35)
    return results


def save_snapshot(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("failed to load catalog snapshot %s", path)
        return None
