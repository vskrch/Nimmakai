"""
Intelligent model routing with classical optimization algorithms.

Scoring pipeline:
    score(m, intent) = quality(m) × affinity(m, intent) × capability(m, intent)
                     × health(m)
                     + ucb_bonus(m, intent)
                     + thompson_bonus(m, intent)

Chain construction: greedy best-first sort — no family pinning.
"""

from __future__ import annotations

import logging
import math
import random
import re
from dataclasses import dataclass, field
from typing import Any

from nimmakai.catalog.docs_fetcher import DocModel
from nimmakai.catalog.families import (
    NEMOTRON_EXCLUDE,
    QWEN_EXCLUDE,
    version_key,
)
from nimmakai.catalog.health import ModelHealthStore
from nimmakai.catalog.learning import LearningStore
from nimmakai.catalog.providers import scoring_model_id
from nimmakai.catalog.score_cache import ModelScoreCache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Parameter-size extraction for cold-start quality estimation.
#
# Production: quality comes from ModelScoreCache (internet-primary).
# Cold start (no cache): log-scale estimate from parameter count.
# ---------------------------------------------------------------------------

PARAM_RE = re.compile(r"(?:^|[^a-z0-9])(\d{1,4})b(?:[^a-z0-9]|$)", re.I)


# ---------------------------------------------------------------------------
# 2. Intent affinity + keyword tables — DELETED (NMK-L502, NMK-L503).
#    Affinity is now computed from live internet data in score_cache.py.
#    INTENT_KEYWORDS doc bonus is superseded by Thompson posteriors.
# ---------------------------------------------------------------------------

# Modality exclusion for non-LLM endpoints (kept — cold-start modality gate)
CHAT_EXCLUDE = re.compile(
    r"(embed|rerank|ocr|asr|safety|guard|tts|image-edit|diffusion|"
    r"protein|fold|yolo|page-elements|table-structure|voicechat)",
    re.I,
)


@dataclass
class ScoredModel:
    model_id: str
    score: float
    quality: float = 0.0
    affinity: float = 1.0
    capability: float = 1.0
    health: float = 1.0
    ucb_bonus: float = 0.0
    thompson_bonus: float = 0.0
    doc_bonus: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass
class LadderSnapshot:
    intent: str
    ladder: list[str]
    scores: dict[str, float]
    built_from_live: int


class LadderService:
    """
    Quality-first model routing using classical optimization.

    Algorithms:
        - ModelScoreCache: quality from internet-primary intel (AA, HF, Arena)
        - Multi-criteria scoring: quality × affinity × capability × health
        - UCB1: upper confidence bound exploration bonus
        - Thompson Sampling: Bayesian online learning from outcomes

    Lifecycle: call `rebuild(live_ids)` at startup / explicit cache refresh.
    When ``frozen=True`` (default after rebuild), ``ladder_for`` serves the
    precomputed cache — no per-request re-score (stable + fast for production).

    Config knobs (NMK-C101): ucb_c, diversity_streak, default_affinity,
    thompson_scale, thompson_blend_n — all overridable via Settings.
    """

    def __init__(
        self,
        health: ModelHealthStore | None = None,
        learning: LearningStore | None = None,
        *,
        ucb_c: float = 5.0,
        diversity_streak_max: int = 2,
        default_affinity: float = 0.85,
        thompson_scale: float = 16.0,
        thompson_blend_n: int = 12,
    ) -> None:
        self.health = health or ModelHealthStore()
        self.learning = learning or LearningStore()
        # Config-driven tuning constants (NMK-C101)
        self._ucb_c = ucb_c
        self._diversity_streak = diversity_streak_max
        self._default_affinity = default_affinity
        self._thompson_scale = thompson_scale
        self._thompson_blend_n = thompson_blend_n
        self._ladders: dict[tuple[str, str], LadderSnapshot] = {}
        self._docs_by_slug: dict[str, DocModel] = {}
        self.live_ids: set[str] = set()
        # Capability hints learned from probes / docs: model_id → flags
        self.capabilities: dict[str, dict[str, bool]] = {}
        # Quality score overrides for custom/explicit models (NMK-104)
        self.quality_overrides: dict[str, float] = {}
        # Overridable from config/models.yaml
        self.provider_ids: set[str] = {"nim"}
        # Sticky rankings: freeze after precompute until explicit refresh
        self.frozen: bool = False
        self.computed_at: float = 0.0

    def apply_catalog_policy(
        self,
        *,
        primary_by_intent: dict[str, str] | None = None,
        fallback_families: list[str] | None = None,
    ) -> None:
        """Wire soft family policy from models.yaml.

        NMK-L502: family affinity is now computed in score_cache.py from live
        internet data. This method is kept for call-site compat but is a no-op;
        primary/fallback hints are absorbed by the score cache's capability
        deltas and quality signals.
        """
        return

    def set_docs(self, docs: list[DocModel]) -> None:
        self._docs_by_slug = {d.slug.lower().replace("_", "-"): d for d in docs}

    def rebuild(
        self,
        live_ids: set[str],
        *,
        intents: list[str] | None = None,
        freeze: bool = True,
    ) -> None:
        """Precompute all intent ladders (expensive). Call at startup / cache refresh."""
        import time as _time

        self.live_ids = set(live_ids)
        targets = intents or [
            "coding_agentic",
            "chat_fast",
            "reasoning",
            "long_horizon",
            "vision",
            "embeddings",
        ]
        variants = ["default", "cheap", "fast"]
        for intent in targets:
            for variant in variants:
                self._ladders[(intent, variant)] = self._build_ladder(
                    intent, variant=variant
                )
        self.computed_at = _time.time()
        self.frozen = freeze
        logger.info(
            "ladder precomputed frozen=%s (%s intents × 3 variants, %s live): %s",
            freeze,
            len(targets),
            len(live_ids),
            {
                k[0]: v.ladder[:3]
                for k, v in self._ladders.items()
                if k[1] == "default"
            },
        )

    def freeze(self) -> None:
        self.frozen = True

    def unfreeze(self) -> None:
        self.frozen = False

    def export_cache(self) -> dict:
        """Serialize precomputed ladders for SQLite / disk persistence."""
        ladders: dict[str, dict] = {}
        for (intent, variant), snap in self._ladders.items():
            key = f"{intent}::{variant}"
            ladders[key] = {
                "intent": intent,
                "variant": variant,
                "ladder": list(snap.ladder),
                "scores": dict(snap.scores),
                "built_from_live": snap.built_from_live,
            }
        return {
            "version": 1,
            "computed_at": self.computed_at,
            "frozen": self.frozen,
            "live_ids": sorted(self.live_ids),
            "ladders": ladders,
            "best_coding": list(
                (
                    self._ladders.get(("coding_agentic", "default"))
                    or LadderSnapshot("", [], {}, 0)
                ).ladder[:12]
            ),
            "best_chat": list(
                (self._ladders.get(("chat_fast", "default")) or LadderSnapshot("", [], {}, 0)).ladder[:8]
            ),
        }

    def import_cache(self, data: dict, *, freeze: bool = True) -> bool:
        """Restore ladders from persisted cache. Returns False if unusable."""
        ladders = data.get("ladders") if isinstance(data, dict) else None
        if not isinstance(ladders, dict) or not ladders:
            return False
        restored: dict[tuple[str, str], LadderSnapshot] = {}
        for _k, raw in ladders.items():
            if not isinstance(raw, dict):
                continue
            intent = str(raw.get("intent") or "")
            variant = str(raw.get("variant") or "default")
            ladder = list(raw.get("ladder") or [])
            if not intent or not ladder:
                continue
            scores = raw.get("scores") or {}
            if not isinstance(scores, dict):
                scores = {}
            restored[(intent, variant)] = LadderSnapshot(
                intent=intent,
                ladder=ladder,
                scores={str(a): float(b) for a, b in scores.items()},
                built_from_live=int(raw.get("built_from_live") or len(ladder)),
            )
        if not restored:
            return False
        self._ladders = restored
        cached_live = data.get("live_ids") or []
        if isinstance(cached_live, list) and cached_live:
            # Keep union so offline models drop via filter; new live ids still known
            self.live_ids = set(self.live_ids) | {str(x).lower() for x in cached_live}
        self.computed_at = float(data.get("computed_at") or data.get("_updated_at") or 0)
        self.frozen = freeze
        logger.info(
            "ladder cache restored (%s entries, computed_at=%.0f, best_coding=%s)",
            len(restored),
            self.computed_at,
            (data.get("best_coding") or [])[:3],
        )
        return True

    def is_coding_capable(self, model_id: str) -> bool:
        """True if a model can serve coding_agentic (capability + modality gate).

        Reuses the same gates as scoring so the request-time candidate pool
        matches what the ladder would rank — without a full score computation.
        """
        bare = scoring_model_id(model_id, self.provider_ids)
        mid = bare.lower()
        if CHAT_EXCLUDE.search(mid):
            return False
        if "qwen" in mid and QWEN_EXCLUDE.search(mid):
            return False
        if "nemotron" in mid and NEMOTRON_EXCLUDE.search(mid):
            return False
        return self._capability_score(model_id, mid, "coding_agentic") >= 0.01

    def ladder_for(
        self, intent: str, *, variant: str = "default", max_n: int | None = None
    ) -> list[str]:
        """
        Best-first chain from the precomputed cache.

        When frozen (production default), does **not** re-score — rankings stay
        sticky until ``rebuild()`` / admin cache refresh. Offline models are
        filtered out; health reorder still applies at the registry layer.
        """
        key = (intent, variant)
        snap = self._ladders.get(key)
        if snap is None or not self.frozen:
            # Missing cache entry, or unfrozen (explicit recompute mode)
            snap = self._build_ladder(intent, variant=variant)
            self._ladders[key] = snap

        out: list[str] = []
        for mid in snap.ladder:
            if mid not in self.live_ids and self.live_ids:
                continue
            out.append(mid)
            if max_n is not None and len(out) >= max_n:
                break

        if not out and snap.ladder:
            # Cache had models no longer live — serve cache head as last resort
            out = list(snap.ladder[: max_n or len(snap.ladder)])
        return out

    # ------------------------------------------------------------------
    # Core scoring
    # ------------------------------------------------------------------

    def score_model(
        self, model_id: str, intent: str, *, variant: str = "default"
    ) -> ScoredModel:
        """Multi-criteria composite score for a model on a given intent and variant."""
        bare = scoring_model_id(model_id, self.provider_ids)
        mid = bare.lower()
        reasons: list[str] = []

        # ── Modality gates (hard exclude) ────────────────────────
        if intent == "embeddings":
            if "embed" not in mid and "retrieval" not in mid:
                return ScoredModel(model_id, -1e9, reasons=["not_embedding"])
            # Embeddings get a flat high score — quality differences are small
            return ScoredModel(model_id, 50.0, quality=50.0, reasons=["embedding"])

        if intent == "vision":
            is_vision = any(k in mid for k in ("vl", "vision", "omni", "minimax-m3"))
            if not is_vision:
                doc = self._doc_for(model_id)
                desc = (doc.description if doc else "").lower()
                if not any(k in desc for k in ("vision", "vlm", "multimodal")):
                    return ScoredModel(model_id, -1e9, reasons=["not_vision"])

        # Generic chat/coding — exclude non-LLM endpoints
        if intent not in ("vision", "embeddings"):
            if CHAT_EXCLUDE.search(mid):
                return ScoredModel(model_id, -1e9, reasons=["excluded_modality"])
            if intent == "coding_agentic" and QWEN_EXCLUDE.search(mid) and "qwen" in mid:
                return ScoredModel(model_id, -1e9, reasons=["qwen_non_text"])
            if "nemotron" in mid and NEMOTRON_EXCLUDE.search(mid):
                return ScoredModel(model_id, -1e9, reasons=["nemotron_non_chat"])

        # ── 1. Benchmark quality (from ModelScoreCache or param fallback) ──
        quality = self._base_quality(mid, model_id)
        reasons.append(f"quality={quality:.0f}")

        # ── 2. Intent affinity (from ModelScoreCache or default) ──
        affinity = self._intent_affinity(mid, intent, model_id)
        reasons.append(f"affinity={affinity:.2f}")

        # ── 3. Capability gate (from ModelScoreCache modalities or probe caps) ──
        capability = self._capability_score(model_id, mid, intent)
        if capability < 0.01:
            reasons.append("capability_blocked")
        elif capability > 1.01:
            reasons.append(f"capability_bonus={capability:.2f}")

        # ── 4. Health (continuous 0-1) ───────────────────────────
        health_s = self.health.health_score(model_id)
        if health_s < 0.99:
            reasons.append(f"health={health_s:.2f}")

        # ── 5. Variant Multipliers (Cost / Speed) ────────────────
        variant_mult = 1.0
        if variant == "cheap":
            variant_mult = self._cost_multiplier(mid)
            reasons.append(f"cheap_mult={variant_mult:.2f}")
        elif variant == "fast":
            variant_mult = self._speed_multiplier(model_id)
            reasons.append(f"fast_mult={variant_mult:.2f}")

        # ── 5b. Provider speed prior (intent-agnostic; intent tradeoffs
        #        are handled by intent_optimizer_weights in the request-time
        #        optimizer, not here) ────────────────────────────────
        provider_prior = self._provider_speed_prior(model_id)
        if variant == "default":
            provider_prior = 1.0 + (provider_prior - 1.0) * 0.60
        elif variant == "cheap":
            provider_prior = 1.0 + (provider_prior - 1.0) * 0.25
        if abs(provider_prior - 1.0) > 0.02:
            reasons.append(f"provider_prior={provider_prior:.2f}")

        # ── Composite multiplicative score ───────────────────────
        # NMK-L505: coding_elite_boost removed — absorbed into affinity
        # from score_cache's capability_affinity_deltas.
        composite = (
            quality
            * affinity
            * capability
            * health_s
            * variant_mult
            * provider_prior
        )

        # ── 6. UCB1 exploration bonus (additive) ─────────────────
        ucb = self._ucb_bonus(model_id, intent)
        if ucb > 0.5:
            reasons.append(f"ucb={ucb:.1f}")

        # ── 7. Thompson Sampling bonus (additive) ────────────────
        thompson = self._thompson_bonus(model_id, intent)
        if abs(thompson) > 0.5:
            reasons.append(f"thompson={thompson:+.1f}")

        # ── 8. Doc keyword bonus — superseded by Thompson posteriors (NMK-L503)
        doc_bonus = self._doc_keyword_bonus(model_id, mid, intent)
        if doc_bonus > 0:
            reasons.append(f"doc_kw={doc_bonus:.0f}")

        total = composite + ucb + thompson + doc_bonus

        return ScoredModel(
            model_id=model_id,
            score=total,
            quality=quality,
            affinity=affinity,
            capability=capability,
            health=health_s,
            ucb_bonus=ucb,
            thompson_bonus=thompson,
            doc_bonus=doc_bonus,
            reasons=reasons,
        )

    def _base_quality(self, mid_lower: str, model_id: str = "") -> float:
        """Quality from ModelScoreCache (internet-primary); cold-start param estimate."""
        # Check explicit overrides (NMK-104)
        override = self.quality_overrides.get(model_id)
        if override is not None:
            return float(override)
        # Live score cache — internet-primary
        cache = ModelScoreCache.current()
        if cache:
            ms = cache.scores.get(model_id) or cache.scores.get(mid_lower)
            if ms:
                return ms.quality
        # Cold start: param estimate (log-scale heuristic)
        m = PARAM_RE.search(mid_lower)
        if m:
            try:
                return min(95.0, max(10.0, 60.0 + 8.0 * math.log2(int(m.group(1)) / 7.0)))
            except (ValueError, ZeroDivisionError):
                pass
        return 65.0  # optimistic default for unknown models

    def _intent_affinity(self, mid_lower: str, intent: str, model_id: str = "") -> float:
        """Affinity from ModelScoreCache; default fallback for cold start."""
        cache = ModelScoreCache.current()
        if cache:
            ms = cache.scores.get(model_id) or cache.scores.get(mid_lower)
            if ms:
                return ms.intent_affinity.get(intent, self._default_affinity)
        return self._default_affinity

    def _capability_score(
        self, model_id: str, mid_lower: str, intent: str
    ) -> float:
        """Capability gate from ModelScoreCache modalities; probe-based fallback."""
        cache = ModelScoreCache.current()
        ms = cache.scores.get(model_id) if cache else None

        if ms:
            if intent == "vision":
                if "vision" in ms.modalities:
                    return 1.10
                if ms.sources:
                    return 0.0  # confirmed absence
            elif intent == "embeddings":
                if "embeddings" in ms.modalities:
                    return 1.10
                if ms.sources:
                    return 0.0
            elif intent == "coding_agentic":
                if "tools" in ms.modalities:
                    return 1.15
            elif intent == "reasoning":
                if "reasoning" in ms.modalities:
                    return 1.20
            return 1.0

        # Fallback: probe-based caps dict
        caps = self.capabilities.get(model_id) or {}
        if intent == "coding_agentic":
            if caps.get("supports_tools") is True:
                return 1.15
            if caps.get("supports_tools") is False:
                return 0.1
        if intent == "vision":
            if caps.get("supports_vision") is True:
                return 1.10
            if caps.get("supports_vision") is False:
                return 0.0
        if intent == "reasoning":
            if caps.get("supports_reasoning") is True:
                return 1.20
            if caps.get("supports_reasoning") is False:
                return 0.8
        return 1.0

    def model_recommendations(self, model_id: str) -> dict[str, Any]:
        """Return per-model recommendations for temperature, max_tokens, etc."""
        caps = self.capabilities.get(model_id) or {}
        mid = model_id.lower()
        rec: dict[str, Any] = {}

        # Temperature recommendations based on model type
        if caps.get("supports_reasoning") or any(
            k in mid for k in ("o1", "o3", "r1", "deepseek-r1")
        ):
            rec["temperature"] = 1.0  # reasoning models default to 1
        elif any(k in mid for k in ("coder", "code", "coding")):
            rec["temperature"] = 0.0  # coding models work best with 0
        # Max tokens limits based on known model capabilities
        if caps.get("max_output_tokens"):
            rec["max_tokens_limit"] = caps["max_output_tokens"]
        elif any(k in mid for k in ("gpt-4o", "gpt-4.1")):
            rec["max_tokens_limit"] = 16384
        elif any(k in mid for k in ("claude",)):
            rec["max_tokens_limit"] = 8192
        elif any(k in mid for k in ("deepseek",)):
            rec["max_tokens_limit"] = 8192
        # Structured outputs support
        if caps.get("supports_structured_outputs") is True:
            rec["supports_structured_outputs"] = True
        elif any(
            k in mid
            for k in ("gpt-4o", "gpt-4.1", "gpt-4o-mini", "claude", "gemini")
        ):
            rec["supports_structured_outputs"] = True
        return rec

    def _cost_multiplier(self, mid_lower: str) -> float:
        """
        Heuristic: smaller models are cheaper.
        8B gets ~1.6x multiplier, 70B gets ~0.37x, 400B gets ~0.07x.
        This aggressively promotes smaller models for 'cheap' routing.
        """
        m = PARAM_RE.search(mid_lower)
        if m:
            try:
                params_b = int(m.group(1))
                return max(0.05, 30.0 / (params_b + 10.0))
            except ValueError:
                pass
        return 0.5  # Unknown cost: penalize moderately to favor known-small models

    def _speed_multiplier(self, model_id: str) -> float:
        """
        Heuristic: route to highest Tokens Per Second (TPS).
        Tracked dynamically in health.py based on real outcomes.
        Falls back to provider speed priors for free ultra-fast backends.
        """
        h = self.health._by_model.get(model_id)
        if h and h.ewma_tok_per_s > 0:
            # Normalization: 40 TPS is a good baseline (1.0).
            # 120 TPS gives 3.0x score. Cap at 5.0x.
            return min(5.0, h.ewma_tok_per_s / 40.0)
        # Unknown measured speed — use provider prior so free fast backends
        # (Groq, Cerebras, …) still win on auto-fast before probes run.
        return self._provider_speed_prior(model_id)

    def _provider_speed_prior(self, model_id: str) -> float:
        """Multiplicative prior from free/fast OpenAI-compatible providers."""
        from nimmakai.catalog.presets import speed_prior_for_provider
        from nimmakai.catalog.providers import split_provider_model

        pid, _ = split_provider_model(
            model_id, self.provider_ids, default_provider="nim"
        )
        return speed_prior_for_provider(pid)

    def _ucb_bonus(self, model_id: str, intent: str) -> float:
        """
        UCB1 exploration bonus: C × √(ln(N) / nₘ)

        Models with fewer samples get a larger bonus, encouraging exploration
        of potentially better but untested models.
        """
        total_n = self.learning.total_requests(intent)
        model_n = self.learning.model_requests(intent, model_id)

        if total_n < 2:
            return self._ucb_c * 2.0  # generous bonus when system is cold-starting

        if model_n == 0:
            # Never tried: give maximum exploration bonus
            return self._ucb_c * math.sqrt(math.log(total_n + 1))

        return self._ucb_c * math.sqrt(math.log(total_n + 1) / model_n)

    def _thompson_bonus(self, model_id: str, intent: str) -> float:
        """
        Thompson Sampling with production-stable damping.

        α = successes + 1 (optimistic prior)
        β = failures + 1

        When frozen: use deterministic posterior mean so two identical startups
        produce identical ladders. Random exploration only during unfrozen recompute.
        """
        alpha, beta = self.learning.thompson_params(intent, model_id)
        mean = alpha / (alpha + beta)
        if self.frozen:
            return (mean - 0.5) * self._thompson_scale
        sample = random.betavariate(alpha, beta)
        model_n = self.learning.model_requests(intent, model_id)
        weight = min(1.0, model_n / float(self._thompson_blend_n))
        blended = weight * sample + (1.0 - weight) * mean
        return (blended - 0.5) * self._thompson_scale

    def _doc_keyword_bonus(
        self, model_id: str, mid_lower: str, intent: str
    ) -> float:
        """Superseded by Thompson-posterior-based intent affinity (NMK-L503)."""
        return 0.0

    # ------------------------------------------------------------------
    # Chain construction
    # ------------------------------------------------------------------

    def _build_ladder(self, intent: str, *, variant: str = "default") -> LadderSnapshot:
        """Greedy best-first + family diversity for resilient multi-provider fallback."""
        scored: list[ScoredModel] = []
        for mid in self.live_ids:
            s = self.score_model(mid, intent, variant=variant)
            if s.score > -1e8:
                scored.append(s)

        # Greedy sort: highest composite score first
        # Tiebreak: version_key (higher version wins), then shorter id
        scored.sort(
            key=lambda s: (
                s.score,
                version_key(scoring_model_id(s.model_id, self.provider_ids)),
            ),
            reverse=True,
        )

        # Diversify head of chain so fallback isn't 6 near-identical models
        scored = self._diversify_scored(scored)

        ladder = [s.model_id for s in scored]
        scores = {s.model_id: round(s.score, 2) for s in scored}
        return LadderSnapshot(
            intent=intent,
            ladder=ladder,
            scores=scores,
            built_from_live=len(self.live_ids),
        )

    def _diversify_scored(self, scored: list[ScoredModel]) -> list[ScoredModel]:
        """
        Interleave families at the head of the ladder.

        Keeps global quality order when families differ; when the same family
        would dominate the top slots, pull the next-best different family
        forward so fallback actually changes backends/models.
        """
        if len(scored) <= 2:
            return scored

        def family_of(mid: str) -> str:
            bare = scoring_model_id(mid, self.provider_ids).lower()
            for fam in (
                "mimo",
                "opencode",
                "deepseek",
                "claude",
                "kimi",
                "moonshot",
                "grok",
                "qwen",
                "gemini",
                "gpt",
                "nemotron",
                "glm",
                "step",
                "minimax",
                "llama",
                "gemma",
                "mistral",
                "mixtral",
            ):
                if fam in bare:
                    return fam
            # Provider prefix as weak family (groq/..., openrouter/...)
            if "/" in mid:
                return mid.split("/", 1)[0].lower()
            return bare[:12] if bare else "unknown"

        out: list[ScoredModel] = []
        remaining = list(scored)
        streak_fam: str | None = None
        streak = 0

        while remaining:
            pick_idx = 0
            if streak >= self._diversity_streak and streak_fam is not None:
                for i, s in enumerate(remaining):
                    if family_of(s.model_id) != streak_fam:
                        # Only jump if score is within 25% of the head (don't
                        # promote garbage just for diversity)
                        if s.score >= remaining[0].score * 0.75:
                            pick_idx = i
                        break
            chosen = remaining.pop(pick_idx)
            fam = family_of(chosen.model_id)
            if fam == streak_fam:
                streak += 1
            else:
                streak_fam = fam
                streak = 1
            out.append(chosen)

        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _doc_for(self, model_id: str) -> DocModel | None:
        slug = model_id.rsplit("/", 1)[-1].lower().replace("_", "-")
        return self._docs_by_slug.get(slug)

    def snapshot(self) -> dict:
        import time as _time

        age = None
        if self.computed_at:
            age = round(_time.time() - self.computed_at, 1)
        return {
            intent: {
                "ladder_head": snap.ladder[:5],
                "ladder_len": len(snap.ladder),
                "scores_head": {m: snap.scores.get(m) for m in snap.ladder[:5]},
                "built_from_live": snap.built_from_live,
            }
            for (intent, variant), snap in self._ladders.items()
            if variant == "default"
        } | {
            "_cache": {
                "frozen": self.frozen,
                "computed_at": self.computed_at,
                "age_s": age,
                "entries": len(self._ladders),
            }
        }

    def set_capability(self, model_id: str, **flags: bool) -> None:
        cur = self.capabilities.setdefault(model_id, {})
        cur.update(flags)
