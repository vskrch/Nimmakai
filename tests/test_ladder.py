"""Intelligent LadderService — strength-ordered automatic fallback."""

from __future__ import annotations

from nimmakai.catalog.docs_fetcher import DocModel
from nimmakai.catalog.health import ModelHealthStore
from nimmakai.catalog.ladder import LadderService


def test_coding_ladder_prefers_powerful_qwen() -> None:
    svc = LadderService()
    live = {
        "qwen/qwen3.5-397b-a17b",
        "qwen/qwen3.5-122b-a10b",
        "qwen/qwen-image",
        "nvidia/nemotron-3-nano-30b-a3b",
        "zai/glm-5.2",
        "stepfun/step-3.7-flash",
        "minimaxai/minimax-m3",
        "google/gemma-4-31b-it",
    }
    svc.set_docs(
        [
            DocModel(
                slug="qwen3.5-397b-a17b",
                path="/x/qwen.md",
                description="Next-gen Qwen for coding, agentic, multimodal",
            ),
            DocModel(
                slug="glm-5.2",
                path="/x/glm.md",
                description="Flagship LLM for agentic workflows and coding",
            ),
        ]
    )
    svc.rebuild(live)
    ladder = svc.ladder_for("coding_agentic")
    assert ladder[0] == "qwen/qwen3.5-397b-a17b"
    assert "qwen-image" not in ladder
    # Full ladder continues to next-strongest options
    assert any("glm" in m for m in ladder)
    assert any("step-3.7" in m for m in ladder)


def test_chat_ladder_prefers_nemotron() -> None:
    svc = LadderService()
    live = {
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/llama-nemotron-embed-1b-v2",
        "qwen/qwen3.5-122b-a10b",
        "zai/glm-5.2",
    }
    svc.rebuild(live)
    ladder = svc.ladder_for("chat_fast")
    assert "nemotron" in ladder[0]
    assert "embed" not in ladder[0]
    assert "ultra" in ladder[0]


def test_ladder_skips_unhealthy_head() -> None:
    health = ModelHealthStore()
    svc = LadderService(health=health)
    live = {
        "qwen/qwen3.5-397b-a17b",
        "zai/glm-5.2",
        "stepfun/step-3.7-flash",
    }
    svc.rebuild(live)
    health.record_outcome(
        "qwen/qwen3.5-397b-a17b",
        success=False,
        status_code=404,
        unavailable=True,
    )
    ladder = svc.ladder_for("coding_agentic")
    assert ladder[0] != "qwen/qwen3.5-397b-a17b"
    assert "glm" in ladder[0] or "step" in ladder[0]


def test_score_excludes_embeddings_from_chat() -> None:
    svc = LadderService()
    s = svc.score_model("nvidia/llama-nemotron-embed-1b-v2", "chat_fast")
    assert s.score < 0
