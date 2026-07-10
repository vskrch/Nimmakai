"""Family matchers and 'latest' version resolution against live ids."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Exclude these Nemotron variants from generic text default
NEMOTRON_EXCLUDE = re.compile(
    r"(embed|rerank|ocr|asr|safety|guard|parse|page-elements|graphic|"
    r"table-structure|voicechat|content-safety|omni)",
    re.I,
)

# Exclude non-text Qwen from coding primary
QWEN_EXCLUDE = re.compile(r"(image|edit|vlb?$|vision)", re.I)

VERSION_RE = re.compile(
    r"(?:^|[^0-9])(\d+(?:\.\d+){0,3})(?:[^0-9]|$)|(?:^|-)v(\d+(?:\.\d+)*)",
    re.I,
)


@dataclass(frozen=True)
class FamilySpec:
    name: str
    # Match against full api id (org/model) lowercased
    include: re.Pattern[str]
    exclude: re.Pattern[str] | None = None


FAMILIES: dict[str, FamilySpec] = {
    "nemotron": FamilySpec(
        name="nemotron",
        include=re.compile(r"nemotron", re.I),
        exclude=NEMOTRON_EXCLUDE,
    ),
    "qwen": FamilySpec(
        name="qwen",
        include=re.compile(r"(^|/)qwen", re.I),
        exclude=QWEN_EXCLUDE,
    ),
    "glm_5_2": FamilySpec(
        name="glm_5_2",
        include=re.compile(r"glm-?5\.?2", re.I),
    ),
    "step_3_7": FamilySpec(
        name="step_3_7",
        include=re.compile(r"step-?3\.?7", re.I),
    ),
    "minimax_m3": FamilySpec(
        name="minimax_m3",
        include=re.compile(r"minimax.*m3\b|minimax-m3", re.I),
    ),
}


def version_key(model_id: str) -> tuple:
    """
    Sort key: higher version first. Falls back to string length / name.
    Examples: nemotron-3-super > nemotron-3-nano; qwen3.5 > qwen3.
    """
    mid = model_id.lower()
    versions: list[tuple[int, ...]] = []
    for m in VERSION_RE.finditer(mid):
        raw = m.group(1) or m.group(2)
        if not raw:
            continue
        parts = []
        for p in raw.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                break
        if parts:
            versions.append(tuple(parts))
    # Prefer the "largest" version tuple found
    best = max(versions) if versions else (0,)
    # Prefer larger / "super" / "ultra" / "pro" as mild tie-break
    tier = 0
    if "ultra" in mid:
        tier = 3
    elif "super" in mid:
        tier = 2
    elif "pro" in mid or "397b" in mid or "122b" in mid:
        tier = 1
    return (best, tier, len(mid))


def matches_family(model_id: str, family: str) -> bool:
    spec = FAMILIES.get(family)
    if spec is None:
        return False
    if not spec.include.search(model_id):
        return False
    return not (spec.exclude and spec.exclude.search(model_id))


def latest_in_family(live_ids: set[str] | list[str], family: str) -> str | None:
    candidates = [m for m in live_ids if matches_family(m, family)]
    if not candidates:
        return None
    return sorted(candidates, key=version_key, reverse=True)[0]


def all_in_family(live_ids: set[str] | list[str], family: str) -> list[str]:
    candidates = [m for m in live_ids if matches_family(m, family)]
    return sorted(candidates, key=version_key, reverse=True)


# Intent → primary family, then shared fallbacks
INTENT_PRIMARY: dict[str, str] = {
    "chat_fast": "nemotron",
    "coding_agentic": "qwen",
    "reasoning": "nemotron",
    "long_horizon": "qwen",
    "vision": "qwen",  # qwen VL if present; else fall through
    "embeddings": "nemotron",  # will often miss; embeddings handled separately
    "unknown": "nemotron",
}

SHARED_FALLBACKS = ("glm_5_2", "step_3_7", "minimax_m3")


def build_preference_chain(
    live_ids: set[str] | list[str],
    intent: str,
    *,
    probed_ok: set[str] | None = None,
) -> list[str]:
    """
    Build ordered chain: strongest primary + fallbacks.
    probed_ok is ignored for ordering (power-first); unavailability is handled
    via health cooldown demotion, not by preferring probed weaker models.
    """
    del probed_ok  # retained for call-site compatibility
    ids = set(live_ids)

    chain: list[str] = []
    primary_fam = INTENT_PRIMARY.get(intent, "nemotron")
    primary = latest_in_family(ids, primary_fam)
    if primary:
        chain.append(primary)

    for fam in SHARED_FALLBACKS:
        mid = latest_in_family(ids, fam)
        if mid and mid not in chain:
            chain.append(mid)

    # Soft: add next-best same-family variants as deeper fallbacks
    for fam in (primary_fam, *SHARED_FALLBACKS):
        for mid in all_in_family(ids, fam)[1:3]:
            if mid not in chain:
                chain.append(mid)

    return chain
