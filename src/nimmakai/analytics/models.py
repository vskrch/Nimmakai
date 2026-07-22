"""TraceRecord / TraceSpan dataclasses for analytics telemetry."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TraceSpan:
    span_type: str  # classify | route | upstream | fallback_advance
    started_at: float
    model_id: str | None = None
    provider_id: str | None = None
    ended_at: float | None = None
    duration_ms: float | None = None
    status_code: int | None = None
    success: bool = True
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def finish(
        self,
        *,
        success: bool = True,
        status_code: int | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceSpan:
        self.ended_at = time.perf_counter()
        self.duration_ms = (self.ended_at - self.started_at) * 1000
        self.success = success
        if status_code is not None:
            self.status_code = status_code
        if error_message is not None:
            self.error_message = error_message
        if metadata:
            self.metadata.update(metadata)
        return self

    def to_row(self, trace_id: str) -> tuple[Any, ...]:
        return (
            trace_id,
            self.span_type,
            self.model_id,
            self.provider_id,
            self.started_at,
            self.ended_at,
            self.duration_ms,
            self.status_code,
            1 if self.success else 0,
            self.error_message,
            json.dumps(self.metadata) if self.metadata else None,
        )


@dataclass
class TraceRecord:
    trace_id: str
    created_at: float
    method: str = "POST"
    path: str = ""
    client_ip: str | None = None
    api_key: str | None = None
    user_id: str | None = None
    user_agent: str | None = None

    model_requested: str | None = None
    intent: str | None = None
    intent_confidence: float = 0.0
    intent_rule_id: str | None = None
    route_mode: str | None = None

    model_routed: str | None = None
    provider_id: str | None = None
    chain: list[str] = field(default_factory=list)
    fallback_index: int = 0
    chain_length: int = 1

    status_code: int | None = None
    success: bool = True
    error_message: str | None = None
    is_stream: bool = False

    duration_ms: float | None = None
    classify_ms: float | None = None
    route_ms: float | None = None
    upstream_ttft_ms: float | None = None
    upstream_total_ms: float | None = None

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0

    estimated_cost_usd: float = 0.0

    message_count: int = 0
    has_tools: bool = False
    has_images: bool = False
    tool_count: int = 0
    char_length: int = 0

    spans: list[TraceSpan] = field(default_factory=list)

    def add_span(self, span: TraceSpan) -> TraceSpan:
        self.spans.append(span)
        return span

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.trace_id,
            self.created_at,
            self.method,
            self.path,
            self.client_ip,
            self._mask_key(self.api_key),
            self.user_id,
            self.user_agent,
            self.model_requested,
            self.intent,
            self.intent_confidence,
            self.intent_rule_id,
            self.route_mode,
            self.model_routed,
            self.provider_id,
            json.dumps(self.chain) if self.chain else None,
            self.fallback_index,
            self.chain_length if self.chain_length else len(self.chain) or 1,
            self.status_code,
            1 if self.success else 0,
            self.error_message,
            1 if self.is_stream else 0,
            self.duration_ms,
            self.classify_ms,
            self.route_ms,
            self.upstream_ttft_ms,
            self.upstream_total_ms,
            self.prompt_tokens,
            self.completion_tokens,
            self.cached_tokens,
            self.total_tokens or (self.prompt_tokens + self.completion_tokens),
            self.estimated_cost_usd,
            self.message_count,
            1 if self.has_tools else 0,
            1 if self.has_images else 0,
            self.tool_count,
            self.char_length,
        )

    def to_summary(self) -> dict[str, Any]:
        """Compact payload for SSE live feed."""
        return {
            "trace_id": self.trace_id,
            "created_at": self.created_at,
            "model_routed": self.model_routed,
            "model_requested": self.model_requested,
            "intent": self.intent,
            "provider_id": self.provider_id,
            "status_code": self.status_code,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "total_tokens": self.total_tokens
            or (self.prompt_tokens + self.completion_tokens),
            "fallback_index": self.fallback_index,
            "estimated_cost_usd": self.estimated_cost_usd,
            "error_message": self.error_message,
            "is_stream": self.is_stream,
            "user_id": self.user_id,
        }

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["api_key"] = self._mask_key(self.api_key)
        return d

    @staticmethod
    def _mask_key(key: str | None) -> str | None:
        if not key:
            return None
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}…{key[-4:]}"


TRACE_INSERT_SQL = """
INSERT INTO traces (
    trace_id, created_at, method, path, client_ip, api_key, user_id, user_agent,
    model_requested, intent, intent_confidence, intent_rule_id, route_mode,
    model_routed, provider_id, chain_json, fallback_index, chain_length,
    status_code, success, error_message, is_stream,
    duration_ms, classify_ms, route_ms, upstream_ttft_ms, upstream_total_ms,
    prompt_tokens, completion_tokens, cached_tokens, total_tokens,
    estimated_cost_usd, message_count, has_tools, has_images, tool_count, char_length
) VALUES (
    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
)
"""

SPAN_INSERT_SQL = """
INSERT INTO trace_spans (
    trace_id, span_type, model_id, provider_id, started_at, ended_at,
    duration_ms, status_code, success, error_message, metadata_json
) VALUES (?,?,?,?,?,?,?,?,?,?,?)
"""
