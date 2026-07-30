"""Alias helpers for client model names → chain or NIM id."""

from __future__ import annotations

import re

# Claude Code CLI appends context-window suffixes like [1m], [2m] to model
# names.  Strip them so alias lookups and /v1/models comparisons succeed.
_CC_CTX_SUFFIX_RE = re.compile(r"\[\d+m\]\s*$")


def normalize_model_name(name: str | None) -> str:
    if name is None:
        return ""
    n = str(name).strip().lower()
    # Strip Claude Code context-window suffixes (e.g. claude-opus-5[1m])
    n = _CC_CTX_SUFFIX_RE.sub("", n)
    return n


def looks_like_nim_id(name: str) -> bool:
    """Heuristic: org/model style ids."""
    return "/" in name and not name.startswith("chain:")
