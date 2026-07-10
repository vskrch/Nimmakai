"""Deterministic (and optional LLM-assisted) intent classification."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from nimmakai.routing.intents import Intent, IntentResult

if TYPE_CHECKING:
    from nimmakai.config import Settings
    from nimmakai.upstream import UpstreamClient

logger = logging.getLogger(__name__)

AGENT_FINGERPRINTS = (
    "you are a powerful agentic ai coding assistant",
    "open_and_recently_viewed_files",
    "codebase_search",
    "you are auto",
    "apply_patch",
    "read_file",
    "opencode",
    "cline",
    "continue.dev",
    "cursor",
)

REASONING_KEYWORDS = re.compile(
    r"\b(prove|theorem|derivative|integral|complexity proof|"
    r"step[- ]by[- ]step|reason carefully|mathematical proof)\b",
    re.I,
)

CODE_FENCE_RE = re.compile(r"```")


class _LRUCache:
    def __init__(self, max_size: int = 256, ttl: float = 600.0) -> None:
        self.max_size = max_size
        self.ttl = ttl
        self._data: OrderedDict[str, tuple[Intent, float]] = OrderedDict()

    def get(self, key: str) -> Intent | None:
        item = self._data.get(key)
        if item is None:
            return None
        intent, expires = item
        if time.monotonic() > expires:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return intent

    def put(self, key: str, intent: Intent) -> None:
        self._data[key] = (intent, time.monotonic() + self.ttl)
        self._data.move_to_end(key)
        while len(self._data) > self.max_size:
            self._data.popitem(last=False)


class IntentClassifier:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        self._llm_cache = _LRUCache(
            max_size=getattr(settings, "llm_classify_cache_size", 256) if settings else 256,
            ttl=float(getattr(settings, "llm_classify_cache_ttl", 600) if settings else 600),
        )
        self.stats: dict[str, int] = {}

    def classify(
        self,
        *,
        path: str,
        body: dict[str, Any],
        headers: dict[str, str] | Any | None = None,
    ) -> IntentResult:
        # Forced intent header
        if headers is not None and hasattr(headers, "get"):
            forced = headers.get("x-nimmakai-intent") or headers.get("X-Nimmakai-Intent")
            if forced:
                try:
                    intent = Intent(str(forced).strip().lower())
                    return IntentResult(
                        intent=intent,
                        confidence=1.0,
                        rule_id="forced_header",
                        features={"forced": True},
                    )
                except ValueError:
                    pass

        path_l = path.lower()
        if path_l.endswith("/embeddings") or "/embeddings" in path_l:
            return self._result(Intent.EMBEDDINGS, 1.0, "path_embeddings", {})

        features = self._extract_features(body, path_l)
        result = self._rules(features, path_l)
        self.stats[result.intent.value] = self.stats.get(result.intent.value, 0) + 1
        return result

    async def classify_maybe_llm(
        self,
        *,
        path: str,
        body: dict[str, Any],
        headers: dict[str, str] | Any | None = None,
        upstream: UpstreamClient | None = None,
        fast_model: str | None = None,
        pool_pressure_high: bool = False,
    ) -> IntentResult:
        base = self.classify(path=path, body=body, headers=headers)
        settings = self.settings
        if settings is None or settings.classify_mode != "rules_then_llm":
            return base
        threshold = settings.llm_classify_threshold
        if base.confidence >= threshold or pool_pressure_high or upstream is None:
            return base
        if base.rule_id in {"path_embeddings", "forced_header", "tools_present"}:
            return base

        cache_key = self._cache_key(base.features, body)
        cached = self._llm_cache.get(cache_key)
        if cached is not None:
            return IntentResult(
                intent=cached,
                confidence=0.75,
                rule_id="llm_cache",
                features=base.features,
            )

        model = fast_model or "google/gemma-4-31b-it"
        try:
            intent = await self._llm_classify(upstream, model, body, base.features)
            if intent is not None:
                self._llm_cache.put(cache_key, intent)
                return IntentResult(
                    intent=intent,
                    confidence=0.7,
                    rule_id="llm_classify",
                    features=base.features,
                )
        except Exception:
            logger.exception("llm classify failed; using rules")
        return base

    def _extract_features(self, body: dict[str, Any], path: str) -> dict[str, Any]:
        messages = body.get("messages") or body.get("input") or []
        if not isinstance(messages, list):
            messages = []

        tools = body.get("tools") or body.get("functions") or []
        tool_choice = body.get("tool_choice")
        has_tools = bool(tools)
        has_tool_choice = tool_choice not in (None, "none", "None")

        roles = []
        texts: list[str] = []
        has_image = False
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "")
            roles.append(role)
            content = m.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        ptype = str(part.get("type") or "")
                        if ptype in {"image_url", "input_image"} or "image" in ptype:
                            has_image = True
                        if "text" in part and isinstance(part["text"], str):
                            texts.append(part["text"])
                        if "image_url" in part:
                            has_image = True

        joined = "\n".join(texts)
        joined_l = joined.lower()
        fence_count = len(CODE_FENCE_RE.findall(joined))
        has_tool_role = any(r in {"tool", "function"} for r in roles)
        agent_hit = any(fp in joined_l for fp in AGENT_FINGERPRINTS)
        char_len = len(joined)
        last_user = ""
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, str):
                    last_user = c
                break

        return {
            "has_tools": has_tools,
            "has_tool_choice": has_tool_choice,
            "has_tool_role": has_tool_role,
            "has_image": has_image,
            "fence_count": fence_count,
            "agent_fingerprint": agent_hit,
            "char_len": char_len,
            "message_count": len(messages),
            "last_user": last_user[:2000],
            "path": path,
            "reasoning_kw": bool(REASONING_KEYWORDS.search(last_user)),
        }

    def _rules(self, features: dict[str, Any], path: str) -> IntentResult:
        long_chars = (
            getattr(self.settings, "long_context_chars", 48000) if self.settings else 48000
        )
        short_chars = (
            getattr(self.settings, "short_chat_chars", 800) if self.settings else 800
        )

        if features["has_image"]:
            return self._result(Intent.VISION, 0.95, "vision_parts", features)

        if (
            features["has_tools"]
            or features["has_tool_choice"]
            or features["has_tool_role"]
        ):
            return self._result(Intent.CODING_AGENTIC, 0.98, "tools_present", features)

        if features["agent_fingerprint"]:
            return self._result(
                Intent.CODING_AGENTIC, 0.92, "agent_fingerprint", features
            )

        if features["char_len"] > long_chars:
            intent = (
                Intent.CODING_AGENTIC
                if features["has_tools"] or features["fence_count"]
                else Intent.LONG_HORIZON
            )
            return self._result(intent, 0.85, "long_context", features)

        if features["reasoning_kw"]:
            return self._result(Intent.REASONING, 0.8, "reasoning_keywords", features)

        if features["fence_count"] >= 1 and (
            features["message_count"] > 1 or features["char_len"] > short_chars
        ):
            return self._result(Intent.CODING_AGENTIC, 0.75, "code_fences", features)

        if "/completions" in path and "chat" not in path:
            if features["fence_count"] or features["char_len"] > short_chars:
                return self._result(
                    Intent.CODING_AGENTIC, 0.6, "completions_codeish", features
                )
            return self._result(Intent.CHAT_FAST, 0.7, "completions_default", features)

        if (
            not features["has_tools"]
            and features["message_count"] <= 2
            and features["char_len"] < short_chars
            and features["fence_count"] == 0
            and not features["agent_fingerprint"]
        ):
            return self._result(Intent.CHAT_FAST, 0.7, "short_chat", features)

        # Ambiguous chat → coding_agentic (design decision #12)
        return self._result(Intent.CODING_AGENTIC, 0.55, "default_coding", features)

    @staticmethod
    def _result(
        intent: Intent, confidence: float, rule_id: str, features: dict[str, Any]
    ) -> IntentResult:
        return IntentResult(
            intent=intent, confidence=confidence, rule_id=rule_id, features=features
        )

    def _cache_key(self, features: dict[str, Any], body: dict[str, Any]) -> str:
        last = str(features.get("last_user") or "")[:512]
        payload = {
            "tools": features.get("has_tools"),
            "image": features.get("has_image"),
            "len": features.get("char_len"),
            "last": last,
        }
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    async def _llm_classify(
        self,
        upstream: UpstreamClient,
        model: str,
        body: dict[str, Any],
        features: dict[str, Any],
    ) -> Intent | None:
        feat_keys = ("has_tools", "has_image", "char_len", "fence_count")
        prompt = (
            "Classify the user request into one intent label. "
            "Reply with ONLY one of: coding_agentic, chat_fast, reasoning, "
            "long_horizon, vision.\n"
            f"features={json.dumps({k: features[k] for k in feat_keys if k in features})}\n"
            f"last_user={str(features.get('last_user') or '')[:400]}"
        )
        status, resp, _headers, _key = await upstream.request_json(
            "POST",
            "/chat/completions",
            json_body={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 20,
                "temperature": 0,
            },
            max_retries=1,
        )
        if status >= 400 or not isinstance(resp, dict):
            return None
        try:
            text = resp["choices"][0]["message"]["content"].strip().lower()
            for intent in Intent:
                if intent.value in text:
                    return intent
        except (KeyError, IndexError, TypeError, AttributeError):
            return None
        return None
