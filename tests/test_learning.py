"""Learning store unit tests."""

from __future__ import annotations

from pathlib import Path

from nimmakai.catalog.learning import LearningStore


def test_learning_penalty_on_unavailable(tmp_path: Path) -> None:
    store = LearningStore(path=tmp_path / "learning.json")
    for _ in range(3):
        store.record(
            intent="coding_agentic",
            model_id="m1",
            success=False,
            unavailable=True,
        )
    assert store.score_delta("coding_agentic", "m1") < -10
    store.save()
    store2 = LearningStore(path=tmp_path / "learning.json")
    store2.load()
    assert store2.score_delta("coding_agentic", "m1") < -10


def test_learning_reward_on_tool_success(tmp_path: Path) -> None:
    store = LearningStore(path=tmp_path / "learning.json")
    for _ in range(5):
        store.record(
            intent="coding_agentic",
            model_id="m2",
            success=True,
            had_tools=True,
            tool_ok=True,
        )
    assert store.score_delta("coding_agentic", "m2") > 0
