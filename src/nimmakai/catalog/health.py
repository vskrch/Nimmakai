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
    # Adaptive responsiveness (monotonic clocks)
    last_success_at: float = 0.0
    last_fail_at: float = 0.0
    consecutive_fails: int = 0
    consecutive_successes: int = 0

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

    error_rate_threshold: float = 0.45
    min_samples: int = 2
    # Short cooldowns = auto-adaptive recovery (was 600s — too sticky for free tiers)
    model_cooldown_seconds: float = 45.0
    hard_fail_cooldown_seconds: float = 5.0
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
        now = time.monotonic()
        h = self._model(model_id)
        if unavailable or status_code == 404:
            h.unavailable_count += 1
            h.error_count += 1
            h.consecutive_fails += 1
            h.consecutive_successes = 0
            h.last_fail_at = now
            # Adaptive cooldown grows with consecutive fails, capped
            cool = min(
                180.0,
                self.model_cooldown_seconds * (1.0 + 0.5 * min(h.consecutive_fails, 6)),
            )
            h.cooldown_until = now + cool
        elif success:
            h.success_count += 1
            h.consecutive_successes += 1
            h.consecutive_fails = 0
            h.last_success_at = now
            # Immediate recovery: clear cooldown on success
            h.cooldown_until = 0.0
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
            h.consecutive_fails += 1
            h.consecutive_successes = 0
            h.last_fail_at = now
            # Fail-fast cool: 5xx / timeout-like — short so we skip quickly then retry soon
            if status_code is not None and status_code >= 500:
                h.cooldown_until = max(
                    h.cooldown_until,
                    now + self.hard_fail_cooldown_seconds * min(h.consecutive_fails, 3),
                )
            elif status_code == 429:
                h.cooldown_until = max(h.cooldown_until, now + 15.0)

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
        # Just recovered this request path — treat as healthy immediately
        if h.consecutive_successes > 0 and h.consecutive_fails == 0:
            return False
        total = h.success_count + h.error_count
        if total < self.min_samples:
            return False
        return h.error_rate > self.error_rate_threshold

    def health_score(self, model_id: str) -> float:
        """
        Continuous health in [0, 1] for multiplicative scoring.

        1.0  = healthy or unknown (optimistic default)
        0.01 = in cooldown (near-zero but not fully excluded)
        Intermediate = proportional to (1 - error_rate)
        """
        h = self._by_model.get(model_id)
        if h is None:
            return 1.0  # unknown = optimistic
        if h.in_cooldown():
            return 0.01
        total = h.success_count + h.error_count
        if total < self.min_samples:
            return 1.0  # not enough data
        return max(0.05, 1.0 - h.error_rate)

    def health_reorder(self, chain: list[str]) -> list[str]:
        """
        Adaptive: sticky quality order, but always try **responding** models first.

        1. Demote cooldown / high-error models to the tail
        2. Within the healthy head window, promote recent successes + low latency
           (auto-adaptive, no full ladder recompute — zero extra delay)
        """
        if len(chain) <= 1:
            return list(chain)

        healthy = [m for m in chain if not self.is_unhealthy(m)]
        unhealthy = [m for m in chain if self.is_unhealthy(m)]
        if len(healthy) <= 1:
            return healthy + unhealthy

        # Adaptive window: demote non-responding within the quality head.
        # Do NOT reorder purely by latency among healthy models (preserve intelligence).
        window = min(8, len(healthy))
        head = healthy[:window]
        tail = healthy[window:]
        now = time.monotonic()
        order = {m: i for i, m in enumerate(head)}

        def _resp_key(mid: str) -> tuple:
            h = self._by_model.get(mid)
            sticky = order.get(mid, 99)
            if h is None or h.samples == 0:
                return (0, 0, sticky)  # unknown keeps sticky rank
            # Penalty only when not responding / failing
            fail_pen = h.consecutive_fails
            if fail_pen == 0 and h.error_rate <= self.error_rate_threshold:
                # Among fully healthy: optional micro-boost for *very* recent success
                # without overturning quality order (sticky dominates)
                hot = 0
                if h.last_success_at > 0 and (now - h.last_success_at) < 30.0:
                    hot = -1
                return (0, hot, sticky)
            # Failing / flaky: push back; among failers prefer lower fail streak
            recency_fail = 0.0
            if h.last_fail_at > 0:
                recency_fail = now - h.last_fail_at
            return (1, fail_pen, -recency_fail, sticky)

        head_sorted = sorted(head, key=_resp_key)
        return head_sorted + tail + unhealthy

    def responsive_score(self, model_id: str) -> float:
        """0..1-ish score for diagnostics: higher = better responding."""
        h = self._by_model.get(model_id)
        if h is None:
            return 0.7  # unknown optimistic
        if h.in_cooldown():
            return 0.0
        if h.samples == 0:
            return 0.7
        base = 1.0 - h.error_rate
        if h.ewma_latency > 0:
            base *= max(0.2, min(1.5, 1.5 / (0.5 + h.ewma_latency)))
        if h.consecutive_successes >= 2:
            base *= 1.15
        return max(0.0, min(2.0, base))

    def expire_stale_cooldowns(self) -> int:
        """Clear cooldowns that have elapsed — self-healing after rate limits."""
        now = time.monotonic()
        cleared = 0
        for h in self._by_model.values():
            if h.cooldown_until > 0 and h.cooldown_until <= now:
                h.cooldown_until = 0.0
                cleared += 1
        return cleared

    def soften_all_cooldowns(self, factor: float = 0.5) -> None:
        """Shrink remaining cooldowns (used after successful heal refresh)."""
        now = time.monotonic()
        for h in self._by_model.values():
            if h.cooldown_until > now:
                remain = h.cooldown_until - now
                h.cooldown_until = now + remain * max(0.1, min(1.0, factor))

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

    def provider_health(
        self, provider_models: set[str], provider_id: str = ""
    ) -> float:
        """Aggregate health across all models for a provider.

        0 = all models down, 1 = all models healthy (or unknown).
        Used by the optimizer's provider prior for health-weighted routing.
        """
        if not provider_models:
            return 1.0
        scores: list[float] = []
        for mid in provider_models:
            h = self._by_model.get(mid)
            if h is None:
                scores.append(0.7)
                continue
            if h.in_cooldown():
                scores.append(0.05)
                continue
            total = h.success_count + h.error_count
            if total < self.min_samples:
                scores.append(0.7)
                continue
            scores.append(max(0.05, 1.0 - h.error_rate))
        return sum(scores) / len(scores) if scores else 1.0
