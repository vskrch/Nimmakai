"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from nimmakai import __version__
from nimmakai.balancer import KeyPool
from nimmakai.catalog import ModelRegistry
from nimmakai.catalog.hub import ProviderHub
from nimmakai.catalog.preferences import UserPreferences
from nimmakai.catalog.providers import ProviderStore
from nimmakai.config import Settings, get_settings
from nimmakai.logging_setup import new_request_id, request_logs, setup_logging
from nimmakai.routes import admin, openai
from nimmakai.routing import FallbackExecutor, IntentClassifier, ModelSelector, RoutingStats
from nimmakai.safety import AccountGuard
from nimmakai.upstream import UpstreamClient

logger = logging.getLogger("nimmakai")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    request_logs.max_entries = max(50, int(settings.request_log_size))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(settings.log_level)
        store = ProviderStore.load(
            settings.providers_config_path,
            settings.providers_overlay_path,
            nim_base_url=settings.nim_base_url,
            nim_api_keys=list(settings.nim_api_keys),
            nim_rpm=settings.nim_rpm_limit,
            nim_rpd=settings.nim_rpd_limit,
            nim_max_in_flight=settings.nim_max_in_flight_per_key,
            sqlite_path=settings.sqlite_path,
            seed_free_presets=settings.sqlite_seed_free_presets,
        )
        hub = ProviderHub(store, settings)
        try:
            await hub.start()
        except Exception:
            logger.exception("provider hub startup failed — running degraded")

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

        # Get default upstream/pool — create fail-closed if no providers have keys
        try:
            upstream = hub.default
            pool = hub.default_pool
        except RuntimeError:
            # No providers with keys — create fail-closed pool
            pool = KeyPool(
                api_keys=["placeholder-no-keys"],
                rpm_limit=settings.effective_rpm,
                rpd_limit=settings.nim_rpd_limit,
                max_in_flight_per_key=1,
                auth_fail_threshold=settings.auth_fail_threshold,
                auth_quarantine_seconds=settings.auth_quarantine_seconds,
            )
            upstream = UpstreamClient(
                base_url=settings.nim_base_url,
                pool=pool,
                timeout=settings.upstream_timeout,
                user_agent=settings.upstream_user_agent,
                proxy_url=settings.egress_proxy_url(),
                retry_backoff_base=settings.retry_backoff_base_seconds,
                retry_backoff_cap=settings.retry_backoff_cap_seconds,
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

        # Load user preferences (SQLite + legacy JSON)
        preferences = UserPreferences(
            path=Path(".nimmakai/user_preferences.json"),
            db_path=Path(settings.sqlite_path),
        )
        preferences.load()

        if registry is not None:
            registry.ladder.provider_ids = set(hub.provider_ids)
            selector = ModelSelector(registry, settings, preferences=preferences)
            fallback = FallbackExecutor(
                upstream, registry, settings, stats=routing_stats, hub=hub
            )

            # Defer initial catalog refresh to background — don't block startup
            async def _initial_refresh() -> None:
                try:
                    # Skip docs on initial refresh — too slow for startup
                    ok = await registry.refresh_from_hub(
                        hub,
                        fetch_docs=False,
                        run_probes=False,
                    )
                    if not ok:
                        logger.warning(
                            "initial catalog refresh returned no models — "
                            "check provider API keys and base URLs"
                        )
                    else:
                        logger.info(
                            "initial catalog ready — %s live model(s)",
                            len(registry.live_ids),
                        )
                except Exception:
                    logger.exception("initial catalog refresh failed")

            asyncio.create_task(_initial_refresh())

            async def _refresh_loop() -> None:
                assert registry is not None
                from nimmakai.resilience import heal_and_refresh

                cycle = 0
                every = max(1, int(settings.probe_every_n_refreshes))
                heal_every = max(30, int(getattr(settings, "self_heal_seconds", 120) or 120))
                last_heal = time.monotonic()
                while True:
                    # Wake at the earlier of catalog refresh vs self-heal interval
                    sleep_for = min(
                        float(settings.catalog_refresh_seconds), float(heal_every)
                    )
                    await asyncio.sleep(max(15.0, sleep_for))
                    cycle += 1
                    now = time.monotonic()
                    # Lightweight heal often
                    if now - last_heal >= heal_every:
                        try:
                            empty = not registry.live_ids
                            report = await heal_and_refresh(
                                hub=hub,
                                registry=registry,
                                settings=settings,
                                force=empty,
                            )
                            last_heal = now
                            if report.get("healed_models") or report.get("refreshed"):
                                logger.info("self-heal: %s", report)
                        except Exception:
                            logger.exception("self-heal loop failed")
                    # Full catalog refresh on its own cadence
                    if cycle % max(
                        1, int(settings.catalog_refresh_seconds / max(15.0, sleep_for))
                    ) == 0:
                        run_probes = (
                            settings.catalog_run_probes and cycle % every == 0
                        )
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
        app.state.preferences = preferences

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
        for o in settings.cors_allow_origins.split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-Nimmakai-Model",
            "X-Nimmakai-Intent",
            "X-Nimmakai-Key-Id",
            "X-Nimmakai-Route-Mode",
            "X-Nimmakai-Fallback-Index",
            "X-Nimmakai-Provider",
            "X-Nimmakai-Context-Length",
            "X-Nimmakai-Requested-Model",
            "X-Nimmakai-Rule-Id",
            "X-Request-Id",
        ],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Any:
        rid = (
            request.headers.get("x-request-id")
            or request.headers.get("X-Request-Id")
            or new_request_id()
        )
        request.state.request_id = rid
        response = await call_next(request)
        response.headers.setdefault("X-Request-Id", rid)
        return response

    app.include_router(admin.router)
    app.include_router(openai.router)

    def _dashboard_html() -> HTMLResponse:
        html_path = Path(__file__).parent / "static" / "index.html"
        if html_path.is_file():
            return HTMLResponse(
                content=html_path.read_text(encoding="utf-8"),
                headers={"Cache-Control": "no-cache"},
            )
        return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        """Serve the web dashboard."""
        return _dashboard_html()

    @app.get("/")
    async def root(request: Request) -> Any:
        """
        Browsers get the dashboard; API clients get the JSON discovery document.
        """
        accept = (request.headers.get("accept") or "").lower()
        # Prefer dashboard for human browsers
        if "text/html" in accept and "application/json" not in accept.split(",")[0]:
            return _dashboard_html()
        return {
            "name": "nimmakai",
            "version": __version__,
            "dashboard": "/dashboard",
            "openai_base_url": "/v1",
            "docs": "/docs",
            "health": "/health",
            "stats": "/stats",
            "catalog": "/catalog",
            "providers": "/admin/providers",
            "status": "ok",
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
