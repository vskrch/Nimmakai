"""Per-model cost estimation (USD per 1M tokens).

Uses models.dev as the primary source for pricing data, with a hardcoded
fallback for offline/error scenarios.
"""

from __future__ import annotations

import re
from typing import Any

from nimmakai.analytics.models_cost import all_dynamic_rates, lookup_dynamic

# Hardcoded fallback rates (input_cost_per_M, output_cost_per_M).
# Used when models.dev is unreachable or a model isn't listed there.
MODEL_COST_PER_M: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-haiku": (0.25, 1.25),
    "deepseek-r1": (0.55, 2.19),
    "deepseek-v3": (0.27, 1.10),
    "deepseek-chat": (0.27, 1.10),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "qwen3": (0.30, 0.90),
    "llama-3.3-70b": (0.59, 0.79),
}

# Models / providers treated as free-tier by default
_FREE_PATTERNS = (
    re.compile(r"-free$", re.I),
    re.compile(r"/free$", re.I),
    re.compile(r"groq/", re.I),
    re.compile(r"cerebras/", re.I),
    re.compile(r"mimo", re.I),
    re.compile(r"opencode", re.I),
    re.compile(r"zen/", re.I),
)


def _normalize_model(model_id: str) -> str:
    mid = (model_id or "").strip().lower()
    if "/" in mid:
        mid = mid.split("/", 1)[-1]
    return mid


def lookup_rates(
    model_id: str,
    overrides: dict[str, tuple[float, float]] | None = None,
) -> tuple[float, float]:
    """Return (input_per_M, output_per_M) for a model.

    Resolution order:
    1. Explicit overrides (from admin API or config)
    2. Free-tier patterns (groq, cerebras, etc.)
    3. Dynamic pricing from models.dev (exact namespaced ID match)
    4. Hardcoded fallback rates (exact then fuzzy match)
    5. (0.0, 0.0) for unknown models
    """
    if overrides and model_id in overrides:
        return overrides[model_id]
    raw = (model_id or "").strip().lower()
    if overrides and raw in overrides:
        return overrides[raw]

    for pat in _FREE_PATTERNS:
        if pat.search(raw):
            return (0.0, 0.0)

    # Try dynamic pricing from models.dev (uses full namespaced ID)
    dyn = lookup_dynamic(raw)
    if dyn is not None:
        return dyn

    # Fallback to hardcoded rates
    mid = _normalize_model(raw)
    if mid in MODEL_COST_PER_M:
        return MODEL_COST_PER_M[mid]

    # Fuzzy: longest prefix match on known keys
    best: tuple[float, float] | None = None
    best_len = 0
    for key, rates in MODEL_COST_PER_M.items():
        if (mid.startswith(key) or key in mid) and len(key) > best_len:
            best = rates
            best_len = len(key)
    if best is not None:
        return best

    # Unknown paid-looking models: conservative $0 (self-hosted / free NIM)
    return (0.0, 0.0)


def estimate_cost(
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    overrides: dict[str, tuple[float, float]] | None = None,
) -> float:
    """Estimated cost in USD for a single request."""
    inp, out = lookup_rates(model_id, overrides)
    pt = max(0, int(prompt_tokens or 0))
    ct = max(0, int(completion_tokens or 0))
    return (pt / 1_000_000.0) * inp + (ct / 1_000_000.0) * out


def list_default_rates() -> list[dict[str, Any]]:
    """Return combined rates: dynamic (models.dev) merged with hardcoded fallback."""
    dyn = all_dynamic_rates()
    combined: dict[str, tuple[float, float]] = dict(MODEL_COST_PER_M)
    combined.update(dyn)
    return [
        {"model_id": k, "input_per_m": v[0], "output_per_m": v[1]}
        for k, v in sorted(combined.items())
    ]
