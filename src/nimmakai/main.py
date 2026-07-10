"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nimmakai import __version__
from nimmakai.balancer import KeyPool
from nimmakai.catalog import ModelRegistry
from nimmakai.config import Settings, get_settings
from nimmakai.routes import admin, openai
from nimmakai.routing import FallbackExecutor, IntentClassifier, ModelSelector, RoutingStats
from nimmakai.safety import AccountGuard
from nimmakai.upstream import UpstreamClient

logger = logging.getLogger("nimmakai")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = KeyPool(
            api_keys=settings.nim_api_keys or ["placeholder-unconfigured"],
            rpm_limit=settings.effective_rpm,
            cooldown_seconds=settings.nim_cooldown_seconds,
            rpd_limit=settings.nim_rpd_limit,
            max_in_flight_per_key=settings.nim_max_in_flight_per_key,
            auth_fail_threshold=settings.auth_fail_threshold,
            auth_quarantine_seconds=settings.auth_quarantine_seconds,
            sticky_boost=settings.sticky_boost,
        )
        if not settings.nim_api_keys:
            logger.error(
                "No NIM_API_KEYS configured. Copy .env.example → .env and add your keys."
            )

        upstream = UpstreamClient(
            base_url=settings.nim_base_url,
            pool=pool,
            timeout=settings.upstream_timeout,
            user_agent=settings.upstream_user_agent,
            proxy_url=settings.egress_proxy_url(),
        )
        await upstream.start()

        registry: ModelRegistry | None = None
        classifier = IntentClassifier(settings)
        selector: ModelSelector | None = None
        fallback: FallbackExecutor | None = None
        routing_stats = RoutingStats()
        refresh_task: asyncio.Task | None = None

        try:
            registry = ModelRegistry.from_settings(settings)
        except FileNotFoundError:
            logger.warning(
                "models catalog missing at %s — routing will be limited",
                settings.models_config_path,
            )

        guard = AccountGuard(settings, pool)

        if registry is not None:
            selector = ModelSelector(registry, settings)
            fallback = FallbackExecutor(upstream, registry, settings, stats=routing_stats)
            if settings.nim_api_keys:
                await registry.refresh_from_upstream(
                    upstream,
                    fetch_docs=settings.catalog_fetch_docs,
                    run_probes=settings.catalog_run_probes,
                )

            async def _refresh_loop() -> None:
                assert registry is not None
                while True:
                    await asyncio.sleep(settings.catalog_refresh_seconds)
                    try:
                        await registry.refresh_from_upstream(
                            upstream,
                            fetch_docs=settings.catalog_fetch_docs,
                            # Probes only occasionally — every refresh would burn RPM
                            run_probes=settings.catalog_run_probes,
                        )
                    except Exception:
                        logger.exception("periodic catalog refresh failed")

            refresh_task = asyncio.create_task(_refresh_loop())

        app.state.settings = settings
        app.state.pool = pool
        app.state.upstream = upstream
        app.state.registry = registry
        app.state.classifier = classifier
        app.state.selector = selector
        app.state.fallback = fallback
        app.state.guard = guard
        app.state.routing_stats = routing_stats

        logger.info(
            "Nimmakai v%s ready — %s NIM key(s), effective RPM/key=%.1f, "
            "routing=%s, upstream=%s",
            __version__,
            len(settings.nim_api_keys),
            settings.effective_rpm,
            settings.routing_enabled,
            settings.nim_base_url,
        )
        try:
            yield
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
                with suppress(asyncio.CancelledError):
                    await refresh_task
            await upstream.stop()

    app = FastAPI(
        title="Nimmakai",
        description=(
            "OpenAI-compatible multi-key proxy for NVIDIA NIM with intelligent "
            "model routing. Point Cursor / OpenCode / any OpenAI client at this server."
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
            "catalog": "/catalog",
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
