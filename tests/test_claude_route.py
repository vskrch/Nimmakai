"""Unit tests for Claude UI / Anthropic Messages API compatible routes."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from nimmakai.main import create_app
from nimmakai.routes.claude import (
    is_anthropic_request,
    transform_anthropic_to_openai,
    transform_openai_to_anthropic_json,
)


def test_is_anthropic_request():
    assert is_anthropic_request({"system": "Hello"}, "/chat") is True
    assert is_anthropic_request({}, "/v1/messages") is True
    assert is_anthropic_request({"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}, "/chat") is True
    assert is_anthropic_request({"messages": [{"role": "user", "content": "hi"}]}, "/chat") is False


def test_transform_anthropic_to_openai():
    anthropic_payload = {
        "model": "claude-3-5-sonnet-20241022",
        "system": "Be concise",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Write a python function"}]}
        ],
        "max_tokens": 500,
        "temperature": 0.5,
    }
    openai_payload = transform_anthropic_to_openai(anthropic_payload)
    assert openai_payload["model"] == "nimmakai/auto"
    assert openai_payload["max_tokens"] == 500
    assert openai_payload["temperature"] == 0.5
    assert len(openai_payload["messages"]) == 2
    assert openai_payload["messages"][0] == {"role": "system", "content": "Be concise"}
    assert openai_payload["messages"][1] == {"role": "user", "content": "Write a python function"}


def test_transform_openai_to_anthropic_json():
    openai_resp = {
        "id": "chatcmpl-123",
        "model": "qwen/qwen3.5-122b-a10b",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "def foo(): pass"},
            }
        ],
        "usage": {"prompt_tokens": 15, "completion_tokens": 8},
    }
    anthropic_resp = transform_openai_to_anthropic_json(openai_resp, "nimmakai/auto")
    assert anthropic_resp["type"] == "message"
    assert anthropic_resp["role"] == "assistant"
    assert anthropic_resp["content"][0]["text"] == "def foo(): pass"
    assert anthropic_resp["usage"]["input_tokens"] == 15
    assert anthropic_resp["usage"]["output_tokens"] == 8


@pytest.fixture
def client(tmp_path):
    from nimmakai.config import Settings
    settings = Settings(
        proxy_api_keys=["sk-test"],
        allow_insecure_auth=True,
        catalog_fetch_docs=False,
        catalog_run_probes=False,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_chat_endpoint_default_model(client, monkeypatch):
    async def fake_request_json(*args, **kwargs):
        return (
            200,
            {
                "id": "cmpl-1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Hello from auto router"},
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            },
            {},
            None,
        )

    from nimmakai.upstream import UpstreamClient
    monkeypatch.setattr(UpstreamClient, "request_json", fake_request_json)

    res = client.post(
        "/chat",
        headers={"Authorization": "Bearer sk-test"},
        json={"messages": [{"role": "user", "content": "Hello"}]},
    )
    assert res.status_code == 200
    data = res.json()
    assert "choices" in data or "content" in data


def test_v1_messages_anthropic_endpoint(client, monkeypatch):
    async def fake_request_json(*args, **kwargs):
        return (
            200,
            {
                "id": "cmpl-2",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Response for Claude UI"},
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6},
            },
            {},
            None,
        )

    from nimmakai.upstream import UpstreamClient
    monkeypatch.setattr(UpstreamClient, "request_json", fake_request_json)

    res = client.post(
        "/v1/messages",
        headers={"x-api-key": "sk-test"},
        json={
            "model": "claude-3-5-sonnet-20241022",
            "system": "Act as a helper",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert data["content"][0]["text"] == "Response for Claude UI"
    assert data["usage"]["input_tokens"] == 12
