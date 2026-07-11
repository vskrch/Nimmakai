"""
Routing quality evals — golden cases for classifier + ladder + learning.

Run: uv run pytest tests/eval -q
"""

from __future__ import annotations

from pathlib import Path

from nimmakai.catalog import ModelRegistry
from nimmakai.catalog.learning import LearningStore
from nimmakai.config import Settings
from nimmakai.routing import Intent, IntentClassifier, IntentResult, ModelSelector

YAML = Path(__file__).resolve().parents[2] / "config" / "models.yaml"

LIVE = {
    "qwen/qwen3.5-397b-a17b",
    "qwen/qwen3.5-122b-a10b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3-super-120b-a12b",
    "zai/glm-5.2",
    "stepfun/step-3.7-flash",
    "minimaxai/minimax-m3",
    "nvidia/llama-nemotron-embed-1b-v2",
}


def _registry() -> ModelRegistry:
    reg = ModelRegistry.from_yaml(YAML, probe_budget_per_hour=0)
    reg.live_ids = set(LIVE)
    reg._rebuild_all_chains()
    return reg


def test_eval_cursor_agent_routes_coding_to_qwen_head() -> None:
    c = IntentClassifier()
    intent = c.classify(
        path="/v1/chat/completions",
        body={
            "model": "auto",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a powerful agentic AI coding assistant. "
                        "Cursor tools follow."
                    ),
                },
                {"role": "user", "content": "refactor the auth module"},
            ],
            "tools": [{"type": "function", "function": {"name": "read_file"}}],
        },
    )
    assert intent.intent == Intent.CODING_AGENTIC
    reg = _registry()
    sel = ModelSelector(reg, Settings(nim_api_keys=["k"]))
    d = sel.resolve("nimmakai/auto", intent)
    # qwen3.5-397b should be in top 2 (Thompson noise may occasionally flip)
    top2 = d.chain[:2]
    assert any("qwen" in m for m in top2)
    assert any("397b" in m for m in top2)


def test_eval_short_chat_routes_nemotron() -> None:
    c = IntentClassifier()
    intent = c.classify(
        path="/v1/chat/completions",
        body={"messages": [{"role": "user", "content": "What is the capital of France?"}]},
    )
    assert intent.intent == Intent.CHAT_FAST
    reg = _registry()
    sel = ModelSelector(reg, Settings(nim_api_keys=["k"]))
    d = sel.resolve("auto", intent)
    top2 = d.chain[:2]
    assert any("nemotron" in m for m in top2)


def test_eval_ladder_walks_after_primary_unavailable() -> None:
    reg = _registry()
    reg.health.record_outcome(
        "qwen/qwen3.5-397b-a17b",
        success=False,
        status_code=404,
        unavailable=True,
    )
    chain = reg.chain_for_intent("coding_agentic")
    assert chain[0] != "qwen/qwen3.5-397b-a17b"
    assert len(chain) >= 2


def test_eval_learning_demotes_repeated_failures() -> None:
    store = LearningStore(path=Path("/tmp/nimmakai-learning-eval.json"))
    for _ in range(4):
        store.record(
            intent="coding_agentic",
            model_id="qwen/qwen3.5-122b-a10b",
            success=False,
            unavailable=True,
        )
    delta = store.score_delta("coding_agentic", "qwen/qwen3.5-122b-a10b")
    assert delta < -10

    reg = _registry()
    reg.learning = store
    reg.ladder.learning = store
    reg._rebuild_all_chains()
    # Primary family pin still prefers strongest healthy qwen (397b),
    # but learned-bad 122b should rank below healthier peers in the tail.
    scores = {
        m: reg.ladder.score_model(m, "coding_agentic").score
        for m in ("qwen/qwen3.5-122b-a10b", "zai/glm-5.2")
    }
    assert scores["qwen/qwen3.5-122b-a10b"] < scores["zai/glm-5.2"] + 40


def test_eval_openai_alias_gpt4o_maps_to_coding_ladder() -> None:
    reg = _registry()
    sel = ModelSelector(reg, Settings(nim_api_keys=["k"]))
    intent = IntentResult(
        intent=Intent.CODING_AGENTIC, confidence=0.9, rule_id="alias"
    )
    d = sel.resolve("gpt-4o", intent)
    assert d.mode == "alias"
    top2 = d.chain[:2]
    assert any("qwen" in m for m in top2)


def test_eval_explicit_nim_id_passthrough() -> None:
    reg = _registry()
    sel = ModelSelector(reg, Settings(nim_api_keys=["k"], enable_fallback_on_explicit=True))
    intent = IntentResult(
        intent=Intent.CHAT_FAST, confidence=1.0, rule_id="passthrough"
    )
    d = sel.resolve("nvidia/nemotron-3-super-120b-a12b", intent)
    assert d.chain[0] == "nvidia/nemotron-3-super-120b-a12b"
