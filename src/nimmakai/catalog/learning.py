"""Online learning from routing outcomes — adjusts ladder scores over time."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ModelLearningStats:
    successes: int = 0
    failures: int = 0
    empty_replies: int = 0
    tool_ok: int = 0
    tool_fail: int = 0
    unavailable: int = 0
    # EWMA quality in [-1, 1]: + good, - bad
    ewma_quality: float = 0.0
    last_updated: float = 0.0

    def score_delta(self) -> float:
        """
        Soft adjustment applied on top of static power score.
        Caps keep primary-family power-first unless the model is clearly failing.
        """
        total = self.successes + self.failures
        delta = self.ewma_quality * 12.0
        if self.unavailable >= 2:
            delta -= 25.0
        if self.tool_fail > self.tool_ok and self.tool_fail >= 2:
            delta -= 15.0
        if self.empty_replies >= 3:
            delta -= 10.0
        if total >= 5 and self.successes / max(total, 1) > 0.9:
            delta += 5.0
        return max(-40.0, min(25.0, delta))


@dataclass
class LearningStore:
    """Persisted per-(intent, model) learning signals."""

    path: Path = field(default_factory=lambda: Path(".nimmakai/learning.json"))
    save_debounce_seconds: float = 10.0
    _data: dict[str, dict[str, ModelLearningStats]] = field(default_factory=dict)
    _dirty: bool = False
    _last_save_at: float = 0.0

    def _key(self, intent: str, model_id: str) -> tuple[str, str]:
        return intent, model_id

    def stats(self, intent: str, model_id: str) -> ModelLearningStats:
        bucket = self._data.setdefault(intent, {})
        if model_id not in bucket:
            bucket[model_id] = ModelLearningStats()
        return bucket[model_id]

    def record(
        self,
        *,
        intent: str,
        model_id: str,
        success: bool,
        unavailable: bool = False,
        empty_reply: bool = False,
        had_tools: bool = False,
        tool_ok: bool | None = None,
    ) -> None:
        s = self.stats(intent, model_id)
        s.last_updated = time.time()
        if unavailable:
            s.unavailable += 1
            s.failures += 1
            s.ewma_quality = 0.7 * s.ewma_quality + 0.3 * (-1.0)
        elif success:
            s.successes += 1
            q = 1.0
            if empty_reply:
                s.empty_replies += 1
                q = -0.3
            if had_tools:
                if tool_ok:
                    s.tool_ok += 1
                    q = 1.0
                elif tool_ok is False:
                    s.tool_fail += 1
                    q = -0.8
            s.ewma_quality = 0.7 * s.ewma_quality + 0.3 * q
        else:
            s.failures += 1
            s.ewma_quality = 0.7 * s.ewma_quality + 0.3 * (-1.0)
        self._dirty = True
        self.save_if_due()

    def score_delta(self, intent: str, model_id: str) -> float:
        if intent not in self._data or model_id not in self._data[intent]:
            return 0.0
        return self._data[intent][model_id].score_delta()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for intent, models in (raw.get("intents") or {}).items():
                for mid, d in models.items():
                    st = ModelLearningStats(
                        successes=int(d.get("successes", 0)),
                        failures=int(d.get("failures", 0)),
                        empty_replies=int(d.get("empty_replies", 0)),
                        tool_ok=int(d.get("tool_ok", 0)),
                        tool_fail=int(d.get("tool_fail", 0)),
                        unavailable=int(d.get("unavailable", 0)),
                        ewma_quality=float(d.get("ewma_quality", 0.0)),
                        last_updated=float(d.get("last_updated", 0.0)),
                    )
                    self._data.setdefault(intent, {})[mid] = st
            logger.info("loaded learning store (%s intents)", len(self._data))
        except Exception:
            logger.exception("failed to load learning store")

    def save_if_due(self, *, force: bool = False) -> None:
        if not self._dirty and not force:
            return
        now = time.time()
        if (
            not force
            and self._last_save_at
            and now - self._last_save_at < self.save_debounce_seconds
        ):
            return
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": time.time(),
            "intents": {
                intent: {
                    mid: {
                        "successes": st.successes,
                        "failures": st.failures,
                        "empty_replies": st.empty_replies,
                        "tool_ok": st.tool_ok,
                        "tool_fail": st.tool_fail,
                        "unavailable": st.unavailable,
                        "ewma_quality": round(st.ewma_quality, 4),
                        "last_updated": st.last_updated,
                        "score_delta": round(st.score_delta(), 2),
                    }
                    for mid, st in models.items()
                }
                for intent, models in self._data.items()
            },
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = False
        self._last_save_at = time.time()

    def snapshot(self) -> dict:
        return {
            intent: {
                mid: {
                    "score_delta": round(st.score_delta(), 2),
                    "ewma_quality": round(st.ewma_quality, 3),
                    "successes": st.successes,
                    "failures": st.failures,
                }
                for mid, st in models.items()
            }
            for intent, models in self._data.items()
        }
