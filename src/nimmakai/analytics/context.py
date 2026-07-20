"""Request-scoped analytics helpers (contextvars + body extraction)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from nimmakai.analytics.models import TraceSpan

_spans_cv: ContextVar[list[TraceSpan] | None] = ContextVar(
    "nimmakai_trace_spans", default=None
)


def begin_span_collection() -> list[TraceSpan]:
    spans: list[TraceSpan] = []
    _spans_cv.set(spans)
    return spans


def end_span_collection() -> list[TraceSpan]:
    spans = _spans_cv.get() or []
    _spans_cv.set(None)
    return spans


def collect_span(span: TraceSpan) -> None:
    spans = _spans_cv.get()
    if spans is not None:
        spans.append(span)


def extract_request_context(body: dict[str, Any]) -> dict[str, Any]:
    """Pull message/tool/image/char stats from a chat-like body."""
    messages = body.get("messages") or body.get("input") or []
    if not isinstance(messages, list):
        messages = []
    tools = body.get("tools") or body.get("functions") or []
    if not isinstance(tools, list):
        tools = []

    has_images = False
    char_length = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, str):
            char_length += len(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    has_images = True
                text = part.get("text")
                if isinstance(text, str):
                    char_length += len(text)
        else:
            char_length += len(str(content or ""))

    return {
        "message_count": len(messages),
        "has_tools": bool(tools),
        "tool_count": len(tools),
        "has_images": has_images,
        "char_length": char_length,
    }
