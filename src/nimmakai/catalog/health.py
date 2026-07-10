"""Per-(model, key) and per-model health tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ModelHealth:
    ewma_latency: float = 1.0
    success_count: int = 0
    error_count: int = 0
    unavailable_count: int = 0
    cooldown_until: float = 0.0  # monotonic
    # Rough token throughput proxy: chars_or_tokens / latency when known
    ewma_tok_per_s: float = 0.0

    @property
    def error_rate(self) -> float:
        total = self.success_count + self.error_count
        if total == 0:
            return 0.0
        return self.error_count / total

    def in_cooldown(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        return now < self.cooldown_until

    @property
    def samples(self) -> int:
        return self.success_count + self.error_count


@dataclass
class ModelHealthStore:
    """In-memory health keyed by model_id and optionally (model_id, key_id)."""

    error_rate_threshold: float = 0.5
    min_samples: int = 3
    model_cooldown_seconds: float = 600.0
    _by_model: dict[str, ModelHealth] = field(default_factory=dict)
    _by_pair: dict[tuple[str, str], ModelHealth] = field(default_factory=dict)

    def _model(self, model_id: str) -> ModelHealth:
        if model_id not in self._by_model:
            self._by_model[model_id] = ModelHealth()
        return self._by_model[model_id]

    def record_outcome(
        self,
        model_id: str,
        *,
        key_id: str | None = None,
        success: bool,
        latency: float | None = None,
        status_code: int | None = None,
        unavailable: bool = False,
        tokens: int | None = None,
    ) -> None:
        h = self._model(model_id)
        if unavailable or status_code == 404:
            h.unavailable_count += 1
            h.error_count += 1
            h.cooldown_until = time.monotonic() + self.model_cooldown_seconds
        elif success:
            h.success_count += 1
            if latency is not None and latency > 0:
                h.ewma_latency = 0.7 * h.ewma_latency + 0.3 * latency
                if tokens is not None and tokens > 0:
                    tps = tokens / latency
                    if h.ewma_tok_per_s <= 0:
                        h.ewma_tok_per_s = tps
                    else:
                        h.ewma_tok_per_s = 0.7 * h.ewma_tok_per_s + 0.3 * tps
        else:
            h.error_count += 1
            # Sustained hard failures (5xx) also cool down the model briefly
            if status_code is not None and status_code >= 500:
                h.cooldown_until = max(
                    h.cooldown_until,
                    time.monotonic() + min(120.0, self.model_cooldown_seconds / 5),
                )

        if key_id:
            pair = self._by_pair.setdefault((model_id, key_id), ModelHealth())
            if unavailable or status_code == 404:
                pair.unavailable_count += 1
                pair.error_count += 1
            elif success:
                pair.success_count += 1
                if latency is not None:
                    pair.ewma_latency = 0.7 * pair.ewma_latency + 0.3 * latency
            else:
                pair.error_count += 1

    def is_unhealthy(self, model_id: str) -> bool:
        """True only when unavailable / cooldown / high error rate — never for slowness."""
        h = self._by_model.get(model_id)
        if h is None:
            return False
        if h.in_cooldown():
            return True
        total = h.success_count + h.error_count
        if total < self.min_samples:
            return False
        return h.error_rate > self.error_rate_threshold

    def health_reorder(self, chain: list[str]) -> list[str]:
        """
        Power-first: keep preference order.
        Only demote models that are unavailable, in cooldown, or erroring.
        Never demote a stronger model because a weaker one is faster.
        """
        if len(chain) <= 1:
            return list(chain)

        healthy = [m for m in chain if not self.is_unhealthy(m)]
        unhealthy = [m for m in chain if self.is_unhealthy(m)]
        return healthy + unhealthy

    def snapshot(self) -> dict[str, dict]:
        now = time.monotonic()
        out: dict[str, dict] = {}
        for mid, h in self._by_model.items():
            out[mid] = {
                "ewma_latency_s": round(h.ewma_latency, 3),
                "ewma_tok_per_s": round(h.ewma_tok_per_s, 2),
                "success_count": h.success_count,
                "error_count": h.error_count,
                "unavailable_count": h.unavailable_count,
                "error_rate": round(h.error_rate, 3),
                "cooling_down": h.in_cooldown(now),
                "cooldown_remaining_s": max(0.0, round(h.cooldown_until - now, 1)),
            }
        return out
