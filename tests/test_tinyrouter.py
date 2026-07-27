"""Tests for TinyRouter and its integration with LinUCB RL Bandit Engine."""

from potato.config import Settings
from potato.routing.classifier import IntentClassifier
from potato.routing.intents import Intent
from potato.routing.rl_engine import LinUCBPolicyEngine
from potato.routing.tinyrouter import TinyRouterEngine


def test_tinyrouter_basic_classification():
    engine = TinyRouterEngine()
    result = engine.classify_intent(
        body={"messages": [{"role": "user", "content": "Write a python function to compute the derivative step by step"}]}
    )
    assert result.intent in (Intent.CODING_AGENTIC, Intent.REASONING, Intent.CHAT_FAST, Intent.LONG_HORIZON)
    assert result.confidence > 0.0
    assert result.rule_id == "tinyrouter_10k_neural"
    assert "tinyrouter_probs" in result.features


def test_tinyrouter_tool_density_override():
    engine = TinyRouterEngine()
    result = engine.classify_intent(
        body={
            "messages": [{"role": "user", "content": "do this"}],
            "tools": [{"type": "function", "function": {"name": "read_file"}}, {"type": "function", "function": {"name": "exec"}}],
        }
    )
    # With tools present, rl features should boost confidence and override to coding
    assert result.intent == Intent.CODING_AGENTIC
    assert result.confidence >= 0.95


def test_tinyrouter_rl_engine_selection():
    engine = TinyRouterEngine()
    rl_engine = LinUCBPolicyEngine()
    
    # Train rl_engine slightly on model A
    x_vec = [0.5] * 12
    rl_engine.record_feedback("potato/coding", x_vec, 1.0)
    rl_engine.record_feedback("potato/coding", x_vec, 1.0)
    
    best_model, score = engine.select_best_model_with_rl(
        intent=Intent.CODING_AGENTIC,
        candidate_models=["potato/coding", "potato/best", "potato/auto"],
        rl_engine=rl_engine,
        body={"messages": [{"role": "user", "content": "Write some python code"}]},
    )
    assert best_model in ("potato/coding", "potato/best", "potato/auto")
    assert score != 0.0


def test_classifier_integration_with_tinyrouter_mode():
    settings = Settings(classify_mode="tinyrouter")
    classifier = IntentClassifier(settings=settings)
    
    result = classifier.classify(
        path="/v1/chat/completions",
        body={"messages": [{"role": "user", "content": "Explain general relativity step by step"}]}
    )
    assert result.rule_id == "tinyrouter_10k_neural"
    assert "tinyrouter_probs" in result.features
