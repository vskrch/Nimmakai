"""Intent classifier golden cases."""

from __future__ import annotations

from nimmakai.routing import Intent, IntentClassifier


def test_tools_present() -> None:
    c = IntentClassifier()
    r = c.classify(
        path="/v1/chat/completions",
        body={
            "messages": [{"role": "user", "content": "fix the bug"}],
            "tools": [{"type": "function", "function": {"name": "read_file"}}],
        },
    )
    assert r.intent == Intent.CODING_AGENTIC
    assert r.rule_id == "tools_present"


def test_short_chat() -> None:
    c = IntentClassifier()
    r = c.classify(
        path="/v1/chat/completions",
        body={"messages": [{"role": "user", "content": "What is 2+2?"}]},
    )
    assert r.intent == Intent.CHAT_FAST
    assert r.rule_id == "short_chat"


def test_agent_fingerprint() -> None:
    c = IntentClassifier()
    r = c.classify(
        path="/v1/chat/completions",
        body={
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a powerful agentic AI coding assistant. "
                        "Cursor tools follow."
                    ),
                },
                {"role": "user", "content": "refactor auth"},
            ]
        },
    )
    assert r.intent == Intent.CODING_AGENTIC
    assert r.rule_id == "agent_fingerprint"


def test_reasoning_keywords() -> None:
    c = IntentClassifier()
    r = c.classify(
        path="/v1/chat/completions",
        body={
            "messages": [
                {
                    "role": "user",
                    "content": "Please prove the theorem step-by-step for this integral.",
                }
            ]
        },
    )
    assert r.intent == Intent.REASONING


def test_vision_parts() -> None:
    c = IntentClassifier()
    r = c.classify(
        path="/v1/chat/completions",
        body={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "what is this?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,xxx"},
                        },
                    ],
                }
            ]
        },
    )
    assert r.intent == Intent.VISION


def test_embeddings_path() -> None:
    c = IntentClassifier()
    r = c.classify(path="/v1/embeddings", body={"input": "hello"})
    assert r.intent == Intent.EMBEDDINGS


def test_forced_header() -> None:
    c = IntentClassifier()
    r = c.classify(
        path="/v1/chat/completions",
        body={"messages": [{"role": "user", "content": "hi"}]},
        headers={"x-nimmakai-intent": "reasoning"},
    )
    assert r.intent == Intent.REASONING
    assert r.rule_id == "forced_header"


def test_agent_header_opencode() -> None:
    c = IntentClassifier()
    r = c.classify(
        path="/v1/chat/completions",
        body={"messages": [{"role": "user", "content": "hi"}]},
        headers={"user-agent": "OpenCode/1.0 (agentic)"},
    )
    assert r.intent == Intent.CODING_AGENTIC
    assert r.rule_id == "agent_header"
    assert r.features["agent_header"] == "opencode"


def test_agent_header_kiro() -> None:
    c = IntentClassifier()
    r = c.classify(
        path="/v1/chat/completions",
        body={"messages": [{"role": "user", "content": "hi"}]},
        headers={"x-client-name": "kiro"},
    )
    assert r.intent == Intent.CODING_AGENTIC
    assert r.rule_id == "agent_header"
    assert r.features["agent_header"] == "kiro"


def test_kiro_fingerprint() -> None:
    c = IntentClassifier()
    r = c.classify(
        path="/v1/chat/completions",
        body={
            "messages": [
                {"role": "system", "content": "You are Kiro, an agentic coding assistant."},
                {"role": "user", "content": "refactor auth"},
            ]
        },
    )
    assert r.intent == Intent.CODING_AGENTIC
    assert r.rule_id == "agent_fingerprint"
