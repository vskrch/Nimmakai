"""Contextual feature extractor for LinUCB Reinforcement Learning routing.

Converts client prompt payloads, headers, and tool definitions into normalized
12-dimensional dense feature vectors in [0.0, 1.0].
"""

from __future__ import annotations

import math
import re
from typing import Any

FEATURE_DIM = 12
FEATURE_NAMES = [
    "token_length_tier",  # 0: log-scaled prompt token length
    "tool_density",  # 1: number of tools / 10
    "code_syntax_ratio",  # 2: presence of code fences & code keywords
    "lang_python",  # 3: Python keywords / file extensions
    "lang_typescript",  # 4: JS / TS / React keywords
    "lang_go",  # 5: Go syntax keywords
    "lang_rust_cpp",  # 6: Rust / C++ / systems syntax
    "agent_harness",  # 7: Cursor, OpenCode, Cline, Windsurf signature
    "modality_image",  # 8: Multimodal image input presence
    "reasoning_intensity",  # 9: Math / proof / step-by-step keywords
    "multi_turn_depth",  # 10: Conversation turn depth / session continuation
    "intent_coding_prior",  # 11: Intent classifier prior for coding/agentic
]

# Regex patterns for language & feature detection
_PY_RE = re.compile(r"\b(def|import|from|async\s+def|self|__init__|pytest|pydantic)\b", re.I)
_TS_RE = re.compile(
    r"\b(const|let|interface|type\s+\w+|async\s+function|useEffect|useState|export\s+default)\b",
    re.I,
)
_GO_RE = re.compile(
    r"\b(func\s+\w+|package\s+\w+|struct|interface\s*\{|go\s+func|chan|fmt\.P)\b", re.I
)
_RUST_RE = re.compile(
    r"\b(fn\s+\w+|impl|trait|pub\s+fn|mut|match|#\[derive|std::|#include|namespace)\b", re.I
)
_REASON_RE = re.compile(
    r"\b(prove|theorem|step[- ]by[- ]step|reason carefully|derivative|integral|logic)\b", re.I
)
_CODE_FENCE_RE = re.compile(r"```")
_AGENT_CLIENT_RE = re.compile(
    r"(cursor|opencode|cline|continue|windsurf|kiro|cascade|copilot)", re.I
)


def extract_feature_vector(
    body: dict[str, Any],
    headers: Any | None = None,
    intent_name: str | None = None,
) -> list[float]:
    """
    Extract a normalized 12-dimensional feature vector X for LinUCB bandit scoring.
    All features are bounded in [0.0, 1.0].
    """
    x = [0.0] * FEATURE_DIM

    # 1. Token length tier (log-scaled: 0 for 0 tokens, ~0.5 for 1k, ~1.0 for 100k+)
    messages = body.get("messages") or body.get("input") or []
    if not isinstance(messages, list):
        messages = []

    total_chars = 0
    turn_count = len(messages)
    has_image = False
    texts: list[str] = []

    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, str):
            total_chars += len(content)
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    ptype = str(part.get("type") or "")
                    if (
                        ptype in {"image_url", "input_image"}
                        or "image" in ptype
                        or "image_url" in part
                    ):
                        has_image = True
                    if "text" in part and isinstance(part["text"], str):
                        total_chars += len(part["text"])
                        texts.append(part["text"])

    est_tokens = max(1, total_chars // 4)
    x[0] = min(1.0, math.log10(est_tokens + 10) / 5.5)  # 100k tokens -> ~1.0

    # 2. Tool density
    tools = body.get("tools") or body.get("functions") or []
    num_tools = len(tools) if isinstance(tools, list) else 0
    x[1] = min(1.0, num_tools / 10.0)

    # 3. Code syntax ratio
    joined_text = "\n".join(texts)
    fences = len(_CODE_FENCE_RE.findall(joined_text))
    if fences >= 2 or (num_tools > 0):
        x[2] = 1.0
    elif fences == 1 or "```" in joined_text:
        x[2] = 0.5

    # 4-7. Code Language detection
    if _PY_RE.search(joined_text):
        x[3] = 1.0
    if _TS_RE.search(joined_text):
        x[4] = 1.0
    if _GO_RE.search(joined_text):
        x[5] = 1.0
    if _RUST_RE.search(joined_text):
        x[6] = 1.0

    # Normalize language flags if multiple triggered
    lang_sum = x[3] + x[4] + x[5] + x[6]
    if lang_sum > 1.0:
        for i in range(3, 7):
            x[i] /= lang_sum

    # 8. Agent harness detection
    is_agent = False
    if headers is not None and hasattr(headers, "get"):
        ua = str(headers.get("user-agent") or headers.get("User-Agent") or "")
        xc = str(headers.get("x-client") or headers.get("X-Client") or "")
        if _AGENT_CLIENT_RE.search(ua) or _AGENT_CLIENT_RE.search(xc):
            is_agent = True
    if num_tools > 0 or body.get("tool_choice") not in (None, "none", "None"):
        is_agent = True
    x[7] = 1.0 if is_agent else 0.0

    # 9. Multimodal image
    x[8] = 1.0 if has_image else 0.0

    # 10. Reasoning intensity
    x[9] = 1.0 if _REASON_RE.search(joined_text) else 0.0

    # 11. Multi-turn depth (turns / 20)
    x[10] = min(1.0, turn_count / 20.0)

    # 12. Intent prior
    if intent_name in ("coding_agentic", "reasoning", "long_horizon"):
        x[11] = 1.0
    elif intent_name in ("chat_fast",):
        x[11] = 0.2
    else:
        x[11] = 0.5

    return x
