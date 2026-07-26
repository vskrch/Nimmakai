from __future__ import annotations

from potato.routing.rl_engine import LinUCBPolicyEngine
from potato.routing.rl_features import FEATURE_DIM


def test_linucb_init_and_score():
    engine = LinUCBPolicyEngine(ridge_lambda=1.0, default_alpha=1.0)
    x = [0.5] * FEATURE_DIM

    score, hat_r, ucb = engine.score("test/model-1", x)
    assert hat_r == 0.0  # initial theta is all 0
    assert ucb > 0.0  # ucb exploration bonus positive
    assert score == hat_r + ucb


def test_linucb_sherman_morrison_learning():
    engine = LinUCBPolicyEngine(ridge_lambda=1.0, default_alpha=0.5)

    # Feature vector representing python coding task (feature index 3 = 1.0)
    x_py = [0.0] * FEATURE_DIM
    x_py[3] = 1.0
    x_py[2] = 1.0  # code syntax ratio

    # Initial score
    score_before, hat_r_before, ucb_before = engine.score("test/coder", x_py)

    # Record 10 positive rewards (+1.0) for test/coder on python tasks
    for _ in range(10):
        engine.record_feedback("test/coder", x_py, reward=1.0)

    score_after, hat_r_after, ucb_after = engine.score("test/coder", x_py)

    # Expected reward (hat_r) must increase significantly
    assert hat_r_after > hat_r_before
    # Exploration uncertainty (ucb) must decrease as we gain confidence
    assert ucb_after < ucb_before
    # Overall score must reflect positive adaptation
    assert score_after > score_before


def test_linucb_stats_and_reset():
    engine = LinUCBPolicyEngine()
    x = [0.1] * FEATURE_DIM
    engine.record_feedback("model-a", x, 1.0)
    engine.record_feedback("model-b", x, -0.5)

    stats = engine.get_all_stats()
    assert len(stats) == 2
    assert stats[0]["model_id"] == "model-a"
    assert stats[0]["avg_reward"] == 1.0

    engine.reset_model("model-a")
    assert len(engine.get_all_stats()) == 1

    engine.reset_all()
    assert len(engine.get_all_stats()) == 0
