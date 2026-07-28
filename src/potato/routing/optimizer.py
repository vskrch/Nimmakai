"""
Continuous request-time optimizer: always pick best intelligence × speed × latency.

On every request (not just cache refresh):

    score(m) = intel(m)^α × speed(m)^β × lat(m)^λ × avail(m)^γ × prov(m)^δ

where:
  intel  = ladder precomputed score (quality × affinity × capability) normalized to (0, 1]
  speed  = live EWMA tokens/s (throughput)
  lat    = live EWMA TTFT / latency (inverse — lower latency = higher factor)
  avail  = health / responding (cooldown → near 0)
  prov   = provider speed prior (Zen, Groq, Cerebras, …)

Priority ladder enforced by exponent magnitudes:
  intelligence + knowledge >> speed > latency > availability >> provider
  α dominates: a 95-intel model at 40 TPS beats an 80-intel model at 120 TPS.
  Dead models never lead (availability gate near-zero).
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from potato.catalog.registry import ModelRegistry

logger = logging.getLogger(__name__)

# 5-tuple weights: (intel, speed, lat, avail, prov)
# Priority: intelligence+knowledge >> speed > latency > availability >> provider
# A 95-intel model at 40 TPS beats an 80-intel model at 120 TPS.
# Loaded from YAML scoring.intent_optimizer_weights at startup (NMK-RT601).
# ponytail: lat defaults to speed*0.45 when YAML omits it (preserves the old
# 4-key blend where speed conflated throughput+latency 0.55/0.45).
_INTENT_WEIGHTS: dict[str, tuple[float, float, float, float, float]] = {
    "coding_agentic": (0.92, 0.04, 0.02, 0.01, 0.01),
    "reasoning": (0.92, 0.04, 0.02, 0.01, 0.01),
    "long_horizon": (0.92, 0.04, 0.02, 0.01, 0.01),
    "chat_fast": (0.85, 0.08, 0.04, 0.02, 0.01),
    "vision": (0.88, 0.06, 0.03, 0.02, 0.01),
    "embeddings": (0.75, 0.15, 0.06, 0.03, 0.01),
    "_default": (0.88, 0.06, 0.03, 0.02, 0.01),
}


def load_intent_weights(yaml_scoring: dict) -> None:
    """Called at startup after yaml is loaded. Overrides default weights from YAML.

    Accepts 5-key (intel, speed, lat, avail, prov) or legacy 4-key (lat derived
    from speed*0.45 to preserve the old throughput+latency blend ratio).
    """
    global _INTENT_WEIGHTS
    for intent, w in (yaml_scoring.get("intent_optimizer_weights") or {}).items():
        with contextlib.suppress(KeyError, TypeError, ValueError):
            speed_v = float(w["speed"])
            _INTENT_WEIGHTS[intent] = (
                float(w["intel"]),
                speed_v,
                float(w.get("lat", speed_v * 0.45)),
                float(w["avail"]),
                float(w["prov"]),
            )


def _quality_prior(
    model_id: str,
    *,
    ladder_scores: dict[str, float] | None,
    max_score: float | None = None,
) -> float:
    """Quality prior normalized to (0, 1]."""
    raw: float | None = None
    if ladder_scores:
        raw = ladder_scores.get(model_id)
    if raw is None:
        with contextlib.suppress(Exception):
            from potato.catalog.score_cache import ModelScoreCache
            cache = ModelScoreCache.current()
            if cache:
                ms = cache.get(model_id)
                if ms is not None:
                    raw = float(ms.quality)
    if raw is None or raw <= 0 or raw != raw:
        return 0.50
    if max_score is None and ladder_scores:
        max_score = max(ladder_scores.values())
    if max_score is None or max_score <= 0:
        max_score = 100.0
    return max(0.35, min(1.0, float(raw) / float(max_score)))


def _speed_factor(health: Any, model_id: str) -> float:
    """
    Live throughput (tokens/sec) factor 0.25–2.4 — pure generation speed.
    Unknown models get a mild prior from provider; proven-fast models climb hard.
    Latency/TTFT is handled separately by _latency_factor so exponents can
    weight throughput above raw latency (priority: speed > latency).
    """
    h = health._by_model.get(model_id) if health is not None else None
    if h is None or (h.samples == 0 and h.ewma_tok_per_s <= 0):
        from potato.catalog.presets import speed_prior_for_provider
        from potato.catalog.providers import split_provider_model

        provider_ids = getattr(health, "_provider_ids", set()) if health is not None else set()
        pid, _ = split_provider_model(model_id, provider_ids, default_provider="nim")
        prior = speed_prior_for_provider(pid)
        return max(0.40, min(2.20, prior))

    # Tokens/sec (normalize ~40 TPS = 1.0, 120+ = elite)
    tps = h.ewma_tok_per_s
    tps_f = min(2.4, max(0.25, tps / 40.0)) if tps > 0 else 0.8

    # Throughput hot streak — small boost for models on a success run
    streak = 1.0
    if h.consecutive_successes >= 3:
        streak = 1.12
    elif h.consecutive_fails >= 2:
        streak = 0.75
    return tps_f * streak


def _latency_factor(health: Any, model_id: str) -> float:
    """
    Live latency (TTFT) factor 0.2–2.2 — lower latency = higher factor.
    0.15s → boost, 1s → ~1.0, 3s+ → cut. Independent from throughput so the
    optimizer can weight speed (TPS) above raw latency per the priority ladder.
    """
    h = health._by_model.get(model_id) if health is not None else None
    if h is None or h.samples == 0:
        # Unknown latency: neutral-to-mild prior (don't punish untested models)
        return 1.0
    lat = h.ewma_latency if h.ewma_latency > 0 else 1.0
    return min(2.2, max(0.2, 1.15 / (0.3 + lat)))


def _provider_factor(model_id: str, provider_ids: set[str], health: Any = None) -> float:
    from potato.catalog.presets import speed_prior_for_provider
    from potato.catalog.providers import split_provider_model

    pid, _ = split_provider_model(model_id, provider_ids, default_provider="nim")
    prior = speed_prior_for_provider(pid)
    # NMK-403: weight provider prior by aggregate health
    if health is not None:
        provider_models = {m for m in getattr(health, "_by_model", {}) if m.startswith(pid + "/")}
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
        # Bayesian Laplace smoothing for low sample counts: (success + 1) / (total + 2)
        return max(0.1, min(1.0, (h.success_count + 1.0) / (total + 2.0)))
    return max(0.08, 1.0 - h.error_rate)


def score_model_live(
    model_id: str,
    *,
    ladder_scores: dict[str, float] | None,
    health: Any,
    provider_ids: set[str],
    max_score: float | None = None,
    intent: str = "_default",
) -> float:
    """Single composite score for continuous ranking (intent-weighted).

    Priority: intelligence+knowledge >> speed > latency > availability >> provider
    Enforced by per-intent exponents (alpha > beta > lat_exp > gamma > delta).
    """
    if health is not None and health.is_unhealthy(model_id):
        return 1e-6 * _quality_prior(model_id, ladder_scores=ladder_scores, max_score=max_score)

    alpha, beta, lat_exp, gamma, delta = _INTENT_WEIGHTS.get(intent, _INTENT_WEIGHTS["_default"])

    intel = _quality_prior(model_id, ladder_scores=ladder_scores, max_score=max_score)
    speed = _speed_factor(health, model_id)
    lat = _latency_factor(health, model_id)
    avail = _availability_factor(health, model_id)
    prov = _provider_factor(model_id, provider_ids, health)

    score = (intel**alpha) * (speed**beta) * (lat**lat_exp) * (avail**gamma) * (prov**delta)

    # Dynamic RL feedback multiplier bounded in [0.5, 2.0]
    rl_engine = getattr(health, "_rl_engine", None) if health is not None else None
    if rl_engine is not None:
        with contextlib.suppress(Exception):
            from potato.routing.rl_features import extract_feature_vector

            x = extract_feature_vector({"messages": []}, intent_name=intent)
            ucb_score, _, _ = rl_engine.score(model_id, x)
            if abs(ucb_score) > 1e-4:
                rl_boost = max(0.50, min(2.00, 1.0 + ucb_score))
                score *= rl_boost

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
    max_score: float | None = None
    if ladder is not None:
        snap = getattr(ladder, "_ladders", {}).get((intent, variant))
        if snap is not None and getattr(snap, "scores", None):
            ladder_scores = dict(snap.scores)
            # Precompute max_score once instead of per-model
            if ladder_scores:
                max_score = max(ladder_scores.values())

    scored: list[tuple[float, str]] = []
    for mid in sticky:
        s = score_model_live(
            mid,
            ladder_scores=ladder_scores,
            health=health,
            provider_ids=provider_ids,
            max_score=max_score,
            intent=intent,
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
    max_score: float | None = None
    if ladder is not None:
        snap = getattr(ladder, "_ladders", {}).get((intent, variant))
        if snap is not None and getattr(snap, "scores", None):
            ladder_scores = dict(snap.scores)
            if ladder_scores:
                max_score = max(ladder_scores.values())

    rows = []
    for mid in sticky[: max(n * 3, 12)]:
        intel = _quality_prior(mid, ladder_scores=ladder_scores, max_score=max_score)
        speed = _speed_factor(health, mid)
        lat = _latency_factor(health, mid)
        hs = health.health_score(mid) if health else 1.0
        total = score_model_live(
            mid,
            ladder_scores=ladder_scores,
            health=health,
            provider_ids=provider_ids,
            max_score=max_score,
            intent=intent,
        )
        rows.append(
            {
                "model": mid,
                "score": round(total, 4),
                "intelligence": round(intel, 3),
                "speed": round(speed, 3),
                "latency": round(lat, 3),
                "health": round(hs, 3),
                "unhealthy": bool(health and health.is_unhealthy(mid)),
            }
        )
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:n]
