"""Alias helpers for client model names → chain or NIM id."""

from __future__ import annotations


def normalize_model_name(name: str | None) -> str:
    if name is None:
        return ""
    return str(name).strip().lower()


def looks_like_nim_id(name: str) -> bool:
    """Heuristic: org/model style ids."""
    return "/" in name and not name.startswith("chain:")
