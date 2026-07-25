"""Unified Analytics & Request Telemetry Helper.

Ensures ALL endpoint routes (OpenAI chat/completions/embeddings, Anthropic messages,
and Responses API) record complete 360-degree trace records into AnalyticsStore.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import Request

from potato.analytics.context import end_span_collection, extract_request_context
from potato.analytics.cost import estimate_cost
from potato.analytics.models import TraceRecord
from potato.routing.selector import RouteDecision

logger = logging.getLogger(__name__)


def _enqueue_trace(store: Any, trace: TraceRecord, spans: list[Any]) -> None:
    if store is None:
        return
    try:
        writer = getattr(store, "writer", None)
        if writer is not None:
            writer.enqueue_trace(trace, spans=spans)
        else:
            store.record_trace(trace, spans=spans)
    except Exception:
        logger.debug("trace enqueue failed", exc_info=True)


def build_trace_base(
    request: Request,
    *,
    req_id: str,
    entry: Any,
    body: dict[str, Any],
    proxy_token: str | None,
) -> TraceRecord:
    ctx_stats = extract_request_context(body)
    method = str(getattr(entry, "method", "POST"))
    path = str(getattr(entry, "path", request.url.path))
    client_ip = str(getattr(entry, "client", request.client.host if request.client else "unknown"))
    user_agent = str(getattr(entry, "user_agent", request.headers.get("user-agent", "")))
    ts = float(getattr(entry, "ts", time.time()))

    auth_ctx = getattr(request.state, "auth", None)
    user_id = getattr(auth_ctx, "user_id", None) or getattr(auth_ctx, "email", None)

    return TraceRecord(
        trace_id=req_id,
        created_at=ts,
        method=method,
        path=path,
        client_ip=client_ip,
        api_key=proxy_token,
        user_id=user_id,
        user_agent=user_agent,
        model_requested=str(body.get("model") or "") or None,
        is_stream=bool(body.get("stream")),
        **ctx_stats,
    )


def apply_timing(trace: TraceRecord, timing: dict[str, Any] | None) -> None:
    if not timing:
        return
    if timing.get("classify_ms") is not None:
        trace.classify_ms = float(timing["classify_ms"])
    if timing.get("route_ms") is not None:
        trace.route_ms = float(timing["route_ms"])
    if timing.get("intent_confidence") is not None:
        trace.intent_confidence = float(timing["intent_confidence"] or 0)
    if timing.get("intent_rule_id"):
        trace.intent_rule_id = str(timing["intent_rule_id"])


def finalize_trace(
    request: Request,
    trace: TraceRecord | None,
    *,
    t0: float,
    status: int,
    decision: RouteDecision | None = None,
    model_routed: str | None = None,
    provider: str | None = None,
    fallback_index: int = 0,
    error: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    upstream_ttft_ms: float | None = None,
    upstream_total_ms: float | None = None,
    spans: list[Any] | None = None,
    timing: dict[str, Any] | None = None,
) -> None:
    settings = getattr(request.app.state, "settings", None)
    if settings and not getattr(settings, "analytics_enabled", True):
        end_span_collection()
        return
    if trace is None:
        end_span_collection()
        return

    store = getattr(request.app.state, "analytics_store", None)
    overrides = store.cost_overrides_map() if store else None

    if spans is None:
        spans = end_span_collection()
    else:
        end_span_collection()

    apply_timing(trace, timing)
    trace.duration_ms = (time.perf_counter() - t0) * 1000
    trace.status_code = status
    trace.success = 200 <= status < 400 and not error
    trace.error_message = error
    if decision is not None:
        trace.intent = decision.intent.value
        if not trace.intent_rule_id:
            trace.intent_rule_id = decision.rule_id
        trace.route_mode = decision.mode

    if model_routed:
        trace.model_routed = model_routed
    elif decision and decision.chain:
        trace.model_routed = decision.chain[min(fallback_index, len(decision.chain) - 1)]

    trace.provider_id = provider
    trace.fallback_index = fallback_index
    trace.prompt_tokens = prompt_tokens
    trace.completion_tokens = completion_tokens
    trace.total_tokens = prompt_tokens + completion_tokens
    trace.cached_tokens = cached_tokens
    trace.upstream_ttft_ms = upstream_ttft_ms
    trace.upstream_total_ms = upstream_total_ms

    final_model = trace.model_routed or trace.model_requested or "unknown"
    final_provider = trace.provider_id or "unknown"
    est_prompt, est_comp, est_total = estimate_cost(
        final_model,
        final_provider,
        prompt_tokens,
        completion_tokens,
        overrides=overrides,
    )
    trace.cost_prompt_usd = est_prompt
    trace.cost_completion_usd = est_comp
    trace.cost_total_usd = est_total

    _enqueue_trace(store, trace, spans)
