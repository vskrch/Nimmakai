"""Ordered model fallback execution (separate from key rotation)."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nimmakai.routing.selector import RouteDecision
from nimmakai.safety.backoff import sleep_backoff
from nimmakai.upstream import parse_retry_after

if TYPE_CHECKING:
    from nimmakai.balancer import KeyStats
    from nimmakai.catalog.registry import ModelRegistry
    from nimmakai.config import Settings
    from nimmakai.upstream import UpstreamClient

logger = logging.getLogger(__name__)


@dataclass
class UpstreamResult:
    status_code: int
    body: Any
    headers: dict[str, str]
    key: KeyStats | None
    model: str
    fallback_index: int
    decision: RouteDecision


@dataclass
class StreamResult:
    status_code: int
    byte_iter: AsyncIterator[bytes]
    headers: dict[str, str]
    key: KeyStats | None
    model: str
    fallback_index: int
    decision: RouteDecision


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

    def record(self, intent: str, model: str, advanced: bool) -> None:
        self.intents_total[intent] = self.intents_total.get(intent, 0) + 1
        self.models_total[model] = self.models_total.get(model, 0) + 1
        if advanced:
            self.fallback_advances += 1

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
    if status in {500, 502, 503, 504}:
        return True
    if _is_model_not_found(status, body):
        return True
    # Tools unsupported → try next model
    if status == 400 and isinstance(body, dict):
        msg = str((body.get("error") or {}).get("message") or "").lower()
        if "tool" in msg and ("not support" in msg or "unsupported" in msg):
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
    if status in {400, 401, 403, 422}:
        return not _is_retryable_model_error(status, body)
    return False


def _analyze_success_body(body: Any, *, had_tools: bool) -> tuple[bool, bool | None]:
    """
    Returns (empty_reply, tool_ok).
    tool_ok is None when tools were not requested.
    """
    if not isinstance(body, dict):
        return False, None if not had_tools else False
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
    ) -> None:
        self.upstream = upstream
        self.registry = registry
        self.settings = settings
        self.stats = stats or RoutingStats()
        self.hub = hub

    def _client_for(self, model: str) -> tuple[Any, str]:
        """Return (upstream_client, upstream_model_id) for this namespaced model."""
        if self.hub is not None:
            client, _pid, upstream_mid = self.hub.client_for_model(model)
            return client, upstream_mid
        return self.upstream, model

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
            return True

    def _chain(self, decision: RouteDecision) -> list[str]:
        max_n = int(getattr(self.settings, "max_model_fallbacks", 10) or 10)
        if decision.intent.value == "coding_agentic":
            max_n = max(
                max_n,
                int(getattr(self.settings, "coding_max_fallbacks", 12) or 12),
            )
        raw = list(decision.chain)
        # Drop models whose provider has no active keys/runtime (production safety)
        available = [m for m in raw if self._provider_available(m)]
        if not available:
            # Self-heal: rebuild emergency chain from live catalog
            try:
                from nimmakai.resilience import emergency_coding_chain

                available = [
                    m
                    for m in emergency_coding_chain(self.registry, max_n=max_n)
                    if self._provider_available(m)
                ]
                if available:
                    logger.warning(
                        "empty chain healed with %s emergency models", len(available)
                    )
            except Exception:
                logger.exception("emergency chain rebuild failed")
        if not available and raw:
            logger.warning(
                "all %s chain models have unavailable providers; keeping raw chain",
                len(raw),
            )
            available = raw
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
        if available:
            from nimmakai.routing.optimizer import optimize_chain

            available = optimize_chain(
                available,
                self.registry,
                intent=intent,
                variant=variant,
                max_n=None,
            )
        # Fail-fast: skip cooling models for TTFT (keep 1 cold last-resort)
        if available and hasattr(self.registry, "health"):
            hot = [m for m in available if not self.registry.health.is_unhealthy(m)]
            cold = [m for m in available if self.registry.health.is_unhealthy(m)]
            available = hot + cold[:1]
        chain = available[: max(1, max_n)]
        return chain

    def routing_headers(
        self,
        decision: RouteDecision,
        *,
        model: str,
        key_id: str | None,
        fallback_index: int,
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
        if self.hub is not None:
            _c, pid, _u = self.hub.client_for_model(model)
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
        chain = self._chain(decision)
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

        for idx, model in enumerate(chain):
            client, upstream_mid = self._client_for(model)
            attempt_body = {**body, "model": upstream_mid}
            try:
                status, resp_body, headers, key = await client.request_json(
                    "POST",
                    path,
                    json_body=attempt_body,
                    forward_headers=forward_headers,
                    preferred_key_id=preferred_key_id,
                )
            except RuntimeError as exc:
                msg = str(exc).lower()
                retryable_pool = (
                    "rate-limited" in msg
                    or "cooling" in msg
                    or "unavailable" in msg
                    or "no api keys" in msg
                    or "not available" in msg
                    or "provider" in msg
                )
                if retryable_pool:
                    if advance_on_pool and idx < len(chain) - 1:
                        self.stats.fallback_advances += 1
                        logger.info(
                            "provider/pool unavailable on %s (%s); advancing model",
                            model,
                            exc,
                        )
                        continue
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
                    )
                raise

            key_id = key.key_id if key else None
            unavailable = _is_model_not_found(status, resp_body)
            success = 200 <= status < 300
            had_tools = bool(
                (body.get("tools") or body.get("functions"))
                or body.get("tool_choice") not in (None, "none", "None")
            )
            empty_reply = False
            tool_ok: bool | None = None
            if success:
                empty_reply, tool_ok = _analyze_success_body(
                    resp_body, had_tools=had_tools
                )
            # Adaptive speed signal: JSON path latency (if measured upstream)
            latency = None
            tokens = None
            if success and isinstance(resp_body, dict):
                usage = resp_body.get("usage")
                if isinstance(usage, dict):
                    pt = int(usage.get("prompt_tokens") or 0)
                    ct = int(usage.get("completion_tokens") or 0)
                    tokens = pt + ct if (pt or ct) else None
            self.registry.record_outcome(
                model,
                key_id,
                success=success,
                latency=latency,
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
                        pt = usage.get("prompt_tokens", 0)
                        ct = usage.get("completion_tokens", 0)
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
                )

            last = UpstreamResult(
                status_code=status,
                body=resp_body,
                headers=headers,
                key=key,
                model=model,
                fallback_index=idx,
                decision=decision,
            )

            if _is_non_retryable_client_error(status, resp_body):
                self.stats.record(decision.intent.value, model, advanced=False)
                return last

            if _is_retryable_model_error(status, resp_body) and idx < len(chain) - 1:
                if status in {429, 500, 502, 503, 504}:
                    ra = parse_retry_after(
                        headers.get("Retry-After") or headers.get("retry-after")
                    )
                    await sleep_backoff(
                        idx,
                        base=self.settings.retry_backoff_base_seconds,
                        cap=self.settings.retry_backoff_cap_seconds,
                        retry_after=ra if status == 429 else None,
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
        chain = self._chain(decision)
        if not chain:
            payload = (
                b'{"error":{"message":"No models available in routing chain. '
                b'Add provider API keys and refresh the catalog.",'
                b'"type":"server_error","code":"nimmakai_catalog_empty"}}'
            )

            async def empty() -> AsyncIterator[bytes]:
                yield payload

            return StreamResult(
                status_code=503,
                byte_iter=empty(),
                headers={"content-type": "application/json"},
                key=None,
                model="",
                fallback_index=0,
                decision=decision,
            )

        last_status = 503
        last_headers: dict[str, str] = {}
        last_key = None
        last_model = chain[0]

        for idx, model in enumerate(chain):
            client, upstream_mid = self._client_for(model)
            attempt_body = {**body, "model": upstream_mid}
            try:
                status, byte_iter, headers, key = await client.stream(
                    "POST",
                    path,
                    json_body=attempt_body,
                    forward_headers=forward_headers,
                    preferred_key_id=preferred_key_id,
                )
            except RuntimeError as exc:
                if idx < len(chain) - 1:
                    self.stats.fallback_advances += 1
                    logger.info("stream pool/error on %s: %s; advancing", model, exc)
                    continue
                raise

            last_status, last_headers, last_key, last_model = status, headers, key, model

            if 200 <= status < 300:
                import asyncio

                ttft = float(
                    getattr(self.settings, "stream_ttft_timeout_seconds", 12.0) or 12.0
                )
                idle = float(
                    getattr(self.settings, "stream_idle_timeout_seconds", 180.0) or 180.0
                )
                t_stream0 = time.monotonic()
                try:
                    first_chunk = await asyncio.wait_for(anext(byte_iter), timeout=ttft)
                except StopAsyncIteration:
                    first_chunk = b""
                except TimeoutError:
                    logger.warning(
                        "Stream TTFT stalled on %s after %.0fs; falling back",
                        model,
                        ttft,
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
                    continue
                except Exception as exc:
                    logger.warning(
                        "Stream open failed on %s: %s; falling back", model, exc
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
                    continue

                ttft_latency = max(0.01, time.monotonic() - t_stream0)
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

                async def robust_iter(
                    first: bytes,
                    rest: AsyncIterator[bytes],
                    *,
                    mid: str = bound_model,
                    kid: str | None = bound_key_id,
                    idle_s: float = bound_idle,
                    t0: float = bound_t0,
                ) -> AsyncIterator[bytes]:
                    total_tokens = 0

                    def _scan_for_tokens(c: bytes) -> None:
                        nonlocal total_tokens
                        if b'"usage"' in c or b"completion_tokens" in c:
                            import re

                            p = re.search(rb'"prompt_tokens"\s*:\s*(\d+)', c)
                            ct = re.search(rb'"completion_tokens"\s*:\s*(\d+)', c)
                            if p and ct:
                                pt_i, ct_i = int(p.group(1)), int(ct.group(1))
                                total_tokens = pt_i + ct_i
                                self.stats.record_tokens(mid, kid, pt_i, ct_i)

                    if first:
                        _scan_for_tokens(first)
                        yield first
                    try:
                        while True:
                            try:
                                chunk = await asyncio.wait_for(
                                    anext(rest), timeout=idle_s
                                )
                            except TimeoutError:
                                logger.warning(
                                    "Stream idle timeout on %s after %.0fs — closing SSE cleanly",
                                    mid,
                                    idle_s,
                                )
                                # Adaptive: slow stream → slight demotion via latency
                                elapsed = max(0.01, time.monotonic() - t0)
                                self.registry.record_outcome(
                                    mid,
                                    kid,
                                    success=True,
                                    latency=elapsed,
                                    tokens=total_tokens or None,
                                    status_code=200,
                                )
                                yield b"data: [DONE]\n\n"
                                return
                            _scan_for_tokens(chunk)
                            yield chunk
                    except StopAsyncIteration:
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
                        raise
                    except Exception as e:
                        logger.warning(
                            "Stream ended early on %s: %s — closing SSE", mid, e
                        )
                        try:
                            yield b"data: [DONE]\n\n"
                        except Exception:
                            pass
                        return

                return StreamResult(
                    status_code=status,
                    byte_iter=robust_iter(first_chunk, byte_iter),
                    headers=headers,
                    key=key,
                    model=model,
                    fallback_index=idx,
                    decision=decision,
                )

            # Failed stream open — advance on same retryable set as JSON path
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
                import json as _json

                try:
                    err_body = _json.loads(err_raw.decode("utf-8", errors="replace"))
                except Exception:
                    err_body = err_raw.decode("utf-8", errors="replace")

            retryable = status in {404, 429, 500, 502, 503, 504} or (
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
                if status in {429, 500, 502, 503, 504}:
                    ra = parse_retry_after(
                        headers.get("Retry-After") or headers.get("retry-after")
                    )
                    await sleep_backoff(
                        idx,
                        base=self.settings.retry_backoff_base_seconds,
                        cap=self.settings.retry_backoff_cap_seconds,
                        retry_after=ra if status == 429 else None,
                    )
                self.stats.fallback_advances += 1
                logger.info(
                    "stream model %s failed status=%s; falling back",
                    model,
                    status,
                )
                continue

            async def err_bytes(payload: bytes = err_raw) -> AsyncIterator[bytes]:
                if payload:
                    yield payload

            self.stats.record(decision.intent.value, model, advanced=idx > 0)
            return StreamResult(
                status_code=status,
                byte_iter=err_bytes(),
                headers=headers,
                key=key,
                model=model,
                fallback_index=idx,
                decision=decision,
            )

        async def empty_fail() -> AsyncIterator[bytes]:
            if False:  # pragma: no cover
                yield b""
            return

        return StreamResult(
            status_code=last_status,
            byte_iter=empty_fail(),
            headers=last_headers or {"content-type": "application/json"},
            key=last_key,
            model=last_model,
            fallback_index=max(0, len(chain) - 1),
            decision=decision,
        )
