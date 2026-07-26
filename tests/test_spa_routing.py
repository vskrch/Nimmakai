"""Integration tests for React SPA deep-link catch-all routing (UX-1)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from potato.config import Settings
from potato.main import create_app


@pytest.mark.asyncio
async def test_spa_catch_all_dashboard_and_chat():
    settings = Settings(
        proxy_api_keys=["test"],
        allow_insecure_auth=True,
    )
    app = create_app(settings)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        # Deep link under /dashboard
        r_dash = await c.get("/dashboard/models", headers={"accept": "text/html"})
        assert "text/html" in r_dash.headers.get("content-type", "").lower()
        assert "detail" not in r_dash.text  # Not a FastAPI JSON 404

        # Deep link under /chat
        r_chat = await c.get("/chat/conversation/123-abc", headers={"accept": "text/html"})
        assert "text/html" in r_chat.headers.get("content-type", "").lower()
        assert "detail" not in r_chat.text

        # Truly nonexistent root path returns standard 404 JSON or HTML
        r_miss = await c.get("/api/nonexistent")
        assert r_miss.status_code == 404
