"""Dynamic per-model context window discovery (no hardcoded model tables)."""

from __future__ import annotations

import re
from typing import Any

# Bounds for accepted discovered windows (tokens). Reject junk parses.
_MIN_CTX = 1_024
_MAX_CTX = 10_000_000

_CONTEXT_KEYS = (
    "context_length",
    "max_model_len",
    "max_sequence_length",
    "context_window",
    "max_context_length",
    "max_position_embeddings",
)

# Docs / marketing: "128K context", "context length: 131072", "up to 1M tokens"
_TEXT_PATTERNS = (
    re.compile(
        r"(?:context(?:\s+(?:length|window|size))?|max(?:imum)?\s+context)"
        r"\s*[:=]?\s*(\d+(?:\.\d+)?)\s*([kmb])?\s*(?:tokens?)?",
        re.I,
    ),
    re.compile(
        r"(\d+(?:\.\d+)?)\s*([kmb])?\s*(?:tokens?\s+)?(?:context|ctx)\b",
        re.I,
    ),
    re.compile(
        r"(?:up to|supports?)\s+(\d+(?:\.\d+)?)\s*([kmb])?\s*tokens?",
        re.I,
    ),
)


def _coerce_positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value)
    elif isinstance(value, str):
        s = value.strip().lower().replace(",", "")
        m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb])?", s)
        if not m:
            return None
        n = _scale(float(m.group(1)), m.group(2))
    else:
        return None
    if n < _MIN_CTX or n > _MAX_CTX:
        return None
    return n


def _scale(num: float, suffix: str | None) -> int:
    mult = 1
    if suffix == "k":
        mult = 1_000
    elif suffix == "m":
        mult = 1_000_000
    elif suffix == "b":
        mult = 1_000_000_000
    return int(num * mult)


def _from_mapping(obj: dict[str, Any]) -> int | None:
    for key in _CONTEXT_KEYS:
        if key in obj:
            got = _coerce_positive_int(obj.get(key))
            if got is not None:
                return got
    # Case-insensitive key scan one level
    lower_map = {str(k).lower(): v for k, v in obj.items()}
    for key in _CONTEXT_KEYS:
        if key in lower_map:
            got = _coerce_positive_int(lower_map[key])
            if got is not None:
                return got
    return None


def extract_context_length(obj: Any) -> int | None:
    """
    Pull a context window (tokens) from a NVIDIA / OpenAI model object.
    Returns None when unknown — never invents a default.
    """
    if not isinstance(obj, dict):
        return None
    direct = _from_mapping(obj)
    if direct is not None:
        return direct
    for nest_key in ("meta", "parameters", "model_info", "info", "config"):
        nested = obj.get(nest_key)
        if isinstance(nested, dict):
            got = _from_mapping(nested)
            if got is not None:
                return got
    return None


def parse_context_from_text(text: str | None) -> int | None:
    """Best-effort parse from docs / description prose."""
    if not text:
        return None
    best: int | None = None
    for pat in _TEXT_PATTERNS:
        for m in pat.finditer(text):
            n = _scale(float(m.group(1)), (m.group(2) or "").lower() or None)
            if n < _MIN_CTX or n > _MAX_CTX:
                continue
            if best is None or n > best:
                best = n
    return best


def merge_context(existing: int | None, new: int | None) -> int | None:
    """Prefer the larger known window; never shrink a larger upstream value."""
    if existing is None:
        return new
    if new is None:
        return existing
    return max(existing, new)


def enrich_model_dict(
    item: dict[str, Any],
    context_length: int | None,
) -> dict[str, Any]:
    """
    Return a shallow-copied model dict with context_length advertised when known.
    Never lowers an already-larger upstream context_length.
    """
    if context_length is None:
        return item
    out = dict(item)
    upstream = extract_context_length(out)
    final = merge_context(upstream, context_length)
    if final is None:
        return out
    out["context_length"] = final
    if "max_model_len" not in out:
        out["max_model_len"] = final
    return out
