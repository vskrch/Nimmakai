"""OpenRouter / Kilo-style auto-router surface.

Parity goals (client-visible):
- Accept ``openrouter/auto``, ``kilo/auto``, ``kilo-auto/*`` like those products
- Analyze prompt/intent → pick from a curated live pool
- Response ``model`` field is the *actual* upstream model (handled in compat)
- Session stickiness pins the chosen model for multi-turn chats
- Optional ``plugins`` block: allowed_models + cost_quality_tradeoff (OpenRouter)
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from nimmakai.catalog.aliases import normalize_model_name

# Virtual auto-router ids (OpenRouter / Kilo / Nimmakai)
AutoTier = Literal[
    "balanced",  # quality × speed (default openrouter/auto)
    "frontier",  # max capability (kilo-auto/frontier, nimmakai/best)
    "efficient",  # cost-aware / cheapest capable (kilo-auto/efficient, auto-cheap)
    "fast",  # latency first (nimmakai/auto-fast)
    "free",  # free-only pool (kilo-auto/free)
    "coding",  # force coding ladder (nimmakai/auto-coding)
]

# Map client model string → auto tier (or None if not an auto router)
_AUTO_ALIAS: dict[str, AutoTier] = {
    # Nimmakai
    "auto": "balanced",
    "nimmakai/auto": "balanced",
    "nimmakai/auto-coding": "coding",
    "nimmakai/best": "coding",
    "nimmakai/coding": "coding",
    "best": "coding",
    "coding": "coding",
    "auto-coding": "coding",
    "nimmakai/auto-fast": "fast",
    "auto-fast": "fast",
    "nimmakai/auto-cheap": "efficient",
    "auto-cheap": "efficient",
    # OpenRouter
    "openrouter/auto": "balanced",
    "openrouter/auto-router": "balanced",
    # Kilo (legacy + current)
    "kilo/auto": "balanced",
    "kilo/auto-free": "free",
    "kilo-auto": "balanced",
    "kilo-auto/frontier": "frontier",
    "kilo-auto/balanced": "balanced",
    "kilo-auto/efficient": "efficient",
    "kilo-auto/free": "free",
}

# Free-ish provider prefixes (best-effort; free filter also matches *free* in id)
_FREE_PROVIDER_PREFIXES = (
    "zen/",
    "groq/",
    "cerebras/",
    "together/",
    "fireworks/",
    "openrouter/",  # often free models if user uses free keys
)

_FREE_ID_RE = re.compile(r"(^|[-_/])free($|[-_/])|:free$|/free$", re.I)


@dataclass
class AutoRouterOptions:
    """Parsed OpenRouter-style auto-router controls from the request body."""

    tier: AutoTier | None = None
    is_auto: bool = False
    allowed_models: list[str] = field(default_factory=list)
    cost_quality_tradeoff: int | None = None  # 0=quality … 10=cost
    session_id: str | None = None
    models_fallback: list[str] = field(default_factory=list)  # OpenRouter models[]


def is_auto_router_id(model: str | None) -> bool:
    if model is None or model == "":
        return True  # empty → auto
    return normalize_model_name(model) in _AUTO_ALIAS


def resolve_auto_tier(model: str | None) -> AutoTier | None:
    if model is None or str(model).strip() == "":
        return "balanced"
    return _AUTO_ALIAS.get(normalize_model_name(model))


def tradeoff_to_tier(tradeoff: int) -> AutoTier:
    """Map OpenRouter cost_quality_tradeoff (0–10) onto our tiers."""
    t = max(0, min(10, int(tradeoff)))
    if t <= 2:
        return "frontier"
    if t <= 5:
        return "balanced"
    if t <= 7:
        return "efficient"
    return "efficient"


def tier_to_variant(tier: AutoTier) -> str:
    if tier in ("efficient", "free"):
        return "cheap"
    if tier == "fast":
        return "fast"
    # frontier / balanced / coding → quality+speed default
    return "default"


def all_auto_router_ids() -> list[str]:
    """Stable list for /v1/models injection (primary + compatibility aliases)."""
    preferred = [
        "nimmakai/auto",
        "nimmakai/auto-coding",
        "nimmakai/best",
        "nimmakai/auto-fast",
        "nimmakai/auto-cheap",
        "openrouter/auto",
        "kilo/auto",
        "kilo-auto/frontier",
        "kilo-auto/balanced",
        "kilo-auto/efficient",
        "kilo-auto/free",
        "auto",
    ]
    # Dedup while keeping order
    seen: set[str] = set()
    out: list[str] = []
    for mid in preferred:
        n = normalize_model_name(mid)
        if n not in seen:
            seen.add(n)
            out.append(mid)
    return out


def parse_auto_router_options(body: dict[str, Any] | None) -> AutoRouterOptions:
    """Extract session_id, plugins.auto-router, and models[] fallback list."""
    opts = AutoRouterOptions()
    if not body:
        return opts

    # session_id (OpenRouter body field)
    sid = body.get("session_id") or body.get("sessionId")
    if sid:
        opts.session_id = str(sid).strip() or None

    # OpenRouter multi-model fallback: "models": ["a", "b"]
    models = body.get("models")
    if isinstance(models, list):
        opts.models_fallback = [str(m).strip() for m in models if m]

    # plugins: [{id: "auto-router", allowed_models: [...], cost_quality_tradeoff: N}]
    plugins = body.get("plugins")
    if isinstance(plugins, list):
        for p in plugins:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or "").lower().replace("_", "-")
            if pid not in {"auto-router", "autorouter", "auto"}:
                continue
            allowed = p.get("allowed_models") or p.get("allowedModels") or []
            if isinstance(allowed, list):
                opts.allowed_models = [str(x).strip() for x in allowed if x]
            cq = p.get("cost_quality_tradeoff")
            if cq is None:
                cq = p.get("costQualityTradeoff")
            if cq is not None:
                try:
                    opts.cost_quality_tradeoff = int(cq)
                except (TypeError, ValueError):
                    pass
            break

    raw_model = body.get("model")
    tier = resolve_auto_tier(str(raw_model) if raw_model is not None else None)
    if tier is not None:
        opts.is_auto = True
        opts.tier = tier
        # cost_quality_tradeoff overrides tier mapping (OpenRouter behavior)
        if opts.cost_quality_tradeoff is not None and tier not in ("coding", "free", "fast"):
            opts.tier = tradeoff_to_tier(opts.cost_quality_tradeoff)
    elif raw_model is None or str(raw_model).strip() == "":
        opts.is_auto = True
        opts.tier = "balanced"

    return opts


def strip_router_client_fields(body: dict[str, Any]) -> dict[str, Any]:
    """Remove OpenRouter/Kilo client-only fields before upstream forward."""
    out = dict(body)
    for k in ("session_id", "sessionId", "plugins", "models", "provider", "route"):
        out.pop(k, None)
    return out


def match_allowed(model_id: str, patterns: list[str]) -> bool:
    """OpenRouter-style glob: anthropic/*, openai/gpt-5*, */claude-*."""
    if not patterns:
        return True
    mid = model_id.lower()
    bare = mid.rsplit("/", 1)[-1]
    for pat in patterns:
        p = pat.strip().lower()
        if not p:
            continue
        if fnmatch.fnmatch(mid, p) or fnmatch.fnmatch(bare, p):
            return True
        # provider/* style without fnmatch edge cases
        if p.endswith("/*") and mid.startswith(p[:-1]):
            return True
    return False


def is_free_model(model_id: str) -> bool:
    mid = model_id.lower()
    if _FREE_ID_RE.search(mid) or "free" in mid:
        return True
    return any(mid.startswith(p) for p in _FREE_PROVIDER_PREFIXES)


def filter_chain(
    chain: list[str],
    *,
    allowed_models: list[str] | None = None,
    free_only: bool = False,
) -> list[str]:
    """Hard-filter the chain. Empty result means no eligible models (caller → 503)."""
    out = list(chain)
    if free_only:
        out = [m for m in out if is_free_model(m)]
    if allowed_models:
        out = [m for m in out if match_allowed(m, allowed_models)]
    return out


def pin_model_first(chain: list[str], preferred: str | None) -> list[str]:
    """OpenRouter session stickiness: keep pinned model first when present."""
    if not preferred or not chain:
        return chain
    pref = preferred.lower()
    # Exact or bare-name match
    for i, m in enumerate(chain):
        if m.lower() == pref or m.lower().endswith("/" + pref.rsplit("/", 1)[-1]):
            if i == 0:
                return chain
            return [chain[i]] + chain[:i] + chain[i + 1 :]
    # Preferred not in pool — put it first anyway (passthrough pin)
    return [preferred] + [m for m in chain if m.lower() != pref]
