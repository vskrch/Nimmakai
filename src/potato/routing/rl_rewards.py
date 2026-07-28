"""Multi-signal reward calculation for LinUCB Reinforcement Learning.

Computes a scalar reward R in [-1.0, +1.0] from real-time execution feedback:
- HTTP status codes (200 vs 429 vs 503/5xx)
- Time-to-first-byte (TTFB) latency against baseline targets
- Function/tool calling syntax validity (JSON correctness)
- Immediate client retries / disconnects
"""

from __future__ import annotations


def calculate_composite_reward(
    *,
    success: bool,
    status_code: int = 200,
    ttfb_seconds: float | None = None,
    target_ttfb_seconds: float = 0.5,
    tool_ok: bool | None = None,
    empty_reply: bool = False,
    is_immediate_retry: bool = False,
) -> float:
    """
    Calculate normalized scalar reward R in [-1.0, +1.0].

    Failure granularity (so the bandit can distinguish transient rate-limits
    from hard upstream failures):
      - 429 (rate-limit)        -> -0.5  (transient; provider may be fine next request)
      - 503/504 (gateway)       -> -0.8  (upstream temporarily unavailable)
      - 500/502 (server error)  -> -0.9  (provider-side fault)
      - other 4xx               -> -0.7  (client-side; usually not the model's fault)
    """
    if not success or status_code >= 400:
        if status_code == 429:
            return -0.5
        if status_code in (503, 504):
            return -0.8
        if status_code in (500, 502):
            return -0.9
        return -0.7

    if empty_reply:
        return -0.8

    # Base success reward
    reward = 0.6

    # 1. TTFB latency bonus / penalty
    if ttfb_seconds is not None and ttfb_seconds > 0:
        if ttfb_seconds <= target_ttfb_seconds:
            # Sub-target speed bonus (up to +0.25)
            speed_ratio = (target_ttfb_seconds - ttfb_seconds) / max(0.1, target_ttfb_seconds)
            reward += min(0.25, 0.25 * speed_ratio)
        else:
            # Slow TTFB penalty (down to -0.3)
            slow_ratio = (ttfb_seconds - target_ttfb_seconds) / max(0.1, target_ttfb_seconds)
            reward -= min(0.3, 0.15 * slow_ratio)

    # 2. Tool calling execution validity
    if tool_ok is True:
        reward += 0.2
    elif tool_ok is False:
        # Malformed tool syntax is a critical failure for agentic workflows
        reward -= 0.6

    # 3. Client immediate retry penalty (indicates user dissatisfaction / loop)
    if is_immediate_retry:
        reward -= 0.4

    return max(-1.0, min(1.0, round(reward, 3)))


def _demo() -> None:
    """Self-check: reward invariants hold across the failure taxonomy."""
    assert calculate_composite_reward(success=False, status_code=429) == -0.5
    assert calculate_composite_reward(success=False, status_code=503) == -0.8
    assert calculate_composite_reward(success=False, status_code=504) == -0.8
    assert calculate_composite_reward(success=False, status_code=500) == -0.9
    assert calculate_composite_reward(success=False, status_code=400) == -0.7
    assert calculate_composite_reward(success=True, status_code=200, empty_reply=True) == -0.8
    r_fast = calculate_composite_reward(
        success=True, status_code=200, ttfb_seconds=0.2, target_ttfb_seconds=0.5, tool_ok=True
    )
    assert 0.8 < r_fast <= 1.0, r_fast
    r_retry = calculate_composite_reward(success=True, status_code=200, is_immediate_retry=True)
    assert r_retry < 0.5, r_retry
    assert calculate_composite_reward(success=False, status_code=429) > calculate_composite_reward(
        success=False, status_code=500
    ), "429 must be less punitive than 500 so the bandit can learn provider rate-limit patterns"
    print("rl_rewards OK")


if __name__ == "__main__":
    _demo()
