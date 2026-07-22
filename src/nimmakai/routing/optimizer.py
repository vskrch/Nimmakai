"""
Continuous request-time optimizer: always pick best intelligence × speed.

On every request (not just cache refresh):

    score(m) = quality_prior(m)^α × speed(m)^β × avail(m)^γ × provider(m)^δ

where:
  quality_prior = ladder precomputed score normalized to (0, 1]
  speed = live EWMA tokens/s + inverse latency
  avail = health / responding (cooldown → near 0)
  provider = provider speed prior (Zen, Groq, Cerebras, …)

α dominates (0.55): a 95-quality model at 40 TPS beats an 80-quality at 120 TPS.
Dead models never lead (availability gate near-zero).
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nimmakai.catalog.registry import ModelRegistry

logger = logging.getLogger(__name__)

# Weights: intelligence dominates for coding efficiency + low latency.
# A 95-quality model at 40 TPS beats an 80-quality model at 120 TPS.
_ALPHA_INTEL = 0.55
_BETA_SPEED = 0.30
_GAMMA_AVAIL = 0.12
_DELTA_PROVIDER = 0.03


def _quality_prior(
    model_id: str,
    *,
    ladder_scores: dict[str, float] | None,
) -> float:
    """Quality prior from precomputed ladder scores, normalized to (0, 1].

    Uses the ladder's actual composite score to preserve the full quality
    spread. A 95-quality model maps to ~0.95, a 60-quality model to ~0.60.
    Floor at 0.35 so weak models participate as deep fallbacks only.
    """
    if not ladder_scores:
        return 0.70
    raw = ladder_scores.get(model_id)
    if raw is None:
        return 0.70
    raw = float(raw)
    if raw <= 0 or raw != raw:  # NaN guard: NaN != NaN
        return 0.50
    max_score = max(ladder_scores.values())
    if max_score <= 0:
        return 0.65
    return max(0.35, min(1.0, raw / max_score))


def _speed_factor(health: Any, model_id: str) -> float:
    """
    Live speed 0.25–2.4 — continuously adapts from TTFT + tokens/s.
    Unknown models get a mild prior; proven fast models climb hard.
    """
    h = health._by_model.get(model_id) if health is not None else None
    if h is None or (h.samples == 0 and h.ewma_tok_per_s <= 0):
        return 0.85  # unexplored — slight discount vs proven fast

    # Tokens/sec (normalize ~40 TPS = 1.0, 120+ = elite)
    tps = h.ewma_tok_per_s
    if tps > 0:
        tps_f = min(2.4, max(0.25, tps / 40.0))
    else:
        tps_f = 0.8

    # Latency / TTFT (0.15s → boost, 1s → ~1.0, 3s+ → cut)
    lat = h.ewma_latency if h.ewma_latency > 0 else 1.0
    lat_f = min(2.2, max(0.2, 1.15 / (0.3 + lat)))

    # Recent success streak → small boost (model is hot)
    streak = 1.0
    if h.consecutive_successes >= 3:
        streak = 1.12
    elif h.consecutive_fails >= 2:
        streak = 0.75

    # Blend: throughput + latency + hot streak
    return (0.50 * tps_f + 0.40 * lat_f + 0.10) * streak


def _provider_factor(model_id: str, provider_ids: set[str], health: Any = None) -> float:
    from nimmakai.catalog.presets import speed_prior_for_provider
    from nimmakai.catalog.providers import split_provider_model

    pid, _ = split_provider_model(model_id, provider_ids, default_provider="nim")
    prior = speed_prior_for_provider(pid)
    # NMK-403: weight provider prior by aggregate health
    if health is not None:
        provider_models = {
            m for m in getattr(health, "_by_model", {})
            if m.startswith(pid + "/")
        }
        if provider_models:
            agg_health = health.provider_health(provider_models, pid)
            prior *= max(0.5, agg_health)
    return max(0.85, min(1.2, 0.75 + 0.25 * prior))


def _availability_factor(health: Any, model_id: str) -> float:
    """Higher = a live upstream path exists right now (keys free + responding).

    Combines cooldown state, recent responsiveness, and key-pool exhaustion
    signal carried by the health store. 1.0 when healthy/unknown, near 0 when
    a model has no usable path this instant.
    """
    if health is None:
        return 1.0
    h = health._by_model.get(model_id)
    if h is None:
        return 1.0  # optimistic: unexplored model may serve
    if h.in_cooldown():
        return 0.02  # no available path until cooldown clears
    # Recent + consecutive failures mean limited availability right now
    if h.consecutive_fails >= 2:
        return max(0.1, 0.6 - 0.15 * h.consecutive_fails)
    total = h.success_count + h.error_count
    if total < getattr(health, "min_samples", 3):
        return 1.0
    return max(0.08, 1.0 - h.error_rate)


def score_model_live(
    model_id: str,
    *,
    ladder_scores: dict[str, float] | None,
    health: Any,
    provider_ids: set[str],
) -> float:
    """Single composite score for continuous ranking."""
    if health is not None and health.is_unhealthy(model_id):
        return 1e-6 * _quality_prior(model_id, ladder_scores=ladder_scores)

    intel = _quality_prior(model_id, ladder_scores=ladder_scores)
    speed = _speed_factor(health, model_id)
    avail = _availability_factor(health, model_id)
    prov = _provider_factor(model_id, provider_ids, health)

    score = (
        (intel**_ALPHA_INTEL)
        * (speed**_BETA_SPEED)
        * (avail**_GAMMA_AVAIL)
        * (prov**_DELTA_PROVIDER)
    )
    return score


def optimize_chain(
    chain: list[str],
    registry: ModelRegistry,
    *,
    intent: str = "coding_agentic",
    variant: str = "default",
    max_n: int | None = None,
) -> list[str]:
    """
    Always re-rank candidates for best intelligence × speed × health.

    Called on every request — O(n log n) over chain length (~10–20), no I/O.
    """
    if len(chain) <= 1:
        return list(chain)

    sticky = list(chain)
    ladder = getattr(registry, "ladder", None)
    health = getattr(registry, "health", None)
    provider_ids = set(getattr(ladder, "provider_ids", None) or {"nim"})

    ladder_scores: dict[str, float] | None = None
    if ladder is not None:
        snap = getattr(ladder, "_ladders", {}).get((intent, variant))
        if snap is not None and getattr(snap, "scores", None):
            ladder_scores = dict(snap.scores)

    scored: list[tuple[float, str]] = []
    for mid in sticky:
        s = score_model_live(
            mid,
            ladder_scores=ladder_scores,
            health=health,
            provider_ids=provider_ids,
        )
        scored.append((s, mid))

    scored.sort(key=lambda t: t[0], reverse=True)
    out = [m for _, m in scored]
    if max_n is not None:
        out = out[: max(1, max_n)]
    return out


def explain_top(
    chain: list[str],
    registry: ModelRegistry,
    *,
    intent: str = "coding_agentic",
    variant: str = "default",
    n: int = 5,
) -> list[dict[str, Any]]:
    """Debug breakdown for /admin/rankings."""
    sticky = list(chain)
    ladder = getattr(registry, "ladder", None)
    health = getattr(registry, "health", None)
    provider_ids = set(getattr(ladder, "provider_ids", None) or {"nim"})
    ladder_scores = None
    if ladder is not None:
        snap = getattr(ladder, "_ladders", {}).get((intent, variant))
        if snap is not None and getattr(snap, "scores", None):
            ladder_scores = dict(snap.scores)

    rows = []
    for mid in sticky[: max(n * 3, 12)]:
        intel = _quality_prior(mid, ladder_scores=ladder_scores)
        speed = _speed_factor(health, mid)
        hs = health.health_score(mid) if health else 1.0
        total = score_model_live(
            mid,
            ladder_scores=ladder_scores,
            health=health,
            provider_ids=provider_ids,
        )
        rows.append(
            {
                "model": mid,
                "score": round(total, 4),
                "intelligence": round(intel, 3),
                "speed": round(speed, 3),
                "health": round(hs, 3),
                "unhealthy": bool(health and health.is_unhealthy(mid)),
            }
        )
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:n]
