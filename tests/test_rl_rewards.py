from __future__ import annotations

from potato.routing.rl_rewards import calculate_composite_reward


def test_reward_503_error():
    # 503/504 are transient gateway unavailability — less punitive than hard
    # server errors so the bandit can learn provider rate-limit patterns.
    r = calculate_composite_reward(success=False, status_code=503)
    assert r == -0.8
    assert calculate_composite_reward(success=False, status_code=504) == -0.8


def test_reward_429_less_punitive_than_500():
    # 429 (rate-limit) must be less punitive than 500 (server fault) so the
    # bandit can distinguish transient capacity from a broken provider.
    r_429 = calculate_composite_reward(success=False, status_code=429)
    r_500 = calculate_composite_reward(success=False, status_code=500)
    assert r_429 == -0.5
    assert r_500 == -0.9
    assert r_429 > r_500


def test_reward_empty_reply():
    r = calculate_composite_reward(success=True, status_code=200, empty_reply=True)
    assert r == -0.8


def test_reward_fast_success_with_tools():
    r = calculate_composite_reward(
        success=True,
        status_code=200,
        ttfb_seconds=0.2,
        target_ttfb_seconds=0.5,
        tool_ok=True,
    )
    assert r > 0.8  # fast + tools ok -> high reward
    assert r <= 1.0


def test_reward_malformed_tool_syntax():
    r = calculate_composite_reward(
        success=True,
        status_code=200,
        ttfb_seconds=0.4,
        tool_ok=False,
    )
    assert r < 0.2  # penalized heavily for malformed json


def test_reward_immediate_retry():
    r = calculate_composite_reward(
        success=True,
        status_code=200,
        is_immediate_retry=True,
    )
    assert r < 0.5
