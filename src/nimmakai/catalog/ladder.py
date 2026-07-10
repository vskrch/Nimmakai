"""
Intelligent model ladder service.

Automatically builds strength-ordered ladders from whatever NVIDIA models are
live *right now*, scored for the task/intent. The proxy always tries the
strongest available head first and walks down only on unavailability/errors.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

# Intent → hard primary family (must lead ladder when any member is live)
INTENT_PRIMARY_FAMILY: dict[str, str] = {
    "coding_agentic": "qwen",
    "chat_fast": "nemotron",
    "reasoning": "nemotron",
    "long_horizon": "qwen",
    "vision": "qwen",
    "embeddings": "nemotron",
}

# Rough parameter-size extraction: 397b, 120b, 70b, 30b-a3b, etc.
PARAM_RE = re.compile(r"(?:^|[^a-z0-9])(\d{1,4})b(?:[^a-z0-9]|$)", re.I)

# Intent keyword boosts from NVIDIA doc descriptions / model ids
INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "coding_agentic": (
        "coding",
        "code",
        "agent",
        "agentic",
        "tool",
        "function calling",
        "software",
    ),
    "chat_fast": ("chat", "instruct", "general", "assistant", "conversation"),
    "reasoning": ("reason", "math", "logic", "thinking"),
    "long_horizon": ("long", "planning", "agentic", "1m", "context"),
    "vision": ("vision", "vlm", "multimodal", "image", "visual"),
    "embeddings": ("embed", "retrieval", "rerank"),
}

# Family affinity: which families are "base" strongest for an intent
INTENT_FAMILY_BOOST: dict[str, dict[str, float]] = {
    "coding_agentic": {
        "qwen": 40.0,
        "glm_5_2": 25.0,
        "step_3_7": 20.0,
        "minimax_m3": 15.0,
        "nemotron": 10.0,
    },
    "chat_fast": {
        "nemotron": 40.0,
        "glm_5_2": 20.0,
        "step_3_7": 15.0,
        "minimax_m3": 12.0,
        "qwen": 10.0,
    },
    "reasoning": {
        "nemotron": 35.0,
        "qwen": 25.0,
        "glm_5_2": 20.0,
        "step_3_7": 18.0,
        "minimax_m3": 10.0,
    },
    "long_horizon": {
        "qwen": 35.0,
        "nemotron": 25.0,
        "glm_5_2": 22.0,
        "step_3_7": 18.0,
        "minimax_m3": 12.0,
    },
    "vision": {
        "qwen": 40.0,
        "minimax_m3": 25.0,
        "nemotron": 5.0,
    },
}

CHAT_EXCLUDE = re.compile(
    r"(embed|rerank|ocr|asr|safety|guard|tts|image-edit|diffusion|"
    r"protein|fold|yolo|page-elements|table-structure|voicechat)",
    re.I,
)


@dataclass
class ScoredModel:
    model_id: str
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class LadderSnapshot:
    intent: str
    ladder: list[str]
    scores: dict[str, float]
    built_from_live: int


class LadderService:
    """
    In-process intelligent laddering service.

    Lifecycle: call `rebuild(live_ids, docs)` after each catalog refresh.
    Routing: call `ladder_for(intent)` to get strongest → next available order.
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
            "ladder service rebuilt for %s intents from %s live models: %s",
            len(targets),
            len(live_ids),
            {k: v.ladder[:3] for k, v in self._ladders.items()},
        )

    def ladder_for(self, intent: str, *, max_n: int | None = None) -> list[str]:
        """
        Strength-ordered available models for intent.
        Re-scores on each call so online learning applies immediately.
        Unhealthy / cooldown models are skipped (walk to next strongest).
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
            if self.health.is_unhealthy(mid):
                continue
            if mid not in self.live_ids and self.live_ids:
                continue
            out.append(mid)
            if max_n is not None and len(out) >= max_n:
                break

        if not out and snap.ladder:
            out = list(snap.ladder[: max_n or len(snap.ladder)])
        return out

    def score_model(self, model_id: str, intent: str) -> ScoredModel:
        mid = model_id.lower()
        reasons: list[str] = []
        score = 0.0

        # Modality gates
        if intent == "embeddings":
            if "embed" not in mid and "retrieval" not in mid:
                return ScoredModel(model_id, -1e9, ["not_embedding"])
            score += 50.0
            reasons.append("embedding")
        elif intent == "vision":
            if not any(k in mid for k in ("vl", "vision", "omni", "minimax-m3")):
                # Allow models with vision in docs
                doc = self._doc_for(model_id)
                desc = (doc.description if doc else "").lower()
                if "vision" not in desc and "vlm" not in desc and "multimodal" not in desc:
                    return ScoredModel(model_id, -1e9, ["not_vision"])
            score += 20.0
            reasons.append("vision_capable")
        else:
            # Generic chat/coding — exclude non-LLM endpoints
            if CHAT_EXCLUDE.search(mid):
                return ScoredModel(model_id, -1e9, ["excluded_modality"])
            if intent == "coding_agentic" and QWEN_EXCLUDE.search(mid) and "qwen" in mid:
                return ScoredModel(model_id, -1e9, ["qwen_non_text"])
            if "nemotron" in mid and NEMOTRON_EXCLUDE.search(mid):
                return ScoredModel(model_id, -1e9, ["nemotron_non_chat"])

        # Parameter size → power proxy
        params = self._param_billions(mid)
        if params:
            score += min(params, 600) * 0.08  # 397b ≈ 31.8 pts
            reasons.append(f"params={params}b")

        # Version / tier (not param size — that is scored separately)
        vk = version_key(model_id)
        ver_tuple = vk[0]
        # Dotted versions like 3.5 beat bare 3
        ver_score = ver_tuple[0] * 3.0
        if len(ver_tuple) > 1:
            ver_score += ver_tuple[1] * 0.5
        score += ver_score
        score += vk[1] * 8.0  # ultra/super/pro tier
        if vk[1]:
            reasons.append(f"tier={vk[1]}")

        # Family affinity for this intent
        boosts = INTENT_FAMILY_BOOST.get(intent, {})
        for fam, boost in boosts.items():
            if matches_family(model_id, fam):
                score += boost
                reasons.append(f"family={fam}+{boost}")
                break

        # Doc description keyword match
        doc = self._doc_for(model_id)
        if doc:
            desc = doc.description.lower()
            hits = 0
            for kw in INTENT_KEYWORDS.get(intent, ()):
                if kw in desc or kw in mid:
                    hits += 1
            if hits:
                score += hits * 4.0
                reasons.append(f"doc_keywords={hits}")
            # Explicit capability language in NVIDIA docs
            if intent == "coding_agentic" and (
                "tool" in desc or "function calling" in desc or "agent" in desc
            ):
                score += 8.0
                reasons.append("doc_tools_agent")

        # Learned online adjustments (failures, empty replies, tool quality)
        learned = self.learning.score_delta(intent, model_id)
        if learned:
            score += learned
            reasons.append(f"learned={learned:+.1f}")

        # Capability registry (from probes)
        caps = self.capabilities.get(model_id) or {}
        if intent == "coding_agentic":
            if caps.get("supports_tools") is True:
                score += 10.0
                reasons.append("tools_confirmed")
            elif caps.get("supports_tools") is False:
                score -= 20.0
                reasons.append("tools_unsupported")

        return ScoredModel(model_id=model_id, score=score, reasons=reasons)

    def _build_ladder(self, intent: str) -> LadderSnapshot:
        scored: list[ScoredModel] = []
        for mid in self.live_ids:
            s = self.score_model(mid, intent)
            if s.score > -1e8:
                scored.append(s)
        scored.sort(key=lambda s: (s.score, version_key(s.model_id)), reverse=True)

        # Hard pin: strongest member of the intent's primary family leads,
        # then the rest of the strength-ordered ladder (no duplicates).
        primary_fam = INTENT_PRIMARY_FAMILY.get(intent)
        ladder: list[str] = []
        if primary_fam:
            primary_candidates = [
                s for s in scored if matches_family(s.model_id, primary_fam)
            ]
            if primary_candidates:
                ladder.append(primary_candidates[0].model_id)
        for s in scored:
            if s.model_id not in ladder:
                ladder.append(s.model_id)

        scores = {s.model_id: round(s.score, 2) for s in scored}
        return LadderSnapshot(
            intent=intent,
            ladder=ladder,
            scores=scores,
            built_from_live=len(self.live_ids),
        )

    def _doc_for(self, model_id: str) -> DocModel | None:
        slug = model_id.split("/", 1)[-1].lower().replace("_", "-")
        return self._docs_by_slug.get(slug)

    @staticmethod
    def _param_billions(model_id: str) -> int | None:
        m = PARAM_RE.search(model_id)
        if not m:
            return None
        try:
            return int(m.group(1))
        except ValueError:
            return None

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
