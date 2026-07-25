"""LinUCB Contextual Bandit Engine for adaptive routing.

Implements online ridge regression with Sherman-Morrison rank-1 matrix inverse updates
for O(d^2) per-request computation. Provides real-time Upper Confidence Bound (UCB)
scores based on prompt feature vectors and live execution feedback.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from potato.routing.rl_features import FEATURE_DIM, FEATURE_NAMES

logger = logging.getLogger(__name__)


@dataclass
class ModelLinUCBState:
    model_id: str
    # 12x12 inverse covariance matrix (initialized to (1/lambda) * I_d)
    a_inv: list[list[float]] = field(default_factory=lambda: [
        [1.0 if i == j else 0.0 for j in range(FEATURE_DIM)] for i in range(FEATURE_DIM)
    ])
    # 12-D reward vector
    b: list[float] = field(default_factory=lambda: [0.0] * FEATURE_DIM)
    # 12-D parameter vector (theta = A_inv * b)
    theta: list[float] = field(default_factory=lambda: [0.0] * FEATURE_DIM)
    request_count: int = 0
    total_reward: float = 0.0
    last_updated: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "a_inv": self.a_inv,
            "b": self.b,
            "theta": self.theta,
            "request_count": self.request_count,
            "total_reward": self.total_reward,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelLinUCBState:
        state = cls(model_id=str(data.get("model_id") or ""))
        if isinstance(data.get("a_inv"), list) and len(data["a_inv"]) == FEATURE_DIM:
            state.a_inv = data["a_inv"]
        if isinstance(data.get("b"), list) and len(data["b"]) == FEATURE_DIM:
            state.b = data["b"]
        if isinstance(data.get("theta"), list) and len(data["theta"]) == FEATURE_DIM:
            state.theta = data["theta"]
        state.request_count = int(data.get("request_count") or 0)
        state.total_reward = float(data.get("total_reward") or 0.0)
        state.last_updated = float(data.get("last_updated") or 0.0)
        return state


class LinUCBPolicyEngine:
    """
    Thread-safe Contextual Bandit engine using LinUCB with Sherman-Morrison updates.
    """

    def __init__(self, ridge_lambda: float = 1.0, default_alpha: float = 0.8) -> None:
        self.ridge_lambda = ridge_lambda
        self.default_alpha = default_alpha
        self._states: dict[str, ModelLinUCBState] = {}
        self._lock = threading.Lock()
        self._dirty = False

    def get_state(self, model_id: str) -> ModelLinUCBState:
        with self._lock:
            if model_id not in self._states:
                # Initialize A_inv with (1.0 / lambda) on diagonal
                init_val = 1.0 / max(1e-4, self.ridge_lambda)
                state = ModelLinUCBState(
                    model_id=model_id,
                    a_inv=[[init_val if i == j else 0.0 for j in range(FEATURE_DIM)] for i in range(FEATURE_DIM)],
                )
                self._states[model_id] = state
            return self._states[model_id]

    def score(
        self,
        model_id: str,
        x: list[float],
        alpha: float | None = None,
    ) -> tuple[float, float, float]:
        """
        Calculate contextual score for model_id given feature vector x.
        Returns: (ucb_score, expected_reward, exploration_bonus)
        """
        if len(x) != FEATURE_DIM:
            return (0.0, 0.0, 0.0)

        a_val = alpha if alpha is not None else self.default_alpha
        state = self.get_state(model_id)

        with self._lock:
            # Expected reward: hat_r = theta^T * x
            hat_r = sum(state.theta[i] * x[i] for i in range(FEATURE_DIM))

            # Variance estimation: v = A_inv * x
            v = [0.0] * FEATURE_DIM
            for i in range(FEATURE_DIM):
                v[i] = sum(state.a_inv[i][j] * x[j] for j in range(FEATURE_DIM))

            # var = x^T * v = x^T * A_inv * x
            var = sum(x[i] * v[i] for i in range(FEATURE_DIM))
            ucb_bonus = a_val * math.sqrt(max(0.0, var))

        score = hat_r + ucb_bonus
        return (score, hat_r, ucb_bonus)

    def record_feedback(self, model_id: str, x: list[float], reward: float) -> None:
        """
        Online update of LinUCB model parameters using Sherman-Morrison formula.
        O(d^2) complexity per update (~144 floating point ops for d=12).
        """
        if len(x) != FEATURE_DIM:
            return

        # Bound reward to prevent numerical explosion
        r = max(-2.0, min(2.0, float(reward)))

        with self._lock:
            if model_id not in self._states:
                init_val = 1.0 / max(1e-4, self.ridge_lambda)
                self._states[model_id] = ModelLinUCBState(
                    model_id=model_id,
                    a_inv=[[init_val if i == j else 0.0 for j in range(FEATURE_DIM)] for i in range(FEATURE_DIM)],
                )
            state = self._states[model_id]

            # 1. Update reward vector b: b_new = b + r * x
            for i in range(FEATURE_DIM):
                state.b[i] += r * x[i]

            # 2. Compute v = A_inv * x
            v = [0.0] * FEATURE_DIM
            for i in range(FEATURE_DIM):
                v[i] = sum(state.a_inv[i][j] * x[j] for j in range(FEATURE_DIM))

            # 3. Denominator: denom = 1.0 + x^T * v
            denom = 1.0 + sum(x[i] * v[i] for i in range(FEATURE_DIM))

            # 4. Sherman-Morrison update: A_inv_new = A_inv - (v * v^T) / denom
            if denom > 1e-12:
                for i in range(FEATURE_DIM):
                    for j in range(FEATURE_DIM):
                        state.a_inv[i][j] -= (v[i] * v[j]) / denom

            # 5. Recompute theta: theta = A_inv * b
            for i in range(FEATURE_DIM):
                state.theta[i] = sum(state.a_inv[i][j] * state.b[j] for j in range(FEATURE_DIM))

            state.request_count += 1
            state.total_reward += r
            state.last_updated = time.time()
            self._dirty = True

    def get_all_stats(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "model_id": s.model_id,
                    "request_count": s.request_count,
                    "avg_reward": round(s.total_reward / max(1, s.request_count), 3),
                    "total_reward": round(s.total_reward, 2),
                    "theta": [round(val, 3) for val in s.theta],
                    "last_updated": s.last_updated,
                }
                for s in sorted(self._states.values(), key=lambda k: k.request_count, reverse=True)
            ]

    def reset_model(self, model_id: str) -> bool:
        with self._lock:
            if model_id in self._states:
                del self._states[model_id]
                self._dirty = True
                return True
            return False

    def reset_all(self) -> None:
        with self._lock:
            self._states.clear()
            self._dirty = True
