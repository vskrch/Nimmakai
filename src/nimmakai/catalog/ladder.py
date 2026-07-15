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

from nimmakai.catalog.docs_fetcher import DocModel
from nimmakai.catalog.families import (
    NEMOTRON_EXCLUDE,
    QWEN_EXCLUDE,
    version_key,
)
from nimmakai.catalog.health import ModelHealthStore
from nimmakai.catalog.learning import LearningStore
from nimmakai.catalog.providers import scoring_model_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Benchmark Quality Tiers (ELO-like, 0-100 scale)
#
# Derived from public benchmarks: LMSYS Chatbot Arena, MMLU, HumanEval,
# LiveCodeBench, MATH-500.  Pattern-matched against live model IDs so no
# hardcoded full IDs are needed.
# ---------------------------------------------------------------------------

# (family_regex, size_or_tier_regex | None) → base quality score
# Ordered most-specific first; first match wins.
QUALITY_TIERS: list[tuple[str, str | None, float]] = [
    # OpenCode Zen free coding (speed + intelligence — highest priority)
    (r"mimo-v2\.5-free|mimo.*2\.5.*free", None, 99.5),
    (r"deepseek-v4-flash-free|deepseek.*v4.*flash.*free", None, 98.5),
    (r"north-mini-code-free|north.*mini.*code", None, 97.5),
    (r"big-pickle", None, 96.5),
    (r"nemotron-3-ultra-free|nemotron.*ultra.*free", None, 96.0),
    (r"qwen3\.6-plus-free|qwen.*3\.6.*free", None, 96.5),
    (r"minimax-m3-free|minimax.*m3.*free", None, 95.0),
    # OpenCode / MiMo paid+general
    (r"mimo.*2\.5.*pro|opencode.*mimo.*pro", None, 99.0),
    (r"mimo.*2\.5|opencode.*mimo|mimo-v2", None, 97.5),
    (r"mimo|opencode", None, 95.0),
    # DeepSeek (SOTA coding)
    (r"deepseek.*v4.*pro", None, 98.5),
    (r"deepseek.*r1", None, 97.0),
    (r"deepseek.*v4.*flash", None, 96.5),
    (r"deepseek.*v4", None, 96.0),
    (r"deepseek.*v3", None, 89.0),
    (r"deepseek.*coder", None, 86.0),
    (r"deepseek", None, 78.0),
    # Kimi / Moonshot coding
    (r"kimi.*k2\.7.*code|k2\.7.*code", None, 97.5),
    (r"kimi.*2\.6|kimi.*k2\.6|kimi.*k2", None, 96.5),
    (r"kimi|moonshot", None, 78.0),
    # Grok
    (r"grok.*4\.5", None, 96.0),
    (r"grok.*4", None, 94.0),
    (r"grok.*3", None, 88.0),
    (r"grok", None, 75.0),
    # Claude (via OpenRouter etc.)
    (r"claude.*opus.*4|claude-opus-4", None, 97.0),
    (r"claude.*sonnet.*4|claude-sonnet-4", None, 94.0),
    (r"claude.*3\.5.*sonnet|claude-3-5-sonnet", None, 90.0),
    (r"claude.*haiku", None, 78.0),
    (r"claude", None, 85.0),
    # GPT / OpenAI
    (r"gpt-4o(?!-mini)|gpt-4\.1(?!-mini)|o3(?!-mini)|o1(?!-mini)", None, 93.0),
    (r"gpt-4o-mini|gpt-4\.1-mini|o3-mini|o4-mini", None, 80.0),
    (r"gpt-4", None, 84.0),
    (r"gpt-3\.5", None, 65.0),
    # Gemini
    (r"gemini.*2\.5.*pro|gemini-2\.5-pro", None, 94.0),
    (r"gemini.*2\.5.*flash|gemini-2\.5-flash", None, 86.0),
    (r"gemini.*2\.0|gemini-2", None, 84.0),
    (r"gemini.*1\.5.*pro", None, 82.0),
    (r"gemini.*flash", None, 78.0),
    (r"gemini", None, 76.0),
    # Qwen frontier
    (r"qwen.*3\.5", r"397b", 95.0),
    (r"qwen.*3\.5", r"235b", 91.0),
    (r"qwen.*3\.5", r"122b", 88.0),
    (r"qwen.*3\.5", r"72b", 84.0),
    (r"qwen.*3\.5", r"30b|32b", 76.0),
    (r"qwen.*3\.5", r"14b", 72.0),
    (r"qwen.*3\.5", r"7b|8b", 68.0),
    (r"qwen.*2\.5", r"72b", 82.0),
    (r"qwen.*2\.5", r"32b", 74.0),
    (r"qwen.*2\.5", r"14b|7b", 68.0),
    # Qwen 3
    (r"qwen.*3(?!\.)", r"235b", 90.0),
    (r"qwen.*3(?!\.)", r"120b|122b", 86.0),
    (r"qwen.*3(?!\.)", r"30b|32b", 74.0),
    (r"qwen.*3(?!\.)", r"14b", 70.0),
    (r"qwen.*3(?!\.)", r"8b|7b", 66.0),
    (r"qwen.*3(?!\.)", r"4b", 60.0),
    # Qwen fallback (unknown version)
    (r"qwen", None, 70.0),
    # NVIDIA Nemotron
    (r"nemotron.*ultra", r"550b|500b", 93.0),
    (r"nemotron.*ultra", None, 92.0),
    (r"nemotron.*super", r"120b", 86.0),
    (r"nemotron.*super", r"56b|49b", 82.0),
    (r"nemotron.*super", None, 84.0),
    (r"nemotron.*pro", None, 78.0),
    (r"nemotron.*nano", None, 68.0),
    (r"nemotron", None, 75.0),
    # GLM
    (r"glm.*5\.2|glm-5", None, 87.0),
    (r"glm.*4", None, 78.0),
    (r"glm", None, 75.0),
    # Step
    (r"step.*3\.7", None, 83.0),
    (r"step", None, 72.0),
    # MiniMax
    (r"minimax.*m3|minimax-text", None, 81.0),
    (r"minimax", None, 72.0),
    # Google Gemma
    (r"gemma.*4", r"31b|27b", 74.0),
    (r"gemma.*4", r"12b", 68.0),
    (r"gemma.*3", r"27b", 70.0),
    (r"gemma", None, 65.0),
    # Llama / Meta
    (r"llama.*4", r"405b|400b|maverick|scout", 88.0),
    (r"llama.*4", r"70b|72b", 80.0),
    (r"llama.*4", r"8b|7b", 68.0),
    (r"llama.*3\.3", r"70b", 80.0),
    (r"llama.*3\.1", r"405b", 84.0),
    (r"llama.*3\.1", r"70b", 76.0),
    (r"llama.*3", r"70b|72b", 78.0),
    (r"llama.*3", r"8b|7b", 64.0),
    (r"llama", None, 65.0),
    # Mistral / Mixtral
    (r"mistral.*large", None, 82.0),
    (r"mistral.*small|mistral.*nemo", None, 72.0),
    (r"mixtral", None, 74.0),
    (r"mistral", None, 70.0),
    # Groq-hosted popular free models
    (r"llama-3\.3-70b", None, 80.0),
    (r"llama-3\.1-8b", None, 64.0),
]

# Compiled for speed
_QUALITY_COMPILED: list[tuple[re.Pattern, re.Pattern | None, float]] = [
    (re.compile(fam, re.I), re.compile(size, re.I) if size else None, q)
    for fam, size, q in QUALITY_TIERS
]

# Parameter-size extraction for fallback quality estimation
PARAM_RE = re.compile(r"(?:^|[^a-z0-9])(\d{1,4})b(?:[^a-z0-9]|$)", re.I)

# Rough param → quality mapping (logarithmic)
_PARAM_QUALITY_SLOPE = 8.0  # each doubling of params ≈ +8 quality


def _quality_from_params(param_b: int) -> float:
    """Estimate quality from parameter count using log-scale heuristic."""
    # Anchored: 7B ≈ 60, 70B ≈ 80, 400B ≈ 92
    return min(95.0, max(50.0, 60.0 + _PARAM_QUALITY_SLOPE * math.log2(param_b / 7.0)))


# ---------------------------------------------------------------------------
# 2. Intent Affinity Matrix (multiplicative, not additive)
#
# 1.0 = neutral.  A 0.3 affinity kills a high-quality model's score for that
# intent; 1.3 makes a good model great for that intent.
# ---------------------------------------------------------------------------

INTENT_AFFINITY: dict[str, dict[str, float]] = {
    "coding_agentic": {
        "mimo": 1.50,
        "opencode": 1.45,
        "north-mini": 1.42,
        "north_mini": 1.42,
        "big-pickle": 1.40,
        "big_pickle": 1.40,
        "deepseek": 1.42,
        "kimi": 1.35,
        "claude": 1.32,
        "qwen": 1.32,
        "minimax": 1.20,
        "grok": 1.22,
        "gpt": 1.18,
        "gemini": 1.15,
        "glm": 1.18,
        "nemotron": 1.12,
        "step": 1.05,
        "llama": 1.05,
        "gemma": 0.90,
        "mistral": 0.95,
    },
    "chat_fast": {
        "nemotron": 1.25,
        "gemini": 1.20,
        "gpt": 1.15,
        "gemma": 1.10,
        "glm": 1.10,
        "qwen": 1.05,
        "minimax": 1.05,
        "llama": 1.05,
        "mistral": 1.05,
        "step": 1.00,
        "deepseek": 1.00,
        "claude": 1.00,
    },
    "reasoning": {
        "deepseek": 1.40,
        "o1": 1.40,
        "o3": 1.40,
        "nemotron": 1.30,
        "mimo": 1.30,
        "grok": 1.25,
        "claude": 1.22,
        "gemini": 1.20,
        "qwen": 1.15,
        "kimi": 1.20,
        "gpt": 1.15,
        "glm": 1.10,
        "step": 1.05,
        "llama": 1.00,
        "minimax": 0.95,
        "gemma": 0.90,
        "mistral": 0.95,
    },
    "long_horizon": {
        "mimo": 1.40,
        "kimi": 1.35,
        "claude": 1.30,
        "gemini": 1.28,
        "qwen": 1.25,
        "deepseek": 1.25,
        "gpt": 1.20,
        "nemotron": 1.15,
        "grok": 1.20,
        "glm": 1.10,
        "step": 1.05,
        "llama": 1.00,
        "minimax": 1.00,
        "gemma": 0.90,
        "mistral": 0.95,
    },
    "vision": {
        "qwen": 1.35,
        "gemini": 1.35,
        "gpt": 1.30,
        "claude": 1.25,
        "mimo": 1.20,
        "minimax": 1.20,
        "llama": 1.10,
        "deepseek": 1.10,
        "glm": 1.00,
        "gemma": 0.95,
        "nemotron": 0.50,  # most nemotrons are text-only
        "step": 0.60,
        "mistral": 0.60,
    },
}

# Max consecutive same-family models at the head of a ladder (diversity)
_MAX_HEAD_FAMILY_STREAK = 2

# Default affinity for families not in the matrix
_DEFAULT_AFFINITY = 0.85

# Intent keyword boosts from NVIDIA doc descriptions / model ids
INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "coding_agentic": (
        "coding", "code", "agent", "agentic", "tool",
        "function calling", "software",
    ),
    "chat_fast": ("chat", "instruct", "general", "assistant", "conversation"),
    "reasoning": ("reason", "math", "logic", "thinking"),
    "long_horizon": ("long", "planning", "agentic", "1m", "context"),
    "vision": ("vision", "vlm", "multimodal", "image", "visual"),
    "embeddings": ("embed", "retrieval", "rerank"),
}

# Modality exclusion for non-LLM endpoints
CHAT_EXCLUDE = re.compile(
    r"(embed|rerank|ocr|asr|safety|guard|tts|image-edit|diffusion|"
    r"protein|fold|yolo|page-elements|table-structure|voicechat)",
    re.I,
)

# UCB1 exploration constant — controls explore/exploit tradeoff
# ~5.0 gives ~10-15 bonus points to untested models relative to 100-point scale
UCB_C = 5.0


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
        - Benchmark ELO: base quality from public leaderboards
        - Multi-criteria scoring: quality × affinity × capability × health
        - UCB1: upper confidence bound exploration bonus
        - Thompson Sampling: Bayesian online learning from outcomes

    Lifecycle: call `rebuild(live_ids)` at startup / explicit cache refresh.
    When ``frozen=True`` (default after rebuild), ``ladder_for`` serves the
    precomputed cache — no per-request re-score (stable + fast for production).
    """

    def __init__(
        self,
        health: ModelHealthStore | None = None,
        learning: LearningStore | None = None,
    ) -> None:
        self.health = health or ModelHealthStore()
        self.learning = learning or LearningStore()
        self._ladders: dict[tuple[str, str], LadderSnapshot] = {}
        self._docs_by_slug: dict[str, DocModel] = {}
        self.live_ids: set[str] = set()
        # Capability hints learned from probes / docs: model_id → flags
        self.capabilities: dict[str, dict[str, bool]] = {}
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
        """Wire soft family policy from models.yaml — now only boosts affinity."""
        # In the new algorithm, primary/fallback hints are absorbed as small
        # affinity nudges rather than hard pins.
        if primary_by_intent:
            for intent, fam in primary_by_intent.items():
                if fam and intent in INTENT_AFFINITY:
                    aff = INTENT_AFFINITY[intent]
                    # Ensure the preferred family has at least 1.15 affinity
                    if aff.get(fam, _DEFAULT_AFFINITY) < 1.15:
                        aff[fam] = 1.15

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
                (self._ladders.get(("coding_agentic", "default")) or LadderSnapshot("", [], {}, 0)).ladder[:12]
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
            self.live_ids = set(self.live_ids) | {str(x) for x in cached_live}
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

        # ── 1. Benchmark quality ─────────────────────────────────
        quality = self._base_quality(mid)
        reasons.append(f"quality={quality:.0f}")

        # ── 2. Intent affinity (multiplicative) ──────────────────
        affinity = self._intent_affinity(mid, intent)
        reasons.append(f"affinity={affinity:.2f}")

        # ── 3. Capability gate ───────────────────────────────────
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

        # ── 5b. Provider speed prior (best+fast across free pool) ─
        provider_prior = self._provider_speed_prior(model_id)
        if variant == "default":
            # Coding: lean into free fast hosts (Zen/Groq/Cerebras)
            if intent == "coding_agentic":
                provider_prior = 1.0 + (provider_prior - 1.0) * 0.85
            else:
                provider_prior = 1.0 + (provider_prior - 1.0) * 0.55
        elif variant == "cheap":
            provider_prior = 1.0 + (provider_prior - 1.0) * 0.25
        if abs(provider_prior - 1.0) > 0.02:
            reasons.append(f"provider_prior={provider_prior:.2f}")

        # ── 5c. Coding elite boost (OpenCode Zen free + SOTA coders) ─
        coding_boost = 1.0
        if intent == "coding_agentic":
            coding_boost = self._coding_elite_boost(model_id, mid)
            if coding_boost > 1.01:
                reasons.append(f"coding_elite={coding_boost:.2f}")
            # speed+intelligence: blend measured/prior speed into default coding
            if variant == "default":
                speed_f = self._speed_multiplier(model_id)
                # geometric-ish blend so fast elite wins over slow elite
                speed_blend = 0.62 + 0.38 * min(2.5, max(0.4, speed_f)) / 2.5
                coding_boost *= speed_blend
                reasons.append(f"speed_intel={speed_blend:.2f}")

        # ── Composite multiplicative score ───────────────────────
        composite = (
            quality
            * affinity
            * capability
            * health_s
            * variant_mult
            * provider_prior
            * coding_boost
        )

        # ── 6. UCB1 exploration bonus (additive) ─────────────────
        ucb = self._ucb_bonus(model_id, intent)
        if ucb > 0.5:
            reasons.append(f"ucb={ucb:.1f}")

        # ── 7. Thompson Sampling bonus (additive) ────────────────
        thompson = self._thompson_bonus(model_id, intent)
        if abs(thompson) > 0.5:
            reasons.append(f"thompson={thompson:+.1f}")

        # ── 8. Doc keyword bonus (small additive) ────────────────
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

    def _base_quality(self, mid_lower: str) -> float:
        """Look up benchmark quality from the tier table, fall back to param estimate."""
        for fam_re, size_re, quality in _QUALITY_COMPILED:
            if fam_re.search(mid_lower):
                if size_re is None:
                    return quality
                if size_re.search(mid_lower):
                    return quality
        # No tier match — estimate from parameter count
        m = PARAM_RE.search(mid_lower)
        if m:
            try:
                return _quality_from_params(int(m.group(1)))
            except ValueError:
                pass
        # Version / tier heuristic fallback
        vk = version_key(mid_lower)
        ver_score = vk[0][0] * 2.0 + vk[1] * 5.0
        return max(50.0, 55.0 + ver_score)

    def _intent_affinity(self, mid_lower: str, intent: str) -> float:
        """Multiplicative affinity — how well suited a model family is for this intent."""
        affinities = INTENT_AFFINITY.get(intent)
        if not affinities:
            return 1.0
        # Match against known family names
        for fam_key, aff_value in affinities.items():
            if fam_key in mid_lower:
                return aff_value
        return _DEFAULT_AFFINITY

    def _capability_score(
        self, model_id: str, mid_lower: str, intent: str
    ) -> float:
        """Capability gate: 0.0 blocks, 1.0 neutral, >1.0 confirmed bonus."""
        caps = self.capabilities.get(model_id) or {}

        if intent == "coding_agentic":
            if caps.get("supports_tools") is True:
                return 1.15  # confirmed: small bonus
            if caps.get("supports_tools") is False:
                return 0.1  # confirmed no tools: heavy penalty (not zero — may still work)

        if intent == "vision":
            if caps.get("supports_vision") is True:
                return 1.10
            if caps.get("supports_vision") is False:
                return 0.0

        return 1.0

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

    def _coding_elite_boost(self, model_id: str, mid_lower: str) -> float:
        """
        Strong multiplicative boost for proven coding models.

        Priority: OpenCode Zen free (mimo, deepseek-v4-flash-free, north-mini-code,
        big-pickle) > frontier coding (deepseek v4, kimi k2.6/k2.7, qwen3.5/3.6).
        """
        from nimmakai.catalog.presets import ZEN_FREE_CODING_MODELS
        from nimmakai.catalog.providers import split_provider_model

        pid, upstream = split_provider_model(
            model_id, self.provider_ids, default_provider="nim"
        )
        bare = upstream.lower().rsplit("/", 1)[-1]

        # Zen free / curated coding ids
        for zid in ZEN_FREE_CODING_MODELS:
            if zid in bare or bare == zid or bare.endswith(zid):
                if "free" in zid or zid == "big-pickle":
                    return 1.55 if pid == "zen" else 1.40
                return 1.35 if pid == "zen" else 1.28

        if pid == "zen":
            return 1.22  # any Zen model — curated for coding agents

        if re.search(r"mimo|deepseek.*v4|kimi.*k2|qwen3\.[56]|north.*code|big.pickle", mid_lower):
            return 1.25
        if re.search(r"coder|codestral|codellama|devstral", mid_lower):
            return 1.12
        # Embed/rerank already excluded; slight penalty for pure chat-tiny
        if re.search(r"nano|tiny|1b|2b|3b", mid_lower) and "nemotron" not in mid_lower:
            return 0.85
        return 1.0

    def _ucb_bonus(self, model_id: str, intent: str) -> float:
        """
        UCB1 exploration bonus: C × √(ln(N) / nₘ)

        Models with fewer samples get a larger bonus, encouraging exploration
        of potentially better but untested models.
        """
        total_n = self.learning.total_requests(intent)
        model_n = self.learning.model_requests(intent, model_id)

        if total_n < 2:
            return UCB_C * 2.0  # generous bonus when system is cold-starting

        if model_n == 0:
            # Never tried: give maximum exploration bonus
            return UCB_C * math.sqrt(math.log(total_n + 1))

        return UCB_C * math.sqrt(math.log(total_n + 1) / model_n)

    def _thompson_bonus(self, model_id: str, intent: str) -> float:
        """
        Thompson Sampling with production-stable damping.

        α = successes + 1 (optimistic prior)
        β = failures + 1

        Returns a bonus in [-10, +10]. Early on (few samples) we blend the
        posterior mean with a smaller random draw so ladders don't thrash
        randomly in production while still exploring.
        """
        alpha, beta = self.learning.thompson_params(intent, model_id)
        mean = alpha / (alpha + beta)
        sample = random.betavariate(alpha, beta)
        model_n = self.learning.model_requests(intent, model_id)
        # More samples → trust the draw; cold models → lean on mean
        weight = min(1.0, model_n / 12.0)
        blended = weight * sample + (1.0 - weight) * mean
        # Map [0, 1] → [-8, +8] (slightly tighter than before for stability)
        return (blended - 0.5) * 16.0

    def _doc_keyword_bonus(
        self, model_id: str, mid_lower: str, intent: str
    ) -> float:
        """Small additive bonus from doc description keyword matches."""
        doc = self._doc_for(model_id)
        if not doc:
            return 0.0
        desc = doc.description.lower()
        hits = sum(1 for kw in INTENT_KEYWORDS.get(intent, ()) if kw in desc or kw in mid_lower)
        bonus = hits * 2.0
        # Extra for explicit capability language
        if intent == "coding_agentic" and any(
            k in desc for k in ("tool", "function calling", "agent")
        ):
            bonus += 4.0
        return bonus

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
            if streak >= _MAX_HEAD_FAMILY_STREAK and streak_fam is not None:
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
