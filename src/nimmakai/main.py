"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nimmakai import __version__
from nimmakai.catalog import ModelRegistry
from nimmakai.catalog.hub import ProviderHub
from nimmakai.catalog.providers import ProviderStore
from nimmakai.config import Settings, get_settings
from nimmakai.routes import admin, openai
from nimmakai.routing import FallbackExecutor, IntentClassifier, ModelSelector, RoutingStats
from nimmakai.safety import AccountGuard

logger = logging.getLogger("nimmakai")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = ProviderStore.load(
            settings.providers_config_path,
            settings.providers_overlay_path,
            nim_base_url=settings.nim_base_url,
            nim_api_keys=list(settings.nim_api_keys),
            nim_rpm=settings.nim_rpm_limit,
            nim_rpd=settings.nim_rpd_limit,
            nim_max_in_flight=settings.nim_max_in_flight_per_key,
        )
        hub = ProviderHub(store, settings)
        await hub.start()

        if not settings.nim_api_keys:
            logger.error(
                "No NIM_API_KEYS configured. Copy .env.example → .env and add your keys "
                "(or add other providers via /admin/providers)."
            )
        if not settings.proxy_api_keys and not settings.allow_insecure_auth:
            logger.warning(
                "PROXY_API_KEYS is empty and ALLOW_INSECURE_AUTH is false — "
                "all client requests will be rejected until you set proxy keys."
            )

        upstream = hub.default
        pool = hub.default_pool

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
            registry.ladder.provider_ids = set(hub.provider_ids)
            selector = ModelSelector(registry, settings)
            fallback = FallbackExecutor(
                upstream, registry, settings, stats=routing_stats, hub=hub
            )
            has_any_keys = any(
                rt.config.resolved_keys() for rt in hub.runtimes.values()
            )
            if has_any_keys:
                await registry.refresh_from_hub(
                    hub,
                    fetch_docs=settings.catalog_fetch_docs,
                    run_probes=settings.catalog_run_probes,
                )

            async def _refresh_loop() -> None:
                assert registry is not None
                cycle = 0
                every = max(1, int(settings.probe_every_n_refreshes))
                while True:
                    await asyncio.sleep(settings.catalog_refresh_seconds)
                    cycle += 1
                    run_probes = settings.catalog_run_probes and cycle % every == 0
                    try:
                        await registry.refresh_from_hub(
                            hub,
                            fetch_docs=settings.catalog_fetch_docs,
                            run_probes=run_probes,
                        )
                    except Exception:
                        logger.exception("periodic catalog refresh failed")

            refresh_task = asyncio.create_task(_refresh_loop())

        app.state.settings = settings
        app.state.hub = hub
        app.state.pool = pool
        app.state.upstream = upstream
        app.state.registry = registry
        app.state.classifier = classifier
        app.state.selector = selector
        app.state.fallback = fallback
        app.state.guard = guard
        app.state.routing_stats = routing_stats

        logger.info(
            "Nimmakai v%s ready — providers=%s, routing=%s",
            __version__,
            [p.id for p in store.enabled_providers()],
            settings.routing_enabled,
        )
        try:
            yield
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
                with suppress(asyncio.CancelledError):
                    await refresh_task
            await hub.stop()

    app = FastAPI(
        title="Nimmakai",
        description=(
            "Self-hosted OpenRouter-style gateway: NVIDIA NIM + any OpenAI-compatible "
            "providers with intelligent model routing."
        ),
        version=__version__,
        lifespan=lifespan,
    )

    cors_origins = [
        o.strip()
        for o in os.environ.get("CORS_ALLOW_ORIGINS", "*").split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
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
            "providers": "/admin/providers",
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
    if not settings.nim_api_keys and not settings.allow_insecure_auth:
        # Allow start if other providers may be configured in YAML/overlay
        logger.warning(
            "NIM_API_KEYS empty — ensure at least one provider has keys "
            "(NIM or /admin/providers)."
        )
    if not settings.proxy_api_keys and not settings.allow_insecure_auth:
        raise SystemExit(
            "PROXY_API_KEYS is empty. Set proxy keys, or ALLOW_INSECURE_AUTH=true for local dev."
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
