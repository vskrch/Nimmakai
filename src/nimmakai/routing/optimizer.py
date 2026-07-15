"""
Continuous request-time optimizer: always pick best intelligence × speed.

On every request (not just cache refresh):

    score(m) = I(m)^α × S(m)^β × H(m)^γ × P(m)^δ

where:
  I = intelligence prior (sticky precomputed ladder score / rank)
  S = live speed (EWMA tokens/s + inverse latency)
  H = health / responding (cooldown → near 0)
  P = provider speed prior (Zen, Groq, Cerebras, …)

α,β,γ,δ favor coding: intelligence + speed dominate; dead models never lead.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nimmakai.catalog.registry import ModelRegistry

logger = logging.getLogger(__name__)

# Weights: capability (quality gate) + availability + low-latency dominate.
# ponytail: efficiency = best coder that is up *right now* and answers fastest.
_ALPHA_INTEL = 0.42
_BETA_SPEED = 0.46
_GAMMA_AVAIL = 0.12
_DELTA_PROVIDER = 0.04


def _intelligence_prior(
    model_id: str,
    *,
    sticky_chain: list[str],
    ladder_scores: dict[str, float] | None,
) -> float:
    """Capability prior: narrow range so live speed+availability dominate.

    ponytail: frozen rank position must never overpower a fast, available model
    that isn't in the sticky chain yet. Range 0.65–1.0 keeps capability as a
    quality gate while letting speed+availability decide the head.
    """
    if ladder_scores:
        if model_id in ladder_scores:
            raw = float(ladder_scores[model_id])
            return max(0.65, min(1.0, raw / 160.0))
        return 0.70  # not in frozen ladder — neutral, speed decides
    if model_id in sticky_chain:
        rank = sticky_chain.index(model_id)
        return max(0.65, 1.0 / (1.0 + 0.06 * rank))
    return 0.70  # no ladder at all — neutral, speed decides


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


def _provider_factor(model_id: str, provider_ids: set[str]) -> float:
    from nimmakai.catalog.presets import speed_prior_for_provider
    from nimmakai.catalog.providers import split_provider_model

    pid, _ = split_provider_model(model_id, provider_ids, default_provider="nim")
    # Map ~1.0–1.4 prior into mild 0.9–1.15
    prior = speed_prior_for_provider(pid)
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
    sticky_chain: list[str],
    ladder_scores: dict[str, float] | None,
    health: Any,
    provider_ids: set[str],
) -> float:
    """Single composite score for continuous ranking."""
    if health is not None and health.is_unhealthy(model_id):
        # Still rank cold models, but far below anything live
        return 1e-6 * _intelligence_prior(
            model_id, sticky_chain=sticky_chain, ladder_scores=ladder_scores
        )

    intel = _intelligence_prior(
        model_id, sticky_chain=sticky_chain, ladder_scores=ladder_scores
    )
    speed = _speed_factor(health, model_id)
    avail = _availability_factor(health, model_id)
    prov = _provider_factor(model_id, provider_ids)

    # Geometric combination — no zeros unless unhealthy handled above
    score = (
        (intel**_ALPHA_INTEL)
        * (speed**_BETA_SPEED)
        * (avail**_GAMMA_AVAIL)
        * (prov**_DELTA_PROVIDER)
    )
    # Tiny sticky tie-break so equal scores keep quality order
    if model_id in sticky_chain:
        score += 1e-6 * (len(sticky_chain) - sticky_chain.index(model_id))
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
            sticky_chain=sticky,
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
        intel = _intelligence_prior(
            mid, sticky_chain=sticky, ladder_scores=ladder_scores
        )
        speed = _speed_factor(health, mid)
        hs = health.health_score(mid) if health else 1.0
        total = score_model_live(
            mid,
            sticky_chain=sticky,
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
