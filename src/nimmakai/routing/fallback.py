"""Ordered model fallback execution (separate from key rotation)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nimmakai.routing.selector import RouteDecision

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
class RoutingStats:
    intents_total: dict[str, int] = field(default_factory=dict)
    models_total: dict[str, int] = field(default_factory=dict)
    fallback_advances: int = 0

    def record(self, intent: str, model: str, advanced: bool) -> None:
        self.intents_total[intent] = self.intents_total.get(intent, 0) + 1
        self.models_total[model] = self.models_total.get(model, 0) + 1
        if advanced:
            self.fallback_advances += 1


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
    return False


def _is_non_retryable_client_error(status: int, body: Any) -> bool:
    if status in {400, 401, 403, 422}:
        return not _is_retryable_model_error(status, body)
    return False


class FallbackExecutor:
    def __init__(
        self,
        upstream: UpstreamClient,
        registry: ModelRegistry,
        settings: Settings,
        stats: RoutingStats | None = None,
    ) -> None:
        self.upstream = upstream
        self.registry = registry
        self.settings = settings
        self.stats = stats or RoutingStats()

    def _chain(self, decision: RouteDecision) -> list[str]:
        max_n = self.settings.max_model_fallbacks
        chain = list(decision.chain)[: max(1, max_n)]
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
            attempt_body = {**body, "model": model}
            try:
                status, resp_body, headers, key = await self.upstream.request_json(
                    "POST",
                    path,
                    json_body=attempt_body,
                    forward_headers=forward_headers,
                    preferred_key_id=preferred_key_id,
                )
            except RuntimeError as exc:
                msg = str(exc).lower()
                if "rate-limited" in msg or "cooling" in msg or "unavailable" in msg:
                    if advance_on_pool and idx < len(chain) - 1:
                        self.stats.fallback_advances += 1
                        logger.info("pool exhausted on %s; advancing model", model)
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
            self.registry.record_outcome(
                model,
                key_id,
                success=success,
                status_code=status,
                unavailable=unavailable,
            )

            if success:
                if isinstance(resp_body, dict) and "model" in resp_body:
                    resp_body = {**resp_body, "model": model}
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
            async def empty() -> AsyncIterator[bytes]:
                if False:  # pragma: no cover
                    yield b""
                return

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
            attempt_body = {**body, "model": model}
            try:
                status, byte_iter, headers, key = await self.upstream.stream(
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
                self.stats.record(decision.intent.value, model, advanced=idx > 0)
                # Wrap iterator to record health on completion — release already in upstream
                return StreamResult(
                    status_code=status,
                    byte_iter=byte_iter,
                    headers=headers,
                    key=key,
                    model=model,
                    fallback_index=idx,
                    decision=decision,
                )

            # Consume/close failed stream attempt (upstream already released on 429 empty)
            if status == 429 or status >= 500 or status == 404:
                # Drain empty iterators
                try:
                    async for _ in byte_iter:
                        pass
                except Exception:
                    pass
                self.registry.record_outcome(
                    model,
                    key.key_id if key else None,
                    success=False,
                    status_code=status,
                    unavailable=status == 404,
                )
                if idx < len(chain) - 1:
                    self.stats.fallback_advances += 1
                    continue

            # Non-retryable or last attempt — return error stream as-is
            self.stats.record(decision.intent.value, model, advanced=idx > 0)
            return StreamResult(
                status_code=status,
                byte_iter=byte_iter,
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
