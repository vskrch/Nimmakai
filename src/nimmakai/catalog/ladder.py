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
    matches_family,
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
    # Qwen frontier
    (r"qwen.*3\.5", r"397b", 95.0),
    (r"qwen.*3\.5", r"235b", 91.0),
    (r"qwen.*3\.5", r"122b", 88.0),
    (r"qwen.*3\.5", r"72b", 84.0),
    (r"qwen.*3\.5", r"30b|32b", 76.0),
    (r"qwen.*3\.5", r"14b", 72.0),
    (r"qwen.*3\.5", r"7b|8b", 68.0),
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
    (r"glm.*5\.2", None, 87.0),
    (r"glm", None, 75.0),
    # Step
    (r"step.*3\.7", None, 83.0),
    (r"step", None, 72.0),
    # MiniMax
    (r"minimax.*m3", None, 81.0),
    (r"minimax", None, 72.0),
    # Google Gemma
    (r"gemma.*4", r"31b|27b", 74.0),
    (r"gemma.*4", r"12b", 68.0),
    (r"gemma", None, 65.0),
    # Llama
    (r"llama.*4", r"405b|400b", 88.0),
    (r"llama.*4", r"70b|72b", 80.0),
    (r"llama.*4", r"8b|7b", 68.0),
    (r"llama.*3", r"70b|72b", 78.0),
    (r"llama.*3", r"8b|7b", 64.0),
    (r"llama", None, 65.0),
    # DeepSeek
    (r"deepseek.*v3", None, 89.0),
    (r"deepseek.*r1", None, 91.0),
    (r"deepseek", None, 78.0),
    # Mistral
    (r"mistral.*large", None, 82.0),
    (r"mistral", None, 70.0),
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
        "qwen": 1.30,
        "glm": 1.15,
        "deepseek": 1.20,
        "nemotron": 1.10,
        "step": 1.05,
        "minimax": 1.00,
        "llama": 1.05,
        "gemma": 0.90,
        "mistral": 0.95,
    },
    "chat_fast": {
        "nemotron": 1.25,
        "glm": 1.10,
        "qwen": 1.05,
        "minimax": 1.05,
        "step": 1.00,
        "llama": 1.00,
        "deepseek": 1.00,
        "gemma": 0.95,
        "mistral": 1.00,
    },
    "reasoning": {
        "nemotron": 1.30,
        "deepseek": 1.25,
        "qwen": 1.15,
        "glm": 1.10,
        "step": 1.05,
        "llama": 1.00,
        "minimax": 0.95,
        "gemma": 0.90,
        "mistral": 0.95,
    },
    "long_horizon": {
        "qwen": 1.25,
        "nemotron": 1.15,
        "deepseek": 1.10,
        "glm": 1.10,
        "step": 1.05,
        "llama": 1.00,
        "minimax": 1.00,
        "gemma": 0.90,
        "mistral": 0.95,
    },
    "vision": {
        "qwen": 1.35,
        "minimax": 1.20,
        "llama": 1.10,
        "glm": 1.00,
        "gemma": 0.95,
        "nemotron": 0.50,  # most nemotrons are text-only
        "deepseek": 0.70,
        "step": 0.60,
        "mistral": 0.60,
    },
}

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

    Lifecycle: call `rebuild(live_ids, docs)` after each catalog refresh.
    Routing: call `ladder_for(intent)` to get best → fallback order.
    """

    def __init__(
        self,
        health: ModelHealthStore | None = None,
        learning: LearningStore | None = None,
    ) -> None:
        self.health = health or ModelHealthStore()
        self.learning = learning or LearningStore()
        self._ladders: dict[str, LadderSnapshot] = {}
        self._docs_by_slug: dict[str, DocModel] = {}
        self.live_ids: set[str] = set()
        # Capability hints learned from probes / docs: model_id → flags
        self.capabilities: dict[str, dict[str, bool]] = {}
        # Overridable from config/models.yaml
        self.provider_ids: set[str] = {"nim"}

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

    def rebuild(self, live_ids: set[str], *, intents: list[str] | None = None) -> None:
        self.live_ids = set(live_ids)
        targets = intents or [
            "coding_agentic",
            "chat_fast",
            "reasoning",
            "long_horizon",
            "vision",
            "embeddings",
        ]
        for intent in targets:
            self._ladders[intent] = self._build_ladder(intent)
        logger.info(
            "ladder rebuilt (%s intents, %s live): %s",
            len(targets),
            len(live_ids),
            {k: v.ladder[:3] for k, v in self._ladders.items()},
        )

    def ladder_for(self, intent: str, *, max_n: int | None = None) -> list[str]:
        """
        Best-first chain for the given intent.  Re-scores on each call so
        online learning (UCB1 + Thompson) applies immediately.
        """
        if self.live_ids:
            snap = self._build_ladder(intent)
            self._ladders[intent] = snap
        else:
            snap = self._ladders.get(intent)
            if snap is None:
                snap = self._build_ladder(intent)
                self._ladders[intent] = snap

        out: list[str] = []
        for mid in snap.ladder:
            if mid not in self.live_ids and self.live_ids:
                continue
            out.append(mid)
            if max_n is not None and len(out) >= max_n:
                break

        if not out and snap.ladder:
            out = list(snap.ladder[: max_n or len(snap.ladder)])
        return out

    # ------------------------------------------------------------------
    # Core scoring
    # ------------------------------------------------------------------

    def score_model(self, model_id: str, intent: str) -> ScoredModel:
        """Multi-criteria composite score for a model on a given intent."""
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

        # ── Composite multiplicative score ───────────────────────
        composite = quality * affinity * capability * health_s

        # ── 5. UCB1 exploration bonus (additive) ─────────────────
        ucb = self._ucb_bonus(model_id, intent)
        if ucb > 0.5:
            reasons.append(f"ucb={ucb:.1f}")

        # ── 6. Thompson Sampling bonus (additive) ────────────────
        thompson = self._thompson_bonus(model_id, intent)
        if abs(thompson) > 0.5:
            reasons.append(f"thompson={thompson:+.1f}")

        # ── 7. Doc keyword bonus (small additive) ────────────────
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
        Thompson Sampling: draw from Beta(α, β) distribution.

        α = successes + 1 (optimistic prior)
        β = failures + 1

        Returns a bonus in [-10, +10] that reflects the model's Bayesian
        quality estimate with natural exploration built in.
        """
        alpha, beta = self.learning.thompson_params(intent, model_id)
        sample = random.betavariate(alpha, beta)
        # Map [0, 1] → [-10, +10]
        return (sample - 0.5) * 20.0

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

    def _build_ladder(self, intent: str) -> LadderSnapshot:
        """Greedy best-first: score all live models, sort descending, take top K."""
        scored: list[ScoredModel] = []
        for mid in self.live_ids:
            s = self.score_model(mid, intent)
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

        ladder = [s.model_id for s in scored]
        scores = {s.model_id: round(s.score, 2) for s in scored}
        return LadderSnapshot(
            intent=intent,
            ladder=ladder,
            scores=scores,
            built_from_live=len(self.live_ids),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _doc_for(self, model_id: str) -> DocModel | None:
        slug = model_id.rsplit("/", 1)[-1].lower().replace("_", "-")
        return self._docs_by_slug.get(slug)

    def snapshot(self) -> dict:
        return {
            intent: {
                "ladder_head": snap.ladder[:5],
                "ladder_len": len(snap.ladder),
                "scores_head": {m: snap.scores.get(m) for m in snap.ladder[:5]},
                "built_from_live": snap.built_from_live,
            }
            for intent, snap in self._ladders.items()
        }

    def set_capability(self, model_id: str, **flags: bool) -> None:
        cur = self.capabilities.setdefault(model_id, {})
        cur.update(flags)
