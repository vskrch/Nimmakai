from __future__ import annotations

import pytest
from nimmakai.routing.rl_rewards import calculate_composite_reward


def test_reward_503_error():
    r = calculate_composite_reward(success=False, status_code=503)
    assert r == -1.0


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
