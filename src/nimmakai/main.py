"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nimmakai import __version__
from nimmakai.balancer import KeyPool
from nimmakai.config import Settings, get_settings
from nimmakai.routes import admin, openai
from nimmakai.upstream import UpstreamClient

logger = logging.getLogger("nimmakai")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = KeyPool(
            api_keys=settings.nim_api_keys,
            rpm_limit=settings.effective_rpm,
            cooldown_seconds=settings.nim_cooldown_seconds,
        )
        upstream = UpstreamClient(
            base_url=settings.nim_base_url,
            pool=pool,
            timeout=settings.upstream_timeout,
        )
        await upstream.start()
        app.state.settings = settings
        app.state.pool = pool
        app.state.upstream = upstream
        logger.info(
            "Nimmakai v%s ready — %s NIM key(s), effective RPM/key=%.1f, upstream=%s",
            __version__,
            len(pool),
            settings.effective_rpm,
            settings.nim_base_url,
        )
        try:
            yield
        finally:
            await upstream.stop()

    app = FastAPI(
        title="Nimmakai",
        description=(
            "OpenAI-compatible multi-key proxy for NVIDIA NIM. "
            "Point Cursor / OpenCode / any OpenAI client at this server."
        ),
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(admin.router)
    app.include_router(openai.router)

    @app.get("/")
    async def root() -> dict:
        return {
            "name": "nimmakai",
            "version": __version__,
            "openai_base_url": "/v1",
            "docs": "/docs",
            "health": "/health",
            "stats": "/stats",
        }

    return app


# Module-level app for `uvicorn nimmakai.main:app`
app = create_app()


def run() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    if not settings.nim_api_keys:
        logger.error(
            "No NIM_API_KEYS configured. Copy .env.example → .env and add your keys."
        )
    uvicorn.run(
        "nimmakai.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )


if __name__ == "__main__":
    run()
