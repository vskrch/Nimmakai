"""Ordered model fallback execution (separate from key rotation)."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nimmakai.routing.selector import RouteDecision
from nimmakai.safety.backoff import sleep_backoff
from nimmakai.upstream import parse_retry_after

if TYPE_CHECKING:
    from nimmakai.analytics.models import TraceSpan
    from nimmakai.balancer import KeyStats
    from nimmakai.catalog.registry import ModelRegistry
    from nimmakai.config import Settings
    from nimmakai.upstream import UpstreamClient

logger = logging.getLogger(__name__)

SpanCallback = Callable[["TraceSpan"], None]


@dataclass
class UpstreamResult:
    status_code: int
    body: Any
    headers: dict[str, str]
    key: KeyStats | None
    model: str
    fallback_index: int
    decision: RouteDecision
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    upstream_ms: float | None = None
    provider_id: str | None = None


@dataclass
class StreamResult:
    status_code: int
    byte_iter: AsyncIterator[bytes]
    headers: dict[str, str]
    key: KeyStats | None
    model: str
    fallback_index: int
    decision: RouteDecision
    upstream_ttft_ms: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    provider_id: str | None = None
    # Mutable usage bag updated as SSE chunks are scanned (stream may finish after return)
    usage: dict[str, int] = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
        }
    )
    # True when robust_iter detected a mid-stream failure and emitted error events
    stream_failed: bool = False


@dataclass
class TokenStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0

@dataclass
class RoutingStats:
    intents_total: dict[str, int] = field(default_factory=dict)
    models_total: dict[str, int] = field(default_factory=dict)
    model_tokens: dict[str, TokenStats] = field(default_factory=dict)
    key_tokens: dict[str, TokenStats] = field(default_factory=dict)
    fallback_advances: int = 0
    # Adaptive ranking: track last 50 requests' advance status (NMK-304)
    _recent_advances: list[bool] = field(default_factory=list)
    _max_advances_track: int = 50

    def record(self, intent: str, model: str, advanced: bool) -> None:
        self.intents_total[intent] = self.intents_total.get(intent, 0) + 1
        self.models_total[model] = self.models_total.get(model, 0) + 1
        if advanced:
            self.fallback_advances += 1
        self._recent_advances.append(advanced)
        if len(self._recent_advances) > self._max_advances_track:
            self._recent_advances = self._recent_advances[-self._max_advances_track:]

    def should_rerank(self) -> bool:
        """True when >30% of recent requests advanced → rankings may be stale."""
        if len(self._recent_advances) < 20:
            return False
        return sum(self._recent_advances) / len(self._recent_advances) > 0.30

    def record_tokens(self, model: str, key_id: str | None, in_tok: int, out_tok: int) -> None:
        if model not in self.model_tokens:
            self.model_tokens[model] = TokenStats()
        self.model_tokens[model].prompt_tokens += in_tok
        self.model_tokens[model].completion_tokens += out_tok
        
        if key_id:
            if key_id not in self.key_tokens:
                self.key_tokens[key_id] = TokenStats()
            self.key_tokens[key_id].prompt_tokens += in_tok
            self.key_tokens[key_id].completion_tokens += out_tok

def _is_model_not_found(status: int, body: Any) -> bool:
    if status == 404:
        return True
    text = ""
    if isinstance(body, dict):
        err = body.get("error")
        text = str(err.get("message") or "") if isinstance(err, dict) else str(body)
    elif isinstance(body, str):
        text = body
    low = text.lower()
    return any(
        s in low
        for s in ("model not found", "unknown model", "does not exist", "invalid model")
    )


def _is_retryable_model_error(status: int, body: Any) -> bool:
    if status in {401, 403, 405, 408, 429, 500, 502, 503, 504}:
        return True
    if _is_model_not_found(status, body):
        return True
    # Tools unsupported → try next model
    if status == 400 and isinstance(body, dict):
        msg = str((body.get("error") or {}).get("message") or "").lower()
        if "tool" in msg and ("not support" in msg or "unsupported" in msg):
            return True
        # Providers that wrap server errors in 400 responses
        _retryable_phrases = (
            "upstream request failed",
            "error from provider",
            "internal error",
            "service unavailable",
            "request failed",
            "bad gateway",
            "gateway timeout",
        )
        if any(phrase in msg for phrase in _retryable_phrases):
            logger.info(
                "retryable 400 detected: status=%s msg=%s",
                status,
                msg[:200],
            )
            return True
    return status in {400, 413} and _is_context_overflow_message(_body_message(body))


def _body_message(body: Any) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or "")
        return str(body)
    if isinstance(body, str):
        return body
    return ""


def _is_context_overflow_message(msg: str) -> bool:
    low = msg.lower()
    if any(
        s in low
        for s in (
            "context length",
            "context window",
            "maximum context",
            "max context",
            "too many tokens",
            "token limit",
            "prompt is too long",
        )
    ):
        return True
    return bool(
        re.search(r"context.*exceed|exceeds.*context|maximum.*tokens", low)
    )


def _is_non_retryable_client_error(status: int, body: Any) -> bool:
    # 401/403 are retryable across providers (keys already rotated within the
    # current provider by UpstreamClient); only treat 400/422 as hard-stop
    # unless the body signals a model/context issue.
    if status in {400, 422}:
        return not _is_retryable_model_error(status, body)
    return False


def _analyze_success_body(
    body: Any, *, had_tools: bool, path: str = "/chat/completions"
) -> tuple[bool, bool | None]:
    """
    Returns (empty_reply, tool_ok).
    tool_ok is None when tools were not requested.

    Schema-aware: chat (message), text completions (text), responses (output),
    embeddings (data[].embedding).
    """
    if not isinstance(body, dict):
        return False, None if not had_tools else False

    path_l = (path or "").lower()
    is_completions = path_l.endswith("/completions") and "chat" not in path_l
    is_responses = "/responses" in path_l
    is_embeddings = "/embeddings" in path_l

    # Embeddings: data[].embedding must be a non-empty vector
    if is_embeddings:
        data = body.get("data")
        if not isinstance(data, list) or not data:
            return True, None
        for item in data:
            if not isinstance(item, dict):
                return True, None
            emb = item.get("embedding")
            if not isinstance(emb, list) or len(emb) == 0:
                return True, None
        return False, None

    # Text completions: choices[0].text
    if is_completions:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return True, None
        ch0 = choices[0] if isinstance(choices[0], dict) else {}
        text = ch0.get("text")
        empty = text in (None, "")
        return empty, None

    # Responses API: output / output_text
    if is_responses:
        if body.get("output_text"):
            return False, None if not had_tools else True
        output = body.get("output")
        if isinstance(output, list) and output:
            # Any non-empty content/text in output items = success
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, str) and content:
                    return False, None if not had_tools else True
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and (
                            part.get("text") or part.get("type") == "function_call"
                        ):
                            return False, None if not had_tools else True
                if item.get("type") in {"function_call", "tool_call"}:
                    return False, True if had_tools else None
            return True, False if had_tools else None
        return True, False if had_tools else None

    # Chat completions
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return True, False if had_tools else None
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(msg, dict):
        return True, False if had_tools else None
    content = msg.get("content")
    tool_calls = msg.get("tool_calls") or msg.get("function_call")
    empty = content in (None, "", []) and not tool_calls
    if not had_tools:
        return empty, None
    if tool_calls:
        return empty, True
    # Tools requested but none returned — soft fail signal
    return empty, False


class FallbackExecutor:
    def __init__(
        self,
        upstream: UpstreamClient,
        registry: ModelRegistry,
        settings: Settings,
        stats: RoutingStats | None = None,
        hub: Any | None = None,
        span_callback: SpanCallback | None = None,
    ) -> None:
        self.upstream = upstream
        self.registry = registry
        self.settings = settings
        self.stats = stats or RoutingStats()
        self.hub = hub
        self._span_cb = span_callback or None
        # Prefer contextvar collector so concurrent requests don't share state
        from nimmakai.analytics.context import collect_span

        if self._span_cb is None:
            self._span_cb = collect_span

    def set_span_callback(self, cb: SpanCallback | None) -> None:
        self._span_cb = cb
        if self._span_cb is None:
            from nimmakai.analytics.context import collect_span

            self._span_cb = collect_span

    def _emit_span(self, span: Any) -> None:
        if self._span_cb is None:
            return
        try:
            self._span_cb(span)
        except Exception:
            logger.debug("span callback failed", exc_info=True)

    def _provider_id_for(self, model: str) -> str | None:
        if self.hub is None:
            return None
        try:
            from nimmakai.catalog.providers import split_provider_model

            pid, _ = split_provider_model(
                model, self.hub.provider_ids, default_provider="nim"
            )
            return pid
        except Exception:
            return None

    def _circuit_fail(self, provider_id: str | None) -> None:
        if not provider_id or self.hub is None:
            return
        cb = getattr(self.hub, "circuit_breaker", None)
        if cb is not None:
            cb.fail(provider_id)

    def _circuit_succeed(self, provider_id: str | None) -> None:
        if not provider_id or self.hub is None:
            return
        cb = getattr(self.hub, "circuit_breaker", None)
        if cb is not None:
            cb.succeed(provider_id)

    def _make_upstream_span(
        self,
        *,
        model: str,
        t0: float,
        status: int | None = None,
        success: bool = True,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
        span_type: str = "upstream",
    ) -> Any:
        from nimmakai.analytics.models import TraceSpan

        ended = time.perf_counter()
        return TraceSpan(
            span_type=span_type,
            model_id=model,
            provider_id=self._provider_id_for(model),
            started_at=t0,
            ended_at=ended,
            duration_ms=(ended - t0) * 1000,
            status_code=status,
            success=success,
            error_message=error_message,
            metadata=metadata or {},
        )

    def _client_for(self, model: str) -> tuple[Any, str]:
        """Return (upstream_client, upstream_model_id) for this namespaced model."""
        # Use original-case id for upstream round-trip (case-sensitive providers)
        original = self.registry.original_id(model)
        if self.hub is not None:
            client, _pid, upstream_mid = self.hub.client_for_model(original)
            return client, upstream_mid
        return self.upstream, original

    def _provider_available(self, model: str) -> bool:
        if self.hub is None:
            return True
        try:
            from nimmakai.catalog.providers import split_provider_model

            pid, _ = split_provider_model(
                model, self.hub.provider_ids, default_provider="nim"
            )
            return self.hub.has_runtime(pid)
        except Exception:
            logger.exception("provider availability check failed for model %s", model)
            return False

    def _chain(self, decision: RouteDecision, *, had_tools: bool = False) -> list[str]:
        max_n = int(getattr(self.settings, "max_model_fallbacks", 10) or 10)
        if decision.intent.value == "coding_agentic":
            max_n = max(
                max_n,
                int(getattr(self.settings, "coding_max_fallbacks", 12) or 12),
            )
        raw = list(decision.chain)
        # Never execute admin-disabled models (covers passthrough / emergency paths)
        disabled = getattr(self.registry, "disabled_models", None) or set()
        if disabled:
            raw = [
                m
                for m in raw
                if (
                    self.registry.resolve_live_id(m, include_disabled=True) or m
                )
                not in disabled
            ]
        # Pre-filter: skip models confirmed to not support tools when tools are present
        if had_tools and hasattr(self.registry, "ladder"):
            caps = getattr(self.registry.ladder, "capabilities", {})
            filtered = []
            for m in raw:
                cap = caps.get(m) or {}
                if cap.get("supports_tools") is False:
                    logger.debug("pre-filter: skipping %s (tools not supported)", m)
                    continue
                filtered.append(m)
            if filtered:
                raw = filtered
        # Drop models whose provider has no active keys/runtime (production safety)
        available = [m for m in raw if self._provider_available(m)]
        if not available:
            # Self-heal: rebuild emergency chain from live catalog
            try:
                from nimmakai.resilience import emergency_coding_chain
                from nimmakai.routing.auto_router import filter_chain

                available = [
                    m
                    for m in emergency_coding_chain(self.registry, max_n=max_n)
                    if self._provider_available(m)
                    and (
                        self.registry.resolve_live_id(m, include_disabled=True) or m
                    )
                    not in disabled
                ]
                # Re-apply caller hard constraints after emergency rebuild
                allowed = list(getattr(decision, "allowed_models", None) or [])
                free_only = str(getattr(decision, "auto_tier", "") or "").lower() == "free"
                if allowed or free_only:
                    available = filter_chain(
                        available, allowed_models=allowed or None, free_only=free_only
                    )
                if available:
                    logger.warning(
                        "empty chain healed with %s emergency models", len(available)
                    )
            except Exception:
                logger.exception("emergency chain rebuild failed")
        if not available and raw:
            logger.warning("all %s chain models have unavailable providers", len(raw))
        # Continuous optimizer: intelligence × speed × health (every request)
        intent = decision.intent.value
        variant = getattr(decision, "variant", None) or "default"
        if variant == "default":
            req = str(decision.requested_model or "").lower()
            tier = str(getattr(decision, "auto_tier", "") or "").lower()
            if (
                "cheap" in req
                or tier in ("efficient", "free")
                or "efficient" in req
            ):
                variant = "cheap"
            elif "fast" in req or tier == "fast":
                variant = "fast"
        pinned = getattr(decision, "pinned_head", None) or getattr(
            decision, "sticky_model", None
        )
        # Drop pin if it was admin-disabled
        if pinned and disabled:
            pin_live = (
                self.registry.resolve_live_id(pinned, include_disabled=True) or pinned
            )
            if pin_live in disabled:
                pinned = None
        # Re-rank, but keep pinned head first unless unhealthy (F-08)
        if available:
            from nimmakai.routing.optimizer import optimize_chain

            if pinned and pinned in available:
                tail = [m for m in available if m != pinned]
                tail = optimize_chain(
                    tail,
                    self.registry,
                    intent=intent,
                    variant=variant,
                    max_n=None,
                )
                unhealthy = (
                    hasattr(self.registry, "health")
                    and self.registry.health.is_unhealthy(pinned)
                )
                if unhealthy:
                    logger.info("pin_demoted model=%s reason=unhealthy", pinned)
                    available = optimize_chain(
                        [pinned] + tail,
                        self.registry,
                        intent=intent,
                        variant=variant,
                        max_n=None,
                    )
                else:
                    available = [pinned] + tail
            else:
                available = optimize_chain(
                    available,
                    self.registry,
                    intent=intent,
                    variant=variant,
                    max_n=None,
                )
        # Fail-fast: skip cooling models for TTFT (keep 1 cold last-resort)
        # Preserve pinned head if healthy.
        if available and hasattr(self.registry, "health"):
            hot = [m for m in available if not self.registry.health.is_unhealthy(m)]
            cold = [m for m in available if self.registry.health.is_unhealthy(m)]
            if pinned and pinned in hot:
                hot = [pinned] + [m for m in hot if m != pinned]
            available = hot + cold[:1]
        # Drop models whose known context cannot fit the estimate (T13)
        est = getattr(decision, "estimated_tokens", None)
        if est and available:
            fit: list[str] = []
            unknown: list[str] = []
            for m in available:
                ctx_len = self.registry.context_length_for(m)
                if ctx_len is None:
                    unknown.append(m)
                elif ctx_len >= est:
                    fit.append(m)
            if fit or unknown:
                available = fit + unknown
        chain = available[: max(1, max_n)]
        return chain

    def routing_headers(
        self,
        decision: RouteDecision,
        *,
        model: str,
        key_id: str | None,
        fallback_index: int,
        provider_id: str | None = None,
    ) -> dict[str, str]:
        h = {
            "X-Nimmakai-Model": model,
            "X-Nimmakai-Intent": decision.intent.value,
            "X-Nimmakai-Route-Mode": decision.mode,
            "X-Nimmakai-Fallback-Index": str(fallback_index),
            "X-Nimmakai-Rule-Id": decision.rule_id,
        }
        if key_id:
            h["X-Nimmakai-Key-Id"] = key_id
        if decision.requested_model:
            h["X-Nimmakai-Requested-Model"] = str(decision.requested_model)
        if getattr(decision, "auto_tier", None):
            h["X-Nimmakai-Auto-Tier"] = str(decision.auto_tier)
        if getattr(decision, "sticky_model", None):
            h["X-Nimmakai-Sticky-Model"] = str(decision.sticky_model)
        ctx_len = self.registry.context_length_for(model)
        if ctx_len is not None:
            h["X-Nimmakai-Context-Length"] = str(ctx_len)
        pid = provider_id or self._provider_id_for(model)
        if pid:
            h["X-Nimmakai-Provider"] = pid
        return h

    async def execute_json(
        self,
        path: str,
        body: dict[str, Any],
        decision: RouteDecision,
        *,
        preferred_key_id: str | None = None,
        forward_headers: dict[str, str] | None = None,
        fallback_on_pool_exhaust: bool | None = None,
    ) -> UpstreamResult:
        had_tools = bool(
            (body.get("tools") or body.get("functions"))
            or body.get("tool_choice") not in (None, "none", "None")
        )
        chain = self._chain(decision, had_tools=had_tools)
        if not chain:
            return UpstreamResult(
                status_code=503,
                body={
                    "error": {
                        "message": "No models available in routing chain.",
                        "type": "server_error",
                        "code": "nimmakai_catalog_empty",
                    }
                },
                headers={},
                key=None,
                model="",
                fallback_index=0,
                decision=decision,
            )

        advance_on_pool = (
            self.settings.fallback_on_pool_exhaust
            if fallback_on_pool_exhaust is None
            else fallback_on_pool_exhaust
        )
        last: UpstreamResult | None = None

        import httpx

        from nimmakai.compat import openai_error

        deadline = time.monotonic() + float(
            getattr(self.settings, "request_deadline_seconds", 180.0) or 180.0
        )

        for idx, model in enumerate(chain):
            remaining = deadline - time.monotonic()
            if remaining < 3.0 and idx > 0:
                return UpstreamResult(
                    status_code=504,
                    body=openai_error(
                        "Request deadline exceeded before trying remaining models.",
                        code="request_deadline_exceeded",
                        type_="server_error",
                    ),
                    headers={},
                    key=None,
                    model=last.model if last else model,
                    fallback_index=idx,
                    decision=decision,
                )
            try:
                client, upstream_mid = self._client_for(model)
            except RuntimeError as exc:
                if idx < len(chain) - 1:
                    self.stats.fallback_advances += 1
                    logger.info("client_for_model failed on %s: %s; advancing", model, exc)
                    continue
                return UpstreamResult(
                    status_code=503,
                    body={
                        "error": {
                            "message": str(exc),
                            "type": "server_error",
                            "code": "nimmakai_provider_unavailable",
                        }
                    },
                    headers={},
                    key=None,
                    model=model,
                    fallback_index=idx,
                    decision=decision,
                )
            attempt_body = {**body, "model": upstream_mid}
            t_attempt = time.perf_counter()
            pid = self._provider_id_for(model)
            # Apply per-model recommendations (temperature, max_tokens)
            if hasattr(self.registry, "ladder"):
                rec = self.registry.ladder.model_recommendations(model)
                if rec:
                    # Cap max_tokens to model limit if client didn't set it
                    max_limit = rec.get("max_tokens_limit")
                    if max_limit and not attempt_body.get("max_tokens"):
                        attempt_body["max_tokens"] = max_limit
                    elif max_limit and attempt_body.get("max_tokens"):
                        attempt_body["max_tokens"] = min(
                            attempt_body["max_tokens"], max_limit
                        )
            # Log large payloads for debugging agentic loop context overflow
            import sys
            body_size = sys.getsizeof(str(attempt_body))
            if body_size > 100_000:  # >100KB
                logger.info(
                    "large request body: model=%s size=%dKB messages=%d",
                    model,
                    body_size // 1024,
                    len(attempt_body.get("messages") or []),
                )
            try:
                import asyncio as _aio

                attempt_budget = max(1.0, min(remaining, 45.0))
                status, resp_body, headers, key = await _aio.wait_for(
                    client.request_json(
                        "POST",
                        path,
                        json_body=attempt_body,
                        forward_headers=forward_headers,
                        preferred_key_id=preferred_key_id,
                        max_retries=2,
                    ),
                    timeout=attempt_budget,
                )
            except TimeoutError as exc:
                self._circuit_fail(pid)
                self._emit_span(
                    self._make_upstream_span(
                        model=model,
                        t0=t_attempt,
                        status=504,
                        success=False,
                        error_message="attempt_deadline_exceeded",
                        span_type="fallback_advance"
                        if idx < len(chain) - 1
                        else "upstream",
                    )
                )
                if idx < len(chain) - 1:
                    self.stats.fallback_advances += 1
                    await sleep_backoff(
                        idx,
                        base=self.settings.retry_backoff_base_seconds,
                        cap=min(2.0, self.settings.retry_backoff_cap_seconds),
                    )
                    logger.info(
                        "json attempt deadline on %s; falling back", model
                    )
                    continue
                return UpstreamResult(
                    status_code=504,
                    body=openai_error(
                        "Upstream attempt exceeded request deadline.",
                        code="request_deadline_exceeded",
                        type_="server_error",
                    ),
                    headers={},
                    key=None,
                    model=model,
                    fallback_index=idx,
                    decision=decision,
                    provider_id=pid,
                )
            except (RuntimeError, httpx.HTTPError, OSError) as exc:
                msg = str(exc).lower()
                retryable_pool = (
                    isinstance(exc, (httpx.HTTPError, OSError))
                    or "rate-limited" in msg
                    or "cooling" in msg
                    or "unavailable" in msg
                    or "no api keys" in msg
                    or "not available" in msg
                    or "provider" in msg
                    or "circuit" in msg
                )
                self._circuit_fail(pid)
                self._emit_span(
                    self._make_upstream_span(
                        model=model,
                        t0=t_attempt,
                        status=503,
                        success=False,
                        error_message=str(exc),
                        span_type="fallback_advance"
                        if (advance_on_pool or isinstance(exc, (httpx.HTTPError, OSError)))
                        and idx < len(chain) - 1
                        else "upstream",
                    )
                )
                if retryable_pool and idx < len(chain) - 1:
                    self.stats.fallback_advances += 1
                    await sleep_backoff(
                        idx,
                        base=self.settings.retry_backoff_base_seconds,
                        cap=min(2.0, self.settings.retry_backoff_cap_seconds),
                    )
                    logger.info(
                        "provider/transport unavailable on %s (%s); advancing model",
                        model,
                        exc,
                    )
                    continue
                if retryable_pool:
                    return UpstreamResult(
                        status_code=503,
                        body={
                            "error": {
                                "message": str(exc),
                                "type": "server_error",
                                "code": "nimmakai_pool_exhausted",
                            }
                        },
                        headers={},
                        key=None,
                        model=model,
                        fallback_index=idx,
                        decision=decision,
                        upstream_ms=(time.perf_counter() - t_attempt) * 1000,
                        provider_id=pid,
                    )
                raise

            key_id = key.key_id if key else None
            unavailable = _is_model_not_found(status, resp_body)
            success = 200 <= status < 300
            if success:
                self._circuit_succeed(pid)
            elif status >= 500:
                self._circuit_fail(pid)
            had_tools = bool(
                (body.get("tools") or body.get("functions"))
                or body.get("tool_choice") not in (None, "none", "None")
            )
            empty_reply = False
            tool_ok: bool | None = None
            if success:
                empty_reply, tool_ok = _analyze_success_body(
                    resp_body, had_tools=had_tools, path=path
                )
            # Adaptive speed signal: JSON path latency (if measured upstream)
            latency = (time.perf_counter() - t_attempt) * 1000
            tokens = None
            pt = ct = cached = 0
            if success and isinstance(resp_body, dict):
                usage = resp_body.get("usage")
                if isinstance(usage, dict):
                    pt = int(usage.get("prompt_tokens") or 0)
                    ct = int(usage.get("completion_tokens") or 0)
                    cached = int(
                        usage.get("cached_tokens")
                        or (usage.get("prompt_tokens_details") or {}).get(
                            "cached_tokens", 0
                        )
                        or 0
                    )
                    tokens = pt + ct if (pt or ct) else None
            self._emit_span(
                self._make_upstream_span(
                    model=model,
                    t0=t_attempt,
                    status=status,
                    success=success and not (
                        (had_tools and tool_ok is False) or empty_reply
                    ),
                    error_message=None
                    if success
                    else f"upstream_{status}",
                    metadata={
                        "prompt_tokens": pt,
                        "completion_tokens": ct,
                        "cached_tokens": cached,
                        "empty_reply": empty_reply,
                        "tool_ok": tool_ok,
                    },
                    span_type="upstream",
                )
            )
            self.registry.record_outcome(
                model,
                key_id,
                success=success,
                latency=latency / 1000.0 if latency else None,
                tokens=tokens,
                status_code=status,
                unavailable=unavailable,
                intent=decision.intent.value,
                empty_reply=empty_reply,
                had_tools=had_tools,
                tool_ok=tool_ok,
            )
            if had_tools and tool_ok is True:
                self.registry.ladder.set_capability(model, supports_tools=True)
            elif had_tools and tool_ok is False and success:
                # Don't mark unsupported on empty once — wait for learning demotion
                pass
            body_l = str(resp_body).lower()
            if (
                (unavailable or status == 400)
                and "tool" in body_l
                and "support" in body_l
            ):
                self.registry.ladder.set_capability(model, supports_tools=False)

            if success:
                soft_fail = (had_tools and tool_ok is False) or empty_reply
                if soft_fail and idx < len(chain) - 1:
                    self.stats.fallback_advances += 1
                    logger.info(
                        "model %s soft-fail (empty=%s tool_ok=%s); falling back",
                        model,
                        empty_reply,
                        tool_ok,
                    )
                    continue
                if isinstance(resp_body, dict):
                    if "model" in resp_body:
                        resp_body = {**resp_body, "model": model}
                    usage = resp_body.get("usage")
                    if isinstance(usage, dict):
                        pt = int(usage.get("prompt_tokens") or 0)
                        ct = int(usage.get("completion_tokens") or 0)
                        cached = int(
                            usage.get("cached_tokens")
                            or (usage.get("prompt_tokens_details") or {}).get(
                                "cached_tokens", 0
                            )
                            or 0
                        )
                        self.stats.record_tokens(model, key_id, pt, ct)
                self.stats.record(decision.intent.value, model, advanced=idx > 0)
                return UpstreamResult(
                    status_code=status,
                    body=resp_body,
                    headers=headers,
                    key=key,
                    model=model,
                    fallback_index=idx,
                    decision=decision,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    cached_tokens=cached,
                    upstream_ms=latency,
                    provider_id=pid,
                )

            from nimmakai.compat import wrap_upstream_error

            last = UpstreamResult(
                status_code=status,
                body=wrap_upstream_error(resp_body, status=status),
                headers=headers,
                key=key,
                model=model,
                fallback_index=idx,
                decision=decision,
                upstream_ms=latency,
                provider_id=pid,
            )

            if _is_non_retryable_client_error(status, resp_body):
                self.stats.record(decision.intent.value, model, advanced=False)
                return last

            if _is_retryable_model_error(status, resp_body) and idx < len(chain) - 1:
                # 504 = upstream gateway timeout — no point backoff, advance now.
                # 503 = transient overload — tiny backoff then advance.
                if status == 429:
                    ra = parse_retry_after(
                        headers.get("Retry-After") or headers.get("retry-after")
                    )
                    await sleep_backoff(
                        idx,
                        base=self.settings.retry_backoff_base_seconds,
                        cap=self.settings.retry_backoff_cap_seconds,
                        retry_after=ra,
                    )
                elif status in {500, 502, 503}:
                    await sleep_backoff(
                        idx,
                        base=self.settings.retry_backoff_base_seconds,
                        cap=min(1.0, self.settings.retry_backoff_cap_seconds),
                    )
                self.stats.fallback_advances += 1
                logger.info(
                    "model %s failed status=%s; falling back (%s/%s)",
                    model,
                    status,
                    idx + 1,
                    len(chain),
                )
                continue

            # 429 after key retries — optionally advance
            if status == 429 and advance_on_pool and idx < len(chain) - 1:
                ra = parse_retry_after(
                    headers.get("Retry-After") or headers.get("retry-after")
                )
                await sleep_backoff(
                    idx,
                    base=self.settings.retry_backoff_base_seconds,
                    cap=self.settings.retry_backoff_cap_seconds,
                    retry_after=ra,
                )
                self.stats.fallback_advances += 1
                continue

            self.stats.record(decision.intent.value, model, advanced=idx > 0)
            return last

        assert last is not None
        if last.status_code >= 400:
            # ── Last-resort: clear cooldowns, force-allow providers, retry fresh models ──
            remaining = deadline - time.monotonic()
            if remaining >= 5.0:
                if hasattr(self.registry, "health"):
                    for h in self.registry.health._by_model.values():
                        h.cooldown_until = 0.0
                if self.hub is not None:
                    for pid in self.hub.provider_ids:
                        self.hub.circuit_breaker.force_allow(pid)
                retry_chain = self._chain(decision, had_tools=had_tools)
                if not retry_chain:
                    from nimmakai.resilience import emergency_coding_chain

                    retry_chain = emergency_coding_chain(
                        self.registry,
                        max_n=int(
                            getattr(self.settings, "coding_max_fallbacks", 12) or 12
                        ),
                    )
                tried = set(chain)
                fresh = [m for m in (retry_chain or []) if m not in tried]
                if fresh:
                    logger.warning(
                        "last-resort: retrying %s previously-cooled models",
                        len(fresh),
                    )
                    import asyncio as _aio2

                    from nimmakai.compat import wrap_upstream_error as _wue

                    for idx2, model2 in enumerate(fresh):
                        rem2 = deadline - time.monotonic()
                        if rem2 < 3.0:
                            break
                        try:
                            client2, upstream_mid2 = self._client_for(model2)
                        except RuntimeError:
                            continue
                        attempt_body2 = {**body, "model": upstream_mid2}
                        if hasattr(self.registry, "ladder"):
                            rec = self.registry.ladder.model_recommendations(model2)
                            if rec:
                                ml = rec.get("max_tokens_limit")
                                if ml and not attempt_body2.get("max_tokens"):
                                    attempt_body2["max_tokens"] = ml
                                elif ml and attempt_body2.get("max_tokens"):
                                    attempt_body2["max_tokens"] = min(
                                        attempt_body2["max_tokens"], ml
                                    )
                        pid2 = self._provider_id_for(model2)
                        t2 = time.perf_counter()
                        try:
                            budget2 = max(1.0, min(rem2, 45.0))
                            s2, rb2, hd2, k2 = await _aio2.wait_for(
                                client2.request_json(
                                    "POST",
                                    path,
                                    json_body=attempt_body2,
                                    forward_headers=forward_headers,
                                    preferred_key_id=preferred_key_id,
                                    max_retries=2,
                                ),
                                timeout=budget2,
                            )
                        except TimeoutError:
                            self._circuit_fail(pid2)
                            self.registry.record_outcome(
                                model2, None, success=False,
                                status_code=504, intent=decision.intent.value,
                            )
                            continue
                        except (RuntimeError, httpx.HTTPError, OSError):
                            self._circuit_fail(pid2)
                            self.registry.record_outcome(
                                model2, None, success=False,
                                status_code=503, intent=decision.intent.value,
                            )
                            continue

                        lat2 = (time.perf_counter() - t2) * 1000
                        if 200 <= s2 < 300:
                            self._circuit_succeed(pid2)
                            empty2, tool_ok2 = _analyze_success_body(
                                rb2, had_tools=had_tools, path=path
                            )
                            if empty2 or (had_tools and tool_ok2 is False):
                                self.registry.record_outcome(
                                    model2, k2.key_id if k2 else None,
                                    success=False, status_code=s2,
                                    intent=decision.intent.value,
                                    empty_reply=empty2, had_tools=had_tools,
                                    tool_ok=tool_ok2,
                                )
                                last = UpstreamResult(
                                    status_code=s2, body=rb2, headers=hd2,
                                    key=k2, model=model2,
                                    fallback_index=len(chain) + idx2,
                                    decision=decision, upstream_ms=lat2,
                                    provider_id=pid2,
                                )
                                continue
                            pt2 = ct2 = cached2 = 0
                            if isinstance(rb2, dict):
                                usage2 = rb2.get("usage")
                                if isinstance(usage2, dict):
                                    pt2 = int(usage2.get("prompt_tokens") or 0)
                                    ct2 = int(usage2.get("completion_tokens") or 0)
                                    cached2 = int(
                                        usage2.get("cached_tokens")
                                        or (usage2.get("prompt_tokens_details") or {}).get(
                                            "cached_tokens", 0
                                        )
                                        or 0
                                    )
                            self.registry.record_outcome(
                                model2, k2.key_id if k2 else None,
                                success=True, latency=lat2 / 1000,
                                status_code=s2, intent=decision.intent.value,
                                empty_reply=empty2, had_tools=had_tools,
                                tool_ok=tool_ok2,
                            )
                            self.stats.record(
                                decision.intent.value, model2, advanced=True
                            )
                            self.stats.record_tokens(
                                model2, k2.key_id if k2 else None, pt2, ct2
                            )
                            if isinstance(rb2, dict) and "model" in rb2:
                                rb2 = {**rb2, "model": model2}
                            return UpstreamResult(
                                status_code=s2, body=rb2, headers=hd2,
                                key=k2, model=model2,
                                fallback_index=len(chain) + idx2,
                                decision=decision, prompt_tokens=pt2,
                                completion_tokens=ct2, cached_tokens=cached2,
                                upstream_ms=lat2, provider_id=pid2,
                            )
                        # Failed — record and try next fresh model
                        self._circuit_fail(pid2)
                        self.registry.record_outcome(
                            model2, k2.key_id if k2 else None,
                            success=False, status_code=s2,
                            intent=decision.intent.value,
                        )
                        last = UpstreamResult(
                            status_code=s2,
                            body=_wue(rb2, status=s2),
                            headers=hd2, key=k2, model=model2,
                            fallback_index=len(chain) + idx2,
                            decision=decision, upstream_ms=lat2,
                            provider_id=pid2,
                        )

            last = UpstreamResult(
                status_code=503,
                body={
                    "error": {
                        "message": "All models in routing chain failed.",
                        "type": "server_error",
                        "code": "nimmakai_models_exhausted",
                        "last_status": last.status_code,
                        "last_body": last.body,
                    }
                },
                headers=last.headers,
                key=last.key,
                model=last.model,
                fallback_index=last.fallback_index,
                decision=decision,
            )
        self.stats.record(decision.intent.value, last.model, advanced=True)
        return last

    async def execute_stream(
        self,
        path: str,
        body: dict[str, Any],
        decision: RouteDecision,
        *,
        preferred_key_id: str | None = None,
        forward_headers: dict[str, str] | None = None,
    ) -> StreamResult:
        """
        Try models until a stream opens successfully. Never switch mid-stream.
        """
        had_tools = bool(
            (body.get("tools") or body.get("functions"))
            or body.get("tool_choice") not in (None, "none", "None")
        )
        chain = self._chain(decision, had_tools=had_tools)
        from nimmakai.compat import frame_sse_error, json_body_to_sse, openai_error, wrap_upstream_error

        if not chain:
            payload = frame_sse_error(
                "No models available in routing chain. "
                "Add provider API keys and refresh the catalog.",
                code="nimmakai_catalog_empty",
                status=503,
            )

            async def empty() -> AsyncIterator[bytes]:
                yield payload

            return StreamResult(
                status_code=503,
                byte_iter=empty(),
                headers={"content-type": "text/event-stream"},
                key=None,
                model="",
                fallback_index=0,
                decision=decision,
            )

        import json as _json

        import httpx

        last_status = 503
        last_headers: dict[str, str] = {}
        last_key = None
        last_model = chain[0]
        last_pid: str | None = None
        saw_ttft_stall = False
        saw_deadline = False
        deadline = time.monotonic() + float(
            getattr(self.settings, "request_deadline_seconds", 180.0) or 180.0
        )

        def _error_bytes(
            message: str,
            *,
            code: str,
            status: int = 502,
            retry_after: str | None = None,
        ) -> bytes:
            return frame_sse_error(
                message, code=code, status=status, retry_after=retry_after
            )

        for idx, model in enumerate(chain):
            remaining = deadline - time.monotonic()
            if remaining < 3.0 and idx > 0:
                last_status, last_model = 504, model
                saw_deadline = True
                logger.warning(
                    "stream request deadline exceeded before model %s (%.1fs left)",
                    model,
                    remaining,
                )
                break
            pid = self._provider_id_for(model)
            try:
                client, upstream_mid = self._client_for(model)
            except RuntimeError as exc:
                self._circuit_fail(pid)
                if idx < len(chain) - 1:
                    self.stats.fallback_advances += 1
                    logger.info("stream client_for failed on %s: %s; advancing", model, exc)
                    continue
                last_status, last_model, last_pid = 503, model, pid

                async def fail_client(
                    msg: str = str(exc),
                ) -> AsyncIterator[bytes]:
                    yield _error_bytes(msg, code="nimmakai_provider_unavailable", status=503)

                return StreamResult(
                    status_code=503,
                    byte_iter=fail_client(),
                    headers={"content-type": "text/event-stream"},
                    key=None,
                    model=model,
                    fallback_index=idx,
                    decision=decision,
                    provider_id=pid,
                )
            attempt_body = {**body, "model": upstream_mid}
            t_attempt = time.perf_counter()
            try:
                import asyncio as _aio

                attempt_budget = max(1.0, min(remaining, 45.0))
                status, byte_iter, headers, key = await _aio.wait_for(
                    client.stream(
                        "POST",
                        path,
                        json_body=attempt_body,
                        forward_headers=forward_headers,
                        preferred_key_id=preferred_key_id,
                        max_retries=2,
                    ),
                    timeout=attempt_budget,
                )
            except TimeoutError:
                self._circuit_fail(pid)
                self._emit_span(
                    self._make_upstream_span(
                        model=model,
                        t0=t_attempt,
                        status=504,
                        success=False,
                        error_message="attempt_deadline_exceeded",
                        span_type="fallback_advance"
                        if idx < len(chain) - 1
                        else "upstream",
                    )
                )
                if idx < len(chain) - 1:
                    self.stats.fallback_advances += 1
                    await sleep_backoff(
                        idx,
                        base=self.settings.retry_backoff_base_seconds,
                        cap=min(2.0, self.settings.retry_backoff_cap_seconds),
                    )
                    logger.info("stream attempt deadline on %s; advancing", model)
                    continue
                last_status, last_model, last_pid = 504, model, pid
                saw_deadline = True
                break
            except (RuntimeError, httpx.HTTPError, OSError) as exc:
                self._circuit_fail(pid)
                self._emit_span(
                    self._make_upstream_span(
                        model=model,
                        t0=t_attempt,
                        status=503,
                        success=False,
                        error_message=str(exc),
                        span_type="fallback_advance"
                        if idx < len(chain) - 1
                        else "upstream",
                    )
                )
                if idx < len(chain) - 1:
                    self.stats.fallback_advances += 1
                    await sleep_backoff(
                        idx,
                        base=self.settings.retry_backoff_base_seconds,
                        cap=min(2.0, self.settings.retry_backoff_cap_seconds),
                    )
                    logger.info("stream pool/transport on %s: %s; advancing", model, exc)
                    continue
                last_status, last_model, last_pid = 503, model, pid

                async def fail_transport(
                    msg: str = str(exc),
                ) -> AsyncIterator[bytes]:
                    yield _error_bytes(msg, code="nimmakai_pool_exhausted", status=503)

                return StreamResult(
                    status_code=503,
                    byte_iter=fail_transport(),
                    headers={"content-type": "text/event-stream"},
                    key=None,
                    model=model,
                    fallback_index=idx,
                    decision=decision,
                    provider_id=pid,
                )

            last_status, last_headers, last_key, last_model, last_pid = (
                status,
                headers,
                key,
                model,
                pid,
            )

            if 200 <= status < 300:
                import asyncio

                ct = (headers.get("content-type") or headers.get("Content-Type") or "").lower()
                # Upstream ignored stream:true and returned JSON — convert to SSE (F-18)
                if "application/json" in ct and "text/event-stream" not in ct:
                    raw_parts: list[bytes] = []
                    try:
                        async for chunk in byte_iter:
                            raw_parts.append(chunk)
                            if sum(len(c) for c in raw_parts) > 2_000_000:
                                break
                    except Exception:
                        pass
                    if hasattr(byte_iter, "aclose"):
                        with suppress(Exception):
                            await byte_iter.aclose()
                    raw = b"".join(raw_parts)
                    try:
                        parsed = _json.loads(raw.decode("utf-8", errors="replace"))
                    except Exception:
                        parsed = raw.decode("utf-8", errors="replace")
                    sse_payload = json_body_to_sse(parsed, routed_model=model)
                    self._circuit_succeed(pid)
                    self.stats.record(decision.intent.value, model, advanced=idx > 0)

                    async def json_as_sse(p: bytes = sse_payload) -> AsyncIterator[bytes]:
                        yield p

                    return StreamResult(
                        status_code=200,
                        byte_iter=json_as_sse(),
                        headers={**headers, "content-type": "text/event-stream"},
                        key=key,
                        model=model,
                        fallback_index=idx,
                        decision=decision,
                        provider_id=pid,
                    )

                ttft = float(
                    getattr(self.settings, "stream_ttft_timeout_seconds", 12.0) or 12.0
                )
                # Adaptive TTFT: fast models fail over faster (NMK-405)
                h = self.registry.health._by_model.get(model)
                if h is not None and h.ewma_latency > 0:
                    base_ttft = h.ewma_latency * 2.0 + 3.0
                    ttft = min(ttft, max(3.0, base_ttft))
                idle = float(
                    getattr(self.settings, "stream_idle_timeout_seconds", 180.0) or 180.0
                )
                t_stream0 = time.monotonic()
                try:
                    first_chunk = await asyncio.wait_for(anext(byte_iter), timeout=ttft)
                except StopAsyncIteration:
                    # Empty stream body — treat as soft-fail and try next model
                    first_chunk = b""
                    self._circuit_fail(pid)
                    self._emit_span(
                        self._make_upstream_span(
                            model=model,
                            t0=t_attempt,
                            status=502,
                            success=False,
                            error_message="empty_stream",
                            span_type="fallback_advance"
                            if idx < len(chain) - 1
                            else "upstream",
                        )
                    )
                    if hasattr(byte_iter, "aclose"):
                        with suppress(Exception):
                            await byte_iter.aclose()
                    self.stats.fallback_advances += 1
                    self.registry.record_outcome(
                        model,
                        key.key_id if key else None,
                        success=False,
                        status_code=502,
                        intent=decision.intent.value,
                    )
                    if idx < len(chain) - 1:
                        logger.warning(
                            "Empty stream body on %s; falling back", model
                        )
                        continue
                    # Last model: empty stream → terminal error, not 200.
                    # Must continue so we do not fall into the success path.
                    last_status, last_model, last_pid = 502, model, pid
                    logger.warning(
                        "Empty stream body on last model %s; returning 502", model
                    )
                    continue
                except TimeoutError:
                    saw_ttft_stall = True
                    last_status = 504
                    logger.warning(
                        "Stream TTFT stalled on %s after %.0fs; falling back",
                        model,
                        ttft,
                    )
                    self._circuit_fail(pid)
                    self._emit_span(
                        self._make_upstream_span(
                            model=model,
                            t0=t_attempt,
                            status=504,
                            success=False,
                            error_message=f"ttft_timeout_{ttft:.0f}s",
                            span_type="fallback_advance",
                            metadata={"ttft_timeout_s": ttft},
                        )
                    )
                    if hasattr(byte_iter, "aclose"):
                        with suppress(Exception):
                            await byte_iter.aclose()
                    self.stats.fallback_advances += 1
                    self.registry.record_outcome(
                        model,
                        key.key_id if key else None,
                        success=False,
                        latency=ttft,
                        status_code=504,
                        intent=decision.intent.value,
                    )
                    await sleep_backoff(
                        idx,
                        base=self.settings.retry_backoff_base_seconds,
                        cap=min(2.0, self.settings.retry_backoff_cap_seconds),
                    )
                    continue
                except Exception as exc:
                    last_status = 502
                    logger.warning(
                        "Stream open failed on %s: %s; falling back", model, exc
                    )
                    self._circuit_fail(pid)
                    self._emit_span(
                        self._make_upstream_span(
                            model=model,
                            t0=t_attempt,
                            status=502,
                            success=False,
                            error_message=str(exc),
                            span_type="fallback_advance",
                        )
                    )
                    if hasattr(byte_iter, "aclose"):
                        with suppress(Exception):
                            await byte_iter.aclose()
                    self.stats.fallback_advances += 1
                    self.registry.record_outcome(
                        model,
                        key.key_id if key else None,
                        success=False,
                        status_code=502,
                        intent=decision.intent.value,
                    )
                    await sleep_backoff(
                        idx,
                        base=self.settings.retry_backoff_base_seconds,
                        cap=min(2.0, self.settings.retry_backoff_cap_seconds),
                    )
                    continue

                ttft_latency = max(0.01, time.monotonic() - t_stream0)
                self._circuit_succeed(pid)
                self._emit_span(
                    self._make_upstream_span(
                        model=model,
                        t0=t_attempt,
                        status=status,
                        success=True,
                        metadata={"ttft_ms": ttft_latency * 1000, "stream": True},
                    )
                )
                self.stats.record(decision.intent.value, model, advanced=idx > 0)
                # Adaptive: first-token latency feeds speed score immediately
                self.registry.record_outcome(
                    model,
                    key.key_id if key else None,
                    success=True,
                    latency=ttft_latency,
                    status_code=status,
                    intent=decision.intent.value,
                    had_tools=bool(body.get("tools") or body.get("functions")),
                )

                # Bind loop vars so the generator does not close over the last iteration
                bound_model = model
                bound_key_id = key.key_id if key else None
                bound_idle = idle
                bound_t0 = t_stream0
                bound_ttft_ms = ttft_latency * 1000
                usage_bag: dict[str, int] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cached_tokens": 0,
                }
                # Holder so robust_iter can flip stream_failed after StreamResult exists
                result_holder: dict[str, StreamResult | None] = {"r": None}

                async def robust_iter(
                    first: bytes,
                    rest: AsyncIterator[bytes],
                    *,
                    mid: str = bound_model,
                    kid: str | None = bound_key_id,
                    idle_s: float = bound_idle,
                    t0: float = bound_t0,
                    usage: dict[str, int] = usage_bag,
                ) -> AsyncIterator[bytes]:
                    total_tokens = 0
                    # Bounded queue for backpressure — blocks upstream when client is slow
                    _BP_QUEUE_SIZE = 32
                    _bp_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_BP_QUEUE_SIZE)
                    _upstream_done = False
                    _upstream_error: Exception | None = None

                    async def _producer() -> None:
                        """Read from upstream and put into bounded queue."""
                        nonlocal _upstream_done, _upstream_error, total_tokens
                        try:
                            async for chunk in rest:
                                _scan_for_tokens(chunk)
                                await _bp_queue.put(chunk)
                        except Exception as e:
                            _upstream_error = e
                        finally:
                            _upstream_done = True
                            with suppress(Exception):
                                await _bp_queue.put(None)  # sentinel

                    def _scan_for_tokens(c: bytes) -> None:
                        nonlocal total_tokens
                        if b'"usage"' in c or b"completion_tokens" in c:
                            import re

                            p = re.search(rb'"prompt_tokens"\s*:\s*(\d+)', c)
                            ct = re.search(rb'"completion_tokens"\s*:\s*(\d+)', c)
                            if p and ct:
                                pt_i, ct_i = int(p.group(1)), int(ct.group(1))
                                total_tokens = pt_i + ct_i
                                usage["prompt_tokens"] = pt_i
                                usage["completion_tokens"] = ct_i
                                self.stats.record_tokens(mid, kid, pt_i, ct_i)

                    async def _emit_stream_error(err_msg: str, *, code: str) -> AsyncIterator[bytes]:
                        finish = {
                            "id": "nimmakai-stream-error",
                            "object": "chat.completion.chunk",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "error",
                                }
                            ],
                        }
                        err_evt = openai_error(
                            err_msg,
                            code=code,
                            type_="server_error",
                        )
                        yield (
                            b"data: "
                            + _json.dumps(finish).encode("utf-8")
                            + b"\n\n"
                        )
                        yield (
                            b"data: "
                            + _json.dumps(err_evt).encode("utf-8")
                            + b"\n\n"
                        )
                        yield b"data: [DONE]\n\n"

                    def _mark_failed() -> None:
                        held = result_holder["r"]
                        if held is not None:
                            held.stream_failed = True
                        self._circuit_fail(pid)

                    def _cancel_producer() -> None:
                        if not producer_task.done():
                            producer_task.cancel()

                    async def _await_producer() -> None:
                        try:
                            await producer_task
                        except (asyncio.CancelledError, Exception):
                            pass

                    # Start producer task
                    producer_task = asyncio.create_task(_producer())
                    try:
                        if first:
                            _scan_for_tokens(first)
                            yield first
                        while True:
                            try:
                                chunk = await asyncio.wait_for(
                                    _bp_queue.get(), timeout=idle_s
                                )
                            except TimeoutError:
                                logger.warning(
                                    "Stream idle timeout on %s after %.0fs — emitting error SSE",
                                    mid,
                                    idle_s,
                                )
                                _cancel_producer()
                                await _await_producer()
                                if hasattr(rest, "aclose"):
                                    with suppress(Exception):
                                        await rest.aclose()
                                elapsed = max(0.01, time.monotonic() - t0)
                                self.registry.record_outcome(
                                    mid,
                                    kid,
                                    success=False,
                                    latency=elapsed,
                                    tokens=total_tokens or None,
                                    status_code=504,
                                )
                                _mark_failed()
                                async for err_chunk in _emit_stream_error(
                                    f"Stream idle timeout after {idle_s:.0f}s",
                                    code="upstream_stream_idle",
                                ):
                                    yield err_chunk
                                return
                            if chunk is None:  # sentinel from producer
                                break
                            yield chunk
                        # Producer done — check for errors
                        if _upstream_error and not isinstance(
                            _upstream_error, StopAsyncIteration
                        ):
                            raise _upstream_error
                        # Full stream done — update speed with total time + tokens
                        elapsed = max(0.01, time.monotonic() - t0)
                        if total_tokens > 0:
                            self.registry.record_outcome(
                                mid,
                                kid,
                                success=True,
                                latency=elapsed,
                                tokens=total_tokens,
                                status_code=200,
                            )
                        return
                    except (asyncio.CancelledError, GeneratorExit):
                        _cancel_producer()
                        await _await_producer()
                        raise
                    except Exception as e:
                        _cancel_producer()
                        await _await_producer()
                        logger.warning(
                            "Stream ended early on %s: %s — closing SSE with error",
                            mid,
                            e,
                        )
                        _mark_failed()
                        # finish_reason=error chunk + error event before [DONE] (F-17)
                        try:
                            async for err_chunk in _emit_stream_error(
                                str(e)[:500],
                                code="upstream_stream_error",
                            ):
                                yield err_chunk
                        except Exception:
                            pass
                        return
                    finally:
                        _cancel_producer()
                        await _await_producer()

                stream_result = StreamResult(
                    status_code=status,
                    byte_iter=robust_iter(first_chunk, byte_iter),
                    headers=headers,
                    key=key,
                    model=model,
                    fallback_index=idx,
                    decision=decision,
                    upstream_ttft_ms=bound_ttft_ms,
                    usage=usage_bag,
                    prompt_tokens=usage_bag["prompt_tokens"],
                    completion_tokens=usage_bag["completion_tokens"],
                    cached_tokens=usage_bag["cached_tokens"],
                    provider_id=pid,
                    stream_failed=False,
                )
                result_holder["r"] = stream_result
                return stream_result

            # Failed stream open — advance on same retryable set as JSON path
            if status >= 500:
                self._circuit_fail(pid)
            err_raw = b""
            try:
                async for chunk in byte_iter:
                    err_raw += chunk
                    if len(err_raw) > 8192:
                        break
            except Exception:
                pass

            err_body: Any = None
            if err_raw:
                try:
                    err_body = _json.loads(err_raw.decode("utf-8", errors="replace"))
                except Exception:
                    err_body = err_raw.decode("utf-8", errors="replace")
            else:
                ra = headers.get("Retry-After") or headers.get("retry-after")
                err_body = wrap_upstream_error(
                    f"Upstream error HTTP {status}", status=status
                )
                if ra:
                    err_body = openai_error(
                        f"Upstream error HTTP {status}",
                        code="upstream_error",
                        type_=(
                            "server_error"
                            if status >= 500 or status == 429
                            else "invalid_request_error"
                        ),
                        metadata={"retry_after": ra},
                    )
                err_raw = _json.dumps(err_body).encode("utf-8")

            # Always normalize non-empty upstream error bodies to OpenAI envelope
            if err_body is not None:
                wrapped = wrap_upstream_error(err_body, status=status)
                err_raw = (
                    b"data: "
                    + _json.dumps(wrapped).encode("utf-8")
                    + b"\n\ndata: [DONE]\n\n"
                )
            else:
                err_raw = frame_sse_error(
                    f"Upstream error HTTP {status}",
                    code="upstream_error",
                    status=status,
                )

            retryable = status in {401, 403, 404, 405, 408, 429, 500, 502, 503, 504} or (
                status == 400 and _is_retryable_model_error(status, err_body)
            )
            self.registry.record_outcome(
                model,
                key.key_id if key else None,
                success=False,
                status_code=status,
                unavailable=status == 404,
                intent=decision.intent.value,
            )
            if retryable and idx < len(chain) - 1:
                # 504 = gateway timeout — advance immediately, no backoff
                if status == 429:
                    ra = parse_retry_after(
                        headers.get("Retry-After") or headers.get("retry-after")
                    )
                    await sleep_backoff(
                        idx,
                        base=self.settings.retry_backoff_base_seconds,
                        cap=self.settings.retry_backoff_cap_seconds,
                        retry_after=ra,
                    )
                elif status in {500, 502, 503}:
                    await sleep_backoff(
                        idx,
                        base=self.settings.retry_backoff_base_seconds,
                        cap=min(1.0, self.settings.retry_backoff_cap_seconds),
                    )
                self.stats.fallback_advances += 1
                logger.info(
                    "stream model %s failed status=%s; falling back",
                    model,
                    status,
                )
                continue

            async def err_bytes(payload: bytes = err_raw) -> AsyncIterator[bytes]:
                yield payload

            self.stats.record(decision.intent.value, model, advanced=idx > 0)
            return StreamResult(
                status_code=status,
                byte_iter=err_bytes(),
                headers={**headers, "content-type": "text/event-stream"},
                key=key,
                model=model,
                fallback_index=idx,
                decision=decision,
                provider_id=pid,
            )

        # ── Last-resort: clear cooldowns, force-allow providers, retry fresh models ──
        remaining = deadline - time.monotonic()
        if remaining >= 5.0 and last_status >= 400:
            if hasattr(self.registry, "health"):
                for h in self.registry.health._by_model.values():
                    h.cooldown_until = 0.0
            if self.hub is not None:
                for pid_r in self.hub.provider_ids:
                    self.hub.circuit_breaker.force_allow(pid_r)
            retry_chain = self._chain(decision, had_tools=had_tools)
            if not retry_chain:
                from nimmakai.resilience import emergency_coding_chain

                retry_chain = emergency_coding_chain(
                    self.registry,
                    max_n=int(
                        getattr(self.settings, "coding_max_fallbacks", 12) or 12
                    ),
                )
            tried = set(chain)
            fresh = [m for m in (retry_chain or []) if m not in tried]
            if fresh:
                import asyncio as _aio_s

                logger.warning(
                    "stream last-resort: retrying %s previously-cooled models",
                    len(fresh),
                )
                for idx2, model2 in enumerate(fresh):
                    rem2 = deadline - time.monotonic()
                    if rem2 < 3.0:
                        saw_deadline = True
                        break
                    pid2 = self._provider_id_for(model2)
                    try:
                        client2, upstream_mid2 = self._client_for(model2)
                    except RuntimeError:
                        self._circuit_fail(pid2)
                        continue
                    attempt_body2 = {**body, "model": upstream_mid2}
                    t2 = time.perf_counter()
                    try:
                        budget2 = max(1.0, min(rem2, 45.0))
                        s2, byte_iter2, hd2, k2 = await _aio_s.wait_for(
                            client2.stream(
                                "POST", path,
                                json_body=attempt_body2,
                                forward_headers=forward_headers,
                                preferred_key_id=preferred_key_id,
                                max_retries=2,
                            ),
                            timeout=budget2,
                        )
                    except (TimeoutError, RuntimeError, httpx.HTTPError, OSError):
                        self._circuit_fail(pid2)
                        self.registry.record_outcome(
                            model2, None, success=False,
                            status_code=503, intent=decision.intent.value,
                        )
                        continue

                    if 200 <= s2 < 300:
                        ct2 = (
                            hd2.get("content-type") or hd2.get("Content-Type") or ""
                        ).lower()
                        if "application/json" in ct2 and "text/event-stream" not in ct2:
                            raw_parts: list[bytes] = []
                            try:
                                async for chunk in byte_iter2:
                                    raw_parts.append(chunk)
                                    if sum(len(c) for c in raw_parts) > 2_000_000:
                                        break
                            except Exception:
                                pass
                            if hasattr(byte_iter2, "aclose"):
                                with suppress(Exception):
                                    await byte_iter2.aclose()
                            raw = b"".join(raw_parts)
                            try:
                                parsed = _json.loads(raw.decode("utf-8", errors="replace"))
                            except Exception:
                                parsed = raw.decode("utf-8", errors="replace")
                            sse_payload = json_body_to_sse(
                                parsed, routed_model=model2
                            )
                            self._circuit_succeed(pid2)
                            self.stats.record(
                                decision.intent.value, model2, advanced=True
                            )

                            async def json_as_sse2(
                                p: bytes = sse_payload,
                            ) -> AsyncIterator[bytes]:
                                yield p

                            return StreamResult(
                                status_code=200,
                                byte_iter=json_as_sse2(),
                                headers={
                                    **hd2, "content-type": "text/event-stream"
                                },
                                key=k2, model=model2,
                                fallback_index=len(chain) + idx2,
                                decision=decision, provider_id=pid2,
                            )

                        ttft2 = float(
                            getattr(
                                self.settings, "stream_ttft_timeout_seconds", 12.0
                            )
                            or 12.0
                        )
                        idle2 = float(
                            getattr(
                                self.settings, "stream_idle_timeout_seconds", 180.0
                            )
                            or 180.0
                        )
                        t_stream2 = time.monotonic()
                        try:
                            first_chunk2 = await _aio_s.wait_for(
                                anext(byte_iter2), timeout=ttft2
                            )
                        except (StopAsyncIteration, TimeoutError, Exception):
                            self._circuit_fail(pid2)
                            if hasattr(byte_iter2, "aclose"):
                                with suppress(Exception):
                                    await byte_iter2.aclose()
                            self.registry.record_outcome(
                                model2, k2.key_id if k2 else None,
                                success=False, status_code=504,
                                intent=decision.intent.value,
                            )
                            continue

                        # Stream opened successfully — return it
                        self._circuit_succeed(pid2)
                        ttft_lat2 = max(0.01, time.monotonic() - t_stream2)
                        self.registry.record_outcome(
                            model2, k2.key_id if k2 else None,
                            success=True, latency=ttft_lat2,
                            status_code=s2, intent=decision.intent.value,
                            had_tools=had_tools,
                        )
                        self.stats.record(
                            decision.intent.value, model2, advanced=True
                        )
                        bound_model2 = model2
                        bound_key_id2 = k2.key_id if k2 else None
                        bound_idle2 = idle2
                        bound_t02 = t_stream2
                        bound_ttft_ms2 = ttft_lat2 * 1000
                        usage_bag2: dict[str, int] = {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "cached_tokens": 0,
                        }
                        result_holder2: dict[str, StreamResult | None] = {
                            "r": None
                        }

                        async def robust_iter2(
                            first: bytes,
                            rest: AsyncIterator[bytes],
                            *,
                            mid: str = bound_model2,
                            kid: str | None = bound_key_id2,
                            idle_s: float = bound_idle2,
                            t0: float = bound_t02,
                            usage: dict[str, int] = usage_bag2,
                        ) -> AsyncIterator[bytes]:
                            total_tokens = 0
                            _BP = 32
                            _bpq: _aio_s.Queue[bytes | None] = _aio_s.Queue(
                                maxsize=_BP
                            )
                            _up_done = False
                            _up_err: Exception | None = None

                            async def _prod() -> None:
                                nonlocal _up_done, _up_err, total_tokens
                                try:
                                    async for c in rest:
                                        _scan2(c)
                                        await _bpq.put(c)
                                except Exception as e:
                                    _up_err = e
                                finally:
                                    _up_done = True
                                    with suppress(Exception):
                                        await _bpq.put(None)

                            def _scan2(c: bytes) -> None:
                                nonlocal total_tokens
                                if b'"usage"' in c or b"completion_tokens" in c:
                                    import re

                                    p = re.search(
                                        rb'"prompt_tokens"\s*:\s*(\d+)', c
                                    )
                                    ct = re.search(
                                        rb'"completion_tokens"\s*:\s*(\d+)', c
                                    )
                                    if p and ct:
                                        pt_i, ct_i = int(p.group(1)), int(
                                            ct.group(1)
                                        )
                                        total_tokens = pt_i + ct_i
                                        usage["prompt_tokens"] = pt_i
                                        usage["completion_tokens"] = ct_i
                                        self.stats.record_tokens(
                                            mid, kid, pt_i, ct_i
                                        )

                            prod_task = _aio_s.create_task(_prod())
                            try:
                                if first:
                                    _scan2(first)
                                    yield first
                                while True:
                                    try:
                                        chunk = await _aio_s.wait_for(
                                            _bpq.get(), timeout=idle_s
                                        )
                                    except TimeoutError:
                                        prod_task.cancel()
                                        with suppress(Exception):
                                            await prod_task
                                        if hasattr(rest, "aclose"):
                                            with suppress(Exception):
                                                await rest.aclose()
                                        elapsed = max(
                                            0.01, time.monotonic() - t0
                                        )
                                        self.registry.record_outcome(
                                            mid, kid, success=False,
                                            latency=elapsed,
                                            tokens=total_tokens or None,
                                            status_code=504,
                                        )
                                        held = result_holder2["r"]
                                        if held is not None:
                                            held.stream_failed = True
                                        self._circuit_fail(pid2)
                                        finish = {
                                            "id": "nimmakai-stream-error",
                                            "object": "chat.completion.chunk",
                                            "choices": [
                                                {
                                                    "index": 0,
                                                    "delta": {},
                                                    "finish_reason": "error",
                                                }
                                            ],
                                        }
                                        err_evt = openai_error(
                                            f"Stream idle timeout after {idle_s:.0f}s",
                                            code="upstream_stream_idle",
                                            type_="server_error",
                                        )
                                        yield (
                                            b"data: "
                                            + _json.dumps(finish).encode("utf-8")
                                            + b"\n\n"
                                        )
                                        yield (
                                            b"data: "
                                            + _json.dumps(err_evt).encode("utf-8")
                                            + b"\n\n"
                                        )
                                        yield b"data: [DONE]\n\n"
                                        return
                                    if chunk is None:
                                        break
                                    yield chunk
                                if _up_err and not isinstance(
                                    _up_err, StopAsyncIteration
                                ):
                                    raise _up_err
                                elapsed = max(0.01, time.monotonic() - t0)
                                if total_tokens > 0:
                                    self.registry.record_outcome(
                                        mid, kid, success=True,
                                        latency=elapsed, tokens=total_tokens,
                                        status_code=200,
                                    )
                                return
                            except (_aio_s.CancelledError, GeneratorExit):
                                prod_task.cancel()
                                with suppress(Exception):
                                    await prod_task
                                raise
                            except Exception as e:
                                prod_task.cancel()
                                with suppress(Exception):
                                    await prod_task
                                held = result_holder2["r"]
                                if held is not None:
                                    held.stream_failed = True
                                self._circuit_fail(pid2)
                                try:
                                    finish = {
                                        "id": "nimmakai-stream-error",
                                        "object": "chat.completion.chunk",
                                        "choices": [
                                            {
                                                "index": 0,
                                                "delta": {},
                                                "finish_reason": "error",
                                            }
                                        ],
                                    }
                                    err_evt = openai_error(
                                        str(e)[:500],
                                        code="upstream_stream_error",
                                        type_="server_error",
                                    )
                                    yield (
                                        b"data: "
                                        + _json.dumps(finish).encode("utf-8")
                                        + b"\n\n"
                                    )
                                    yield (
                                        b"data: "
                                        + _json.dumps(err_evt).encode("utf-8")
                                        + b"\n\n"
                                    )
                                    yield b"data: [DONE]\n\n"
                                except Exception:
                                    pass
                                return
                            finally:
                                prod_task.cancel()
                                with suppress(Exception):
                                    await prod_task

                        stream_result2 = StreamResult(
                            status_code=s2,
                            byte_iter=robust_iter2(first_chunk2, byte_iter2),
                            headers=hd2,
                            key=k2,
                            model=model2,
                            fallback_index=len(chain) + idx2,
                            decision=decision,
                            upstream_ttft_ms=bound_ttft_ms2,
                            usage=usage_bag2,
                            provider_id=pid2,
                            stream_failed=False,
                        )
                        result_holder2["r"] = stream_result2
                        return stream_result2

                    # Non-2xx stream response
                    if s2 >= 500:
                        self._circuit_fail(pid2)
                    err_raw2 = b""
                    try:
                        async for chunk in byte_iter2:
                            err_raw2 += chunk
                            if len(err_raw2) > 8192:
                                break
                    except Exception:
                        pass
                    self.registry.record_outcome(
                        model2, k2.key_id if k2 else None,
                        success=False, status_code=s2,
                        intent=decision.intent.value,
                    )
                    last_status, last_model, last_pid = s2, model2, pid2

        # No stream successfully relayed — never return 2xx with empty body (F-05)
        if saw_deadline:
            terminal_status = 504
            code = "request_deadline_exceeded"
            msg = "Request deadline exceeded before a stream could be opened."
        elif saw_ttft_stall or last_status < 400:
            terminal_status = 504
            code = "upstream_timeout"
            msg = "All models timed out waiting for the first stream token."
        else:
            terminal_status = last_status if last_status >= 400 else 504
            code = "nimmakai_models_exhausted"
            msg = "All models in routing chain failed to open a stream."
        if terminal_status < 400:
            terminal_status = 504
        payload = _error_bytes(msg, code=code, status=terminal_status)

        async def empty_fail(p: bytes = payload) -> AsyncIterator[bytes]:
            yield p

        return StreamResult(
            status_code=terminal_status,
            byte_iter=empty_fail(),
            headers={"content-type": "text/event-stream"},
            key=last_key,
            model=last_model,
            fallback_index=max(0, len(chain) - 1),
            decision=decision,
            provider_id=last_pid,
        )
