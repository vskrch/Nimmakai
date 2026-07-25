"""Pre-Router Interceptor Framework — strictly additive layer before core routing.

When a request hits Potato with ``model="auto"``, the core router resolves it
using internal classification and ladder mechanics. This module implements a
chain of interceptors that run **before** ``ModelSelector.resolve()``.

If an interceptor decides exactly which model to use, it mutates the request
payload from ``model="auto"`` to a specific model ID. The core router then sees
a specific model, bypasses auto-routing, and treats it as a passthrough with
fallback protections. **The core system remains completely unchanged.**

Interceptors (in order):
  1. CustomCatalogInterceptor — admin-mapped intent → model_id overrides
  2. PromptUnderstandingInterceptor — LLM evaluates prompt, picks best model

All interceptors are async, have strict timeouts, and gracefully fall back to
``model="auto"`` on any failure (the core router handles it).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from potato.routing.intents import IntentResult

logger = logging.getLogger(__name__)


class PreRouterInterceptor(Protocol):
    """Protocol: async interceptor that may mutate ``body["model"]``.

    Returns the (possibly mutated) body. Must never raise — on failure,
    return the body unchanged so the core router handles it.
    """

    async def intercept(
        self,
        body: dict[str, Any],
        *,
        intent: IntentResult,
        registry: Any,
    ) -> dict[str, Any]: ...


async def run_interceptor_chain(
    body: dict[str, Any],
    *,
    intent: IntentResult,
    registry: Any,
    interceptors: list[PreRouterInterceptor] | None,
) -> dict[str, Any]:
    """Run interceptors in order. Each gets the output of the previous.

    If any interceptor sets a non-auto model, subsequent interceptors still
    run but typically no-op (they check for ``model="auto"``).
    """
    if not interceptors:
        return body
    current = body
    for interceptor in interceptors:
        try:
            current = await interceptor.intercept(
                current, intent=intent, registry=registry
            )
        except Exception as exc:
            logger.warning(
                "interceptor %s failed: %s — falling back to core router",
                type(interceptor).__name__,
                exc,
            )
    return current


# ---------------------------------------------------------------------------
# Interceptor 1: Custom Catalog Override (NMK-EXT-301/302)
# ---------------------------------------------------------------------------


class CustomCatalogInterceptor:
    """Override ``model="auto"`` with admin-mapped intent → model_id.

    Reads ``custom_catalog_mappings`` from SQLite (meta key).
    If the detected intent has a mapping, mutates ``body["model"]`` to it.
    """

    def __init__(self, db: Any | None = None) -> None:
        self._db = db

    def update_db(self, db: Any) -> None:
        self._db = db

    def _load_mappings(self) -> dict[str, str]:
        if self._db is None:
            return {}
        try:
            raw = self._db.get_meta("custom_catalog_mappings")
            if not raw:
                return {}
            data = json.loads(raw)
            return {str(k): str(v) for k, v in data.items() if v}
        except Exception:
            logger.exception("custom catalog mappings load failed")
            return {}

    async def intercept(
        self,
        body: dict[str, Any],
        *,
        intent: IntentResult,
        registry: Any,
    ) -> dict[str, Any]:
        model = str(body.get("model") or "").strip().lower()
        # Only intercept auto-routed requests
        if model and model not in ("auto", ""):
            return body
        mappings = self._load_mappings()
        if not mappings:
            return body
        intent_key = intent.intent.value
        target = mappings.get(intent_key)
        if not target:
            return body
        # Verify the target model is live and not disabled
        if registry is not None:
            resolved = registry.resolve_live_id(target)
            if resolved is None:
                logger.info(
                    "custom_catalog: mapped model %s for intent %s "
                    "not live — skipping",
                    target,
                    intent_key,
                )
                return body
            target = resolved
        logger.info(
            "custom_catalog: overriding model=auto → %s for intent=%s",
            target,
            intent_key,
        )
        return {**body, "model": target}


# ---------------------------------------------------------------------------
# Interceptor 2: Prompt-Understanding Router (NMK-EXT-401/402/403)
# ---------------------------------------------------------------------------

_UNDERSTANDING_SYSTEM_PROMPT = """\
You are a model routing assistant. Given the user's prompt and a list of available \
models, select the single best model for the job.

Rules:
- Reserve expensive/frontier models (claude-opus, gpt-4o, gemini-2.5-pro) for \
complex reasoning, coding, or multi-step tasks.
- Use fast/cheap models (llama, qwen-small, gemma, nemotron-nano) for simple \
chat, summaries, and straightforward questions.
- For coding/tool-use, prefer models with coding affinity (mimo, deepseek, qwen-coder).
- For vision/image tasks, select a vision-capable model.

Respond with ONLY a JSON object: {"model": "<exact_model_id>"}
No explanation, no markdown, just the JSON."""


class PromptUnderstandingInterceptor:
    """LLM-powered prompt understanding: pick the best model for the prompt.

    Makes a fast, low-latency LLM call with the user's prompt and available
    model pool. The LLM returns a JSON payload selecting the exact model ID.

    Strict timeout (default 800ms). On timeout or any failure, leaves the
    model as "auto" so the core router handles it.
    """

    def __init__(
        self,
        *,
        db: Any | None = None,
        upstream: Any | None = None,
        timeout_ms: float = 800.0,
        understanding_model: str = "",
    ) -> None:
        self._db = db
        self._upstream = upstream
        self._timeout_s = timeout_ms / 1000.0
        self._understanding_model = understanding_model

    def update_db(self, db: Any) -> None:
        self._db = db

    def update_upstream(self, upstream: Any) -> None:
        self._upstream = upstream

    def _is_enabled(self) -> bool:
        if self._db is None:
            return False
        try:
            raw = self._db.get_meta("extensibility_features")
            if not raw:
                return False
            data = json.loads(raw)
            return bool(data.get("prompt_understanding_enabled", False))
        except Exception:
            return False

    def _get_understanding_model(self) -> str:
        if self._db is None:
            return self._understanding_model
        try:
            raw = self._db.get_meta("extensibility_features")
            if raw:
                data = json.loads(raw)
                m = data.get("prompt_understanding_model", "")
                if m:
                    return m
        except Exception:
            pass
        return self._understanding_model

    async def intercept(
        self,
        body: dict[str, Any],
        *,
        intent: IntentResult,
        registry: Any,
    ) -> dict[str, Any]:
        model = str(body.get("model") or "").strip().lower()
        # Only intercept auto-routed requests
        if model and model not in ("auto", ""):
            return body
        if not self._is_enabled() or self._upstream is None:
            return body

        understanding_model = self._get_understanding_model()
        if not understanding_model:
            return body

        # Gather the live model pool (top candidates, capped for prompt size)
        pool = self._gather_pool(registry, intent.intent.value)
        if not pool:
            return body

        # Extract user prompt text
        prompt_text = self._extract_prompt_text(body)
        if not prompt_text:
            return body

        try:
            selected = await self._call_understanding_llm(
                understanding_model, prompt_text, pool
            )
        except Exception as exc:
            logger.info("prompt-understanding failed: %s — falling back to auto", exc)
            return body

        if not selected:
            return body

        # Verify the selected model is live
        if registry is not None:
            resolved = registry.resolve_live_id(selected)
            if resolved is None:
                logger.info(
                    "prompt-understanding selected %s but not live — falling back",
                    selected,
                )
                return body
            selected = resolved

        logger.info(
            "prompt-understanding: overriding model=auto → %s for intent=%s",
            selected,
            intent.intent.value,
        )
        return {**body, "model": selected}

    def _gather_pool(self, registry: Any, intent: str, max_n: int = 30) -> list[str]:
        if registry is None:
            return []
        try:
            chain = registry.chain_for_intent(intent) or []
            if len(chain) < 5:
                # Widen with related intents
                pool = list(chain)
                active = (
                    registry.active_live_ids()
                    if hasattr(registry, "active_live_ids")
                    else set()
                )
                for m in sorted(active):
                    if m not in pool:
                        pool.append(m)
                    if len(pool) >= max_n:
                        break
                return pool[:max_n]
            return chain[:max_n]
        except Exception:
            return []

    @staticmethod
    def _extract_prompt_text(body: dict[str, Any]) -> str:
        msgs = body.get("messages") or body.get("input") or []
        if isinstance(msgs, list):
            parts = []
            for m in msgs[-4:]:  # last 4 messages for context
                if isinstance(m, dict):
                    role = m.get("role", "")
                    content = m.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(
                            str(c.get("text", "")) if isinstance(c, dict) else str(c)
                            for c in content
                        )
                    parts.append(f"[{role}] {content}")
                else:
                    parts.append(str(m))
            return "\n".join(parts)[:4000]
        if isinstance(msgs, str):
            return msgs[:4000]
        prompt = body.get("prompt") or ""
        return str(prompt)[:4000]

    async def _call_understanding_llm(
        self, model: str, prompt_text: str, pool: list[str]
    ) -> str | None:
        """Make a fast LLM call to select the best model. Returns model_id or None."""
        import asyncio

        user_msg = (
            f"Available models (pick one):\n{json.dumps(pool)}\n\n"
            f"User prompt:\n{prompt_text}\n\n"
            f"Select the best model. Respond with JSON: {{\"model\": \"<id>\"}}"
        )
        llm_body = {
            "model": model,
            "messages": [
                {"role": "system", "content": _UNDERSTANDING_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": 100,
            "temperature": 0,
        }
        try:
            status, resp_body, _headers, _key = await asyncio.wait_for(
                self._upstream.request_json(
                    "POST",
                    "/chat/completions",
                    json_body=llm_body,
                    max_retries=0,
                ),
                timeout=self._timeout_s,
            )
        except TimeoutError:
            logger.info(
                "prompt-understanding timed out (%.0fms) — falling back",
                self._timeout_s * 1000,
            )
            return None

        if status >= 400 or not isinstance(resp_body, dict):
            return None

        # Parse the LLM response
        choices = resp_body.get("choices") or []
        if not choices:
            return None
        content = ""
        msg = choices[0].get("message") or {}
        content = str(msg.get("content") or "")
        if not content:
            return None

        # Extract JSON from the response (may be wrapped in markdown)
        return self._parse_model_from_response(content, pool)

    @staticmethod
    def _parse_model_from_response(content: str, pool: list[str]) -> str | None:
        """Parse the LLM response and validate against the live pool."""
        content = content.strip()
        # Try direct JSON parse
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                m = str(data.get("model") or "")
                if m:
                    return m
        except json.JSONDecodeError:
            pass
        # Try extracting JSON from markdown code blocks or raw text
        json_match = re.search(r'\{[^}]*"model"\s*:\s*"([^"]+)"[^}]*\}', content)
        if json_match:
            return json_match.group(1)
        # Last resort: check if any pool model is mentioned
        content_lower = content.lower()
        for m in pool:
            if m.lower() in content_lower:
                return m
        return None