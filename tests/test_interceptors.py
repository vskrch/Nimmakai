"""Pre-Router Interceptor Framework — strictly additive routing extensions."""

from __future__ import annotations

import asyncio
import os
import tempfile

from potato.catalog.db import PotatoDB
from potato.routing.intents import Intent, IntentResult
from potato.routing.interceptors import (
    CustomCatalogInterceptor,
    PromptUnderstandingInterceptor,
    run_interceptor_chain,
)


class FakeRegistry:
    """Minimal registry stub for interceptor tests."""

    def __init__(self, live_ids: set[str] | None = None) -> None:
        self._live = live_ids or set()

    def resolve_live_id(self, mid: str, **kw):
        if mid in self._live:
            return mid
        for m in self._live:
            if m.lower() == mid.lower():
                return m
        return None

    def chain_for_intent(self, intent: str, **kw):
        return sorted(self._live)[:10]

    def active_live_ids(self):
        return set(self._live)


def _intent(intent=Intent.CODING_AGENTIC) -> IntentResult:
    return IntentResult(intent=intent, confidence=0.9, rule_id="test")


def _tmp_db():
    d = tempfile.mkdtemp()
    return PotatoDB(os.path.join(d, "ext_test.db"))


# ---------------------------------------------------------------------------
# CustomCatalogInterceptor
# ---------------------------------------------------------------------------


def test_custom_catalog_overrides_auto():
    """Mapped intent → model_id overrides model="auto"."""
    db = _tmp_db()
    db.set_custom_catalog_mappings({"coding_agentic": "zen/mimo-v2.5-free"})
    reg = FakeRegistry(live_ids={"zen/mimo-v2.5-free"})
    icc = CustomCatalogInterceptor(db=db)
    body = {"model": "auto", "messages": [{"role": "user", "content": "write code"}]}
    result = asyncio.run(icc.intercept(body, intent=_intent(), registry=reg))
    assert result["model"] == "zen/mimo-v2.5-free"


def test_custom_catalog_skips_non_auto():
    """Explicit model is not intercepted."""
    db = _tmp_db()
    db.set_custom_catalog_mappings({"coding_agentic": "zen/mimo-v2.5-free"})
    reg = FakeRegistry(live_ids={"zen/mimo-v2.5-free"})
    icc = CustomCatalogInterceptor(db=db)
    body = {"model": "gpt-4o", "messages": []}
    result = asyncio.run(icc.intercept(body, intent=_intent(), registry=reg))
    assert result["model"] == "gpt-4o"


def test_custom_catalog_unmapped_intent_stays_auto():
    """Intent with no mapping stays auto."""
    db = _tmp_db()
    db.set_custom_catalog_mappings({"coding_agentic": "zen/mimo-v2.5-free"})
    reg = FakeRegistry(live_ids={"zen/mimo-v2.5-free"})
    icc = CustomCatalogInterceptor(db=db)
    body = {"model": "auto", "messages": []}
    result = asyncio.run(icc.intercept(body, intent=_intent(Intent.CHAT_FAST), registry=reg))
    assert result["model"] == "auto"


def test_custom_catalog_empty_mappings_no_op():
    """Empty mappings = no override."""
    db = _tmp_db()
    reg = FakeRegistry(live_ids={"zen/mimo-v2.5-free"})
    icc = CustomCatalogInterceptor(db=db)
    body = {"model": "auto", "messages": []}
    result = asyncio.run(icc.intercept(body, intent=_intent(), registry=reg))
    assert result["model"] == "auto"


def test_custom_catalog_skips_non_live_model():
    """Mapped model not in live pool stays auto."""
    db = _tmp_db()
    db.set_custom_catalog_mappings({"coding_agentic": "zen/mimo-v2.5-free"})
    reg = FakeRegistry(live_ids={"other/model"})
    icc = CustomCatalogInterceptor(db=db)
    body = {"model": "auto", "messages": []}
    result = asyncio.run(icc.intercept(body, intent=_intent(), registry=reg))
    assert result["model"] == "auto"


def test_custom_catalog_no_db_no_op():
    """No DB = no override (graceful)."""
    icc = CustomCatalogInterceptor(db=None)
    body = {"model": "auto", "messages": []}
    result = asyncio.run(icc.intercept(body, intent=_intent(), registry=FakeRegistry()))
    assert result["model"] == "auto"


# ---------------------------------------------------------------------------
# PromptUnderstandingInterceptor
# ---------------------------------------------------------------------------


def test_prompt_understanding_disabled_no_op():
    """When feature toggle is off, no interception."""
    db = _tmp_db()
    db.set_extensibility_features({"prompt_understanding_enabled": False})
    pui = PromptUnderstandingInterceptor(db=db, upstream=None)
    body = {"model": "auto", "messages": []}
    result = asyncio.run(pui.intercept(body, intent=_intent(), registry=FakeRegistry()))
    assert result["model"] == "auto"


def test_prompt_understanding_no_upstream_no_op():
    """No upstream client = no call, stays auto."""
    db = _tmp_db()
    db.set_extensibility_features(
        {"prompt_understanding_enabled": True, "prompt_understanding_model": "m"}
    )
    pui = PromptUnderstandingInterceptor(db=db, upstream=None)
    body = {"model": "auto", "messages": []}
    result = asyncio.run(pui.intercept(body, intent=_intent(), registry=FakeRegistry()))
    assert result["model"] == "auto"


def test_prompt_understanding_skips_non_auto():
    """Explicit model is not intercepted."""
    db = _tmp_db()
    db.set_extensibility_features(
        {"prompt_understanding_enabled": True, "prompt_understanding_model": "m"}
    )
    pui = PromptUnderstandingInterceptor(db=db, upstream=None)
    body = {"model": "gpt-4o", "messages": []}
    result = asyncio.run(pui.intercept(body, intent=_intent(), registry=FakeRegistry()))
    assert result["model"] == "gpt-4o"


def test_prompt_understanding_timeout_falls_back():
    """LLM call timeout → stays auto (graceful fallback)."""
    import asyncio as aio

    class SlowUpstream:
        async def request_json(self, *args, **kw):
            await aio.sleep(10)  # way past timeout
            return 200, {}, {}, None

    db = _tmp_db()
    db.set_extensibility_features(
        {"prompt_understanding_enabled": True, "prompt_understanding_model": "m"}
    )
    pui = PromptUnderstandingInterceptor(db=db, upstream=SlowUpstream(), timeout_ms=100)
    body = {"model": "auto", "messages": [{"role": "user", "content": "hello"}]}
    result = asyncio.run(pui.intercept(body, intent=_intent(), registry=FakeRegistry()))
    assert result["model"] == "auto"


def test_prompt_understanding_llm_error_falls_back():
    """LLM call raises → stays auto."""

    class ErrorUpstream:
        async def request_json(self, *args, **kw):
            raise RuntimeError("upstream down")

    db = _tmp_db()
    db.set_extensibility_features(
        {"prompt_understanding_enabled": True, "prompt_understanding_model": "m"}
    )
    pui = PromptUnderstandingInterceptor(db=db, upstream=ErrorUpstream(), timeout_ms=5000)
    body = {"model": "auto", "messages": [{"role": "user", "content": "hello"}]}
    result = asyncio.run(pui.intercept(body, intent=_intent(), registry=FakeRegistry()))
    assert result["model"] == "auto"


def test_prompt_understanding_selects_model():
    """LLM returns a valid model → body is mutated."""

    class GoodUpstream:
        async def request_json(self, *args, **kw):
            return (
                200,
                {"choices": [{"message": {"content": '{"model": "zen/mimo-v2.5-free"}'}}]},
                {},
                None,
            )

    db = _tmp_db()
    db.set_extensibility_features(
        {"prompt_understanding_enabled": True, "prompt_understanding_model": "m"}
    )
    reg = FakeRegistry(live_ids={"zen/mimo-v2.5-free", "other/model"})
    pui = PromptUnderstandingInterceptor(db=db, upstream=GoodUpstream(), timeout_ms=5000)
    body = {"model": "auto", "messages": [{"role": "user", "content": "write code"}]}
    result = asyncio.run(pui.intercept(body, intent=_intent(), registry=reg))
    assert result["model"] == "zen/mimo-v2.5-free"


def test_prompt_understanding_selects_non_live_falls_back():
    """LLM returns a non-live model → stays auto."""

    class GoodUpstream:
        async def request_json(self, *args, **kw):
            return (
                200,
                {"choices": [{"message": {"content": '{"model": "nonexistent/model"}'}}]},
                {},
                None,
            )

    db = _tmp_db()
    db.set_extensibility_features(
        {"prompt_understanding_enabled": True, "prompt_understanding_model": "m"}
    )
    reg = FakeRegistry(live_ids={"zen/mimo-v2.5-free"})
    pui = PromptUnderstandingInterceptor(db=db, upstream=GoodUpstream(), timeout_ms=5000)
    body = {"model": "auto", "messages": [{"role": "user", "content": "write code"}]}
    result = asyncio.run(pui.intercept(body, intent=_intent(), registry=reg))
    assert result["model"] == "auto"


def test_parse_model_from_response_json():
    """Direct JSON response is parsed."""
    m = PromptUnderstandingInterceptor._parse_model_from_response('{"model": "gpt-4o"}', ["gpt-4o"])
    assert m == "gpt-4o"


def test_parse_model_from_response_markdown():
    """Markdown-wrapped JSON is parsed."""
    m = PromptUnderstandingInterceptor._parse_model_from_response(
        '```json\n{"model": "gpt-4o"}\n```', ["gpt-4o"]
    )
    assert m == "gpt-4o"


def test_parse_model_from_response_fuzzy():
    """Falls back to fuzzy match against pool."""
    m = PromptUnderstandingInterceptor._parse_model_from_response(
        "I recommend using zen/mimo-v2.5-free for this task.",
        ["zen/mimo-v2.5-free", "other/model"],
    )
    assert m == "zen/mimo-v2.5-free"


def test_parse_model_from_response_invalid():
    """Invalid response returns None."""
    m = PromptUnderstandingInterceptor._parse_model_from_response("I don't know.", ["gpt-4o"])
    assert m is None


def test_extract_prompt_text():
    """Prompt text is extracted from messages."""
    body = {
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Write a Python function"},
        ]
    }
    text = PromptUnderstandingInterceptor._extract_prompt_text(body)
    assert "Python function" in text
    assert "helpful" in text  # last 4 messages


# ---------------------------------------------------------------------------
# Interceptor chain
# ---------------------------------------------------------------------------


def test_chain_empty_interceptors_returns_body():
    """Empty chain = no-op."""
    body = {"model": "auto"}
    result = asyncio.run(
        run_interceptor_chain(body, intent=_intent(), registry=FakeRegistry(), interceptors=[])
    )
    assert result is body


def test_chain_runs_in_order():
    """Interceptors run in order; first override wins."""
    db = _tmp_db()
    db.set_custom_catalog_mappings({"coding_agentic": "zen/mimo-v2.5-free"})
    reg = FakeRegistry(live_ids={"zen/mimo-v2.5-free"})
    icc = CustomCatalogInterceptor(db=db)

    class SecondInterceptor:
        async def intercept(self, body, *, intent, registry):
            # Should see the override from the first interceptor
            return {**body, "_second_ran": True}

    body = {"model": "auto", "messages": []}
    result = asyncio.run(
        run_interceptor_chain(
            body, intent=_intent(), registry=reg, interceptors=[icc, SecondInterceptor()]
        )
    )
    assert result["model"] == "zen/mimo-v2.5-free"
    assert result.get("_second_ran") is True


def test_chain_exception_is_swallowed():
    """Interceptor exception is caught; body returned unchanged."""

    class BoomInterceptor:
        async def intercept(self, body, *, intent, registry):
            raise RuntimeError("boom")

    body = {"model": "auto"}
    result = asyncio.run(
        run_interceptor_chain(
            body, intent=_intent(), registry=FakeRegistry(), interceptors=[BoomInterceptor()]
        )
    )
    assert result["model"] == "auto"
