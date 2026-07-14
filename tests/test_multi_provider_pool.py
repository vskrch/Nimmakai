"""End-to-end: free providers merge into one pool; router picks across them."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from nimmakai import upstream as up_mod
from nimmakai.config import Settings
from nimmakai.main import create_app


@pytest.fixture
def multi_app(monkeypatch):
    td = tempfile.mkdtemp()
    settings = Settings(
        proxy_api_keys=["sk-test"],
        allow_insecure_auth=False,
        nim_api_keys=["nim-key-1"],
        nim_base_url="https://nim.test/v1",
        providers_overlay_path=str(Path(td) / "providers.json"),
        catalog_snapshot_path=str(Path(td) / "catalog.json"),
        sqlite_path=str(Path(td) / "nimmakai.db"),
        sqlite_seed_free_presets=False,
        models_config_path="config/models.yaml",
        routing_enabled=True,
        catalog_fetch_docs=False,
        catalog_run_probes=False,
    )
    app = create_app(settings)

    models_by_base = {
        "https://nim.test/v1": [
            {"id": "meta/llama-3.3-70b-instruct", "context_length": 131072},
            {"id": "qwen/qwen3-32b", "context_length": 32768},
        ],
        "https://api.groq.com/openai/v1": [
            {"id": "llama-3.3-70b-versatile", "context_length": 131072},
            {"id": "llama-3.1-8b-instant", "context_length": 131072},
        ],
        "https://api.cerebras.ai/v1": [
            {"id": "llama3.1-70b", "context_length": 8192},
        ],
    }

    class _K:
        key_id = "k1"

    async def fake_start(self):
        self._client = object()

    async def fake_stop(self):
        return None

    async def fake_request_json(self, method, path, **kwargs):
        base = str(self.base_url).rstrip("/")
        if "models" in path:
            data = models_by_base.get(base, [])
            return 200, {"object": "list", "data": data}, {}, _K()
        return (
            200,
            {
                "id": "chatcmpl-1",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "ok",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
            {},
            _K(),
        )

    monkeypatch.setattr(up_mod.UpstreamClient, "start", fake_start)
    monkeypatch.setattr(up_mod.UpstreamClient, "stop", fake_stop)
    monkeypatch.setattr(up_mod.UpstreamClient, "request_json", fake_request_json)
    return app


@pytest.mark.asyncio
async def test_free_providers_merge_and_route(multi_app):
    app = multi_app
    auth = {"Authorization": "Bearer sk-test"}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            pr = await c.get("/admin/providers/presets", headers=auth)
            assert pr.status_code == 200
            ids = {p["id"] for p in pr.json()["presets"]}
            assert {"groq", "cerebras", "openrouter", "custom"}.issubset(ids)

            r = await c.post(
                "/admin/providers",
                headers=auth,
                json={"preset": "groq", "api_keys": ["gsk-1", "gsk-2"]},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True
            assert body["provider"]["key_count"] == 2
            assert body["live_model_count"] >= 2

            r = await c.post(
                "/admin/providers",
                headers=auth,
                json={
                    "id": "cerebras",
                    "name": "Cerebras",
                    "base_url": "https://api.cerebras.ai/v1",
                    "api_keys": ["csk-1"],
                },
            )
            assert r.status_code == 200

            models = await c.get("/v1/models", headers=auth)
            mids = [m["id"] for m in models.json()["data"]]
            assert any(m.startswith("groq/") for m in mids)
            assert any(m.startswith("cerebras/") for m in mids)
            assert any(m.startswith("nim/") for m in mids)

            listed = await c.get("/admin/providers", headers=auth)
            lj = listed.json()
            assert "pool" in lj
            assert lj["pool"]["live_models"] >= 5
            groq = next(p for p in lj["providers"] if p["id"] == "groq")
            assert groq["model_count"] >= 2
            assert groq["runtime"] is True
            assert groq.get("free_tier") is True

            ladder = await c.get("/ladder", headers=auth)
            head = ladder.json()["ladders"]["coding_agentic"]["ladder_head"]
            assert len({m.split("/")[0] for m in head}) >= 2

            chat = await c.post(
                "/v1/chat/completions",
                headers=auth,
                json={
                    "model": "nimmakai/auto",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert chat.status_code == 200
            assert chat.headers.get("x-nimmakai-model")
            assert chat.headers.get("x-nimmakai-provider") in {
                "nim",
                "groq",
                "cerebras",
            }

            test = await c.post(
                "/admin/providers/test",
                headers=auth,
                json={"id": "groq"},
            )
            assert test.status_code == 200
            assert test.json()["ok"] is True
            assert test.json()["model_count"] >= 1

            dash = await c.get("/dashboard")
            assert dash.status_code == 200
            assert "preset-grid" in dash.text
            assert "openPreset" in dash.text
            assert "pool-bar" in dash.text
