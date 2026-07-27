"""End-to-end integration verification for /chat, /v1/chat/completions, and /v1/messages with TinyRouter and LinUCB RL."""

import pytest
from fastapi.testclient import TestClient

from potato.config import Settings
from potato.main import create_app


@pytest.fixture
def client(monkeypatch):
    settings = Settings(
        proxy_key="sk-potato-test",
        allow_insecure_auth=True,
        classify_mode="tinyrouter",
        catalog_fetch_docs=False,
        catalog_run_probes=False,
        nim_api_keys=["nvapi-dummy"],
    )
    app = create_app(settings)
    
    with TestClient(app) as test_client:
        async def fake_request_json(*args, **kwargs):
            return (
                200,
                {
                    "id": "cmpl-test-123",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "Hello from Potato Gateway!"},
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 8},
                },
                {},
                None,
            )

        if hasattr(app.state, "upstream"):
            monkeypatch.setattr(app.state.upstream, "request_json", fake_request_json)
        yield test_client, app


def test_end_to_end_chat_endpoint_tinyrouter_and_rl(client):
    test_client, app = client
    
    # 1. Clear RL stats
    rl_engine = app.state.rl_engine
    rl_engine.reset_all()
    assert len(rl_engine.get_all_stats()) == 0

    # 2. Call POST /chat with a prompt
    response = test_client.post(
        "/chat",
        headers={"Authorization": "Bearer sk-potato-test"},
        json={
            "model": "potato/auto",
            "messages": [{"role": "user", "content": "Write a python function to add two numbers"}],
        },
    )
    
    assert response.status_code == 200, f"Response failed: {response.text}"
    data = response.json()
    assert "choices" in data or "content" in data

    # 3. Verify RL engine recorded feedback for the executed model
    stats = rl_engine.get_all_stats()
    assert len(stats) > 0, "RL engine did not record feedback!"
    top_stat = stats[0]
    assert top_stat["request_count"] >= 1
    assert top_stat["last_updated"] > 0.0

    # 4. Call POST /v1/messages (Anthropic API format)
    response_msg = test_client.post(
        "/v1/messages",
        headers={"Authorization": "Bearer sk-potato-test"},
        json={
            "model": "potato/coding",
            "messages": [{"role": "user", "content": "Solve x^2 + 5x + 6 = 0 step by step"}],
        },
    )
    assert response_msg.status_code == 200, f"Messages failed: {response_msg.text}"
    
    # 5. Verify RL engine updated again
    stats_updated = rl_engine.get_all_stats()
    total_requests = sum(s["request_count"] for s in stats_updated)
    assert total_requests >= 2, f"Expected total requests >= 2, got {total_requests}"


def test_end_to_end_chat_completions_endpoint(client):
    test_client, app = client
    rl_engine = app.state.rl_engine
    rl_engine.reset_all()

    response = test_client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-potato-test"},
        json={
            "model": "potato/best",
            "messages": [{"role": "user", "content": "Hello world!"}],
        },
    )
    assert response.status_code == 200
    stats = rl_engine.get_all_stats()
    assert len(stats) >= 1
