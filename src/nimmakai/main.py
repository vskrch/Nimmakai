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
from fastapi.staticfiles import StaticFiles

from nimmakai import __version__
from nimmakai.balancer import KeyPool
from nimmakai.catalog import ModelRegistry
from nimmakai.catalog.hub import ProviderHub
from nimmakai.catalog.preferences import UserPreferences
from nimmakai.catalog.providers import ProviderStore
from nimmakai.config import Settings, get_settings
from nimmakai.logging_setup import new_request_id, request_logs, setup_logging
from nimmakai.routes import accounts, admin, analytics, openai
from nimmakai.routing import FallbackExecutor, IntentClassifier, ModelSelector, RoutingStats
from nimmakai.safety import AccountGuard
from nimmakai.upstream import UpstreamClient

logger = logging.getLogger("nimmakai")


def _mount_vite_assets(app: FastAPI, dist_path: Path) -> bool:
    """Mount Vite assets only when the complete asset directory exists."""
    assets_path = dist_path / "assets"
    if not assets_path.is_dir():
        return False
    app.mount("/assets", StaticFiles(directory=str(assets_path)), name="vite-assets")
    return True


def _init_accounts(app: FastAPI, settings: Settings) -> None:
    """Attach AccountStore for signup / sessions / API keys."""
    app.state.accounts = None
    try:
        from nimmakai.accounts.store import AccountStore
        from nimmakai.catalog.db import get_db

        db = get_db(settings.sqlite_path)
        app.state.accounts = AccountStore(db)
        logger.info("accounts store ready")
    except Exception:
        logger.exception("accounts store init failed")


def _init_analytics(app: FastAPI, settings: Settings) -> None:
    """Start analytics writer / retention / event bus when enabled.

    EventBus is always created so Live Feed can stream request-log events
    even when full analytics persistence is disabled.
    """
    from nimmakai.analytics.events import EventBus

    app.state.event_bus = EventBus()
    app.state.trace_writer = None
    app.state.analytics_store = None
    app.state.retention_manager = None
    if not getattr(settings, "analytics_enabled", True):
        logger.info("analytics persistence disabled (live request feed still available)")
        return
    try:
        from nimmakai.analytics.retention import RetentionManager
        from nimmakai.analytics.store import AnalyticsStore
        from nimmakai.analytics.writer import TraceWriter
        from nimmakai.catalog.db import get_db

        db = get_db(settings.sqlite_path)
        bus = app.state.event_bus
        store = AnalyticsStore(db)

        on_flush = None
        hooks: list[Any] = []
        webhook_url = getattr(settings, "analytics_webhook_url", None)
        if webhook_url:
            from nimmakai.analytics.webhook import WebhookBroadcaster

            hooks.append(WebhookBroadcaster(webhook_url).on_flush)
        otlp = getattr(settings, "analytics_otlp_endpoint", None)
        if otlp:
            from nimmakai.analytics.otel import OTLPExporter

            hooks.append(OTLPExporter(otlp).on_flush)

        def _combined_flush(batch: Any) -> None:
            for h in hooks:
                try:
                    h(batch)
                except Exception:
                    logger.exception("analytics flush hook failed")

        if hooks:
            on_flush = _combined_flush

        writer = TraceWriter(
            db,
            batch_size=int(getattr(settings, "analytics_batch_size", 50) or 50),
            flush_interval=float(
                getattr(settings, "analytics_flush_interval", 1.0) or 1.0
            ),
            event_bus=bus,
            on_flush=on_flush,
        )
        retention = RetentionManager(
            db,
            retention_days=int(getattr(settings, "analytics_retention_days", 7) or 7),
            rollup_retention_days=int(
                getattr(settings, "analytics_rollup_retention_days", 90) or 90
            ),
        )
        app.state.trace_writer = writer
        app.state.analytics_store = store
        app.state.retention_manager = retention
    except Exception:
        logger.exception("analytics init failed — continuing without analytics persistence")


def _configure_request_logs(app: FastAPI, settings: Settings) -> None:
    """Bind durable request log file next to SQLite + live SSE publisher."""
    from nimmakai.catalog.db import get_db
    from nimmakai.logging_setup import default_log_file_path, request_logs

    try:
        db = get_db(settings.sqlite_path)
    except Exception:
        db = None
        logger.exception("request log: sqlite unavailable for meta flag")

    def _on_add(entry: Any) -> None:
        bus = getattr(app.state, "event_bus", None)
        if bus is None:
            return
        try:
            bus.publish("request", entry.to_dict())
        except Exception:
            logger.debug("request log live publish failed", exc_info=True)

    request_logs.configure(
        max_entries=max(100, int(settings.request_log_size)),
        file_path=default_log_file_path(settings.sqlite_path),
        enabled=bool(getattr(settings, "request_file_logging", True)),
        db=db,
        on_add=_on_add,
    )
    st = request_logs.status()
    logger.info(
        "request file logging enabled=%s path=%s max=%s",
        st.get("enabled"),
        st.get("file_path"),
        st.get("max_entries"),
    )


async def _start_analytics(app: FastAPI) -> None:
    writer = getattr(app.state, "trace_writer", None)
    retention = getattr(app.state, "retention_manager", None)
    if writer is not None:
        await writer.start()
    if retention is not None:
        await retention.start()


async def _stop_analytics(app: FastAPI) -> None:
    retention = getattr(app.state, "retention_manager", None)
    writer = getattr(app.state, "trace_writer", None)
    if retention is not None:
        await retention.stop()
    if writer is not None:
        await writer.stop()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    # max_entries finalized in _configure_request_logs during lifespan
    request_logs.configure(max_entries=max(100, int(settings.request_log_size)))

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

        guard = AccountGuard(
            settings, pool, capacity_hint=hub.gate_capacity() or None
        )
        hub._on_capacity_change = lambda: guard.resize_gate(hub.gate_capacity())

        # Load user preferences (SQLite + legacy JSON)
        preferences = UserPreferences(
            path=Path(".nimmakai/user_preferences.json"),
            db_path=Path(settings.sqlite_path),
        )
        preferences.load()

        # Bind sticky ranking cache (SQLite) as early as possible
        try:
            from nimmakai.catalog.db import get_db

            _db = get_db(settings.sqlite_path)
            if registry is not None:
                registry.rankings_sticky = True
                registry.bind_db(_db)
        except Exception:
            logger.exception("ranking cache bind failed — will recompute in-memory")

        if registry is not None:
            registry.ladder.provider_ids = set(hub.provider_ids)
            selector = ModelSelector(registry, settings, preferences=preferences)
            fallback = FallbackExecutor(
                upstream, registry, settings, stats=routing_stats, hub=hub
            )

            # Defer initial catalog refresh to background — don't block startup
            async def _initial_refresh() -> None:
                try:
                    had_cache = bool(registry.ladder.frozen and registry.ladder._ladders)
                    # Skip docs on initial refresh — too slow for startup
                    ok = await registry.refresh_from_hub(
                        hub,
                        fetch_docs=False,
                        run_probes=False,
                        # Recompute rankings if no sticky cache; else keep frozen
                        recompute_rankings=not had_cache,
                    )
                    if not registry.ladder.frozen or not registry.dynamic_chains.get(
                        "coding_agentic"
                    ):
                        best = registry.recompute_rankings(persist=True)
                    else:
                        best = {
                            "coding_agentic": registry.dynamic_chains.get(
                                "coding_agentic", []
                            )[:8],
                            "from_cache": True,
                        }
                    if not ok:
                        logger.warning(
                            "initial catalog refresh returned no models — "
                            "check provider API keys and base URLs"
                        )
                    logger.info(
                        "startup ready live=%s sticky_rankings=%s best_coding=%s",
                        len(registry.live_ids),
                        registry.ladder.frozen,
                        best.get("coding_agentic")
                        or best.get("best_coding")
                        or registry.dynamic_chains.get("coding_agentic", [])[:5],
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
                last_learning_save = time.monotonic()
                learning_save_interval = 60.0
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
                            # NMK-304: auto-rerank when fallback rate is high
                            if routing_stats.should_rerank() and not empty:
                                try:
                                    registry.recompute_rankings(persist=True)
                                    logger.info(
                                        "adaptive rerank: %.0f%% recent fallback advances",
                                        sum(routing_stats._recent_advances)
                                        / len(routing_stats._recent_advances)
                                        * 100,
                                    )
                                except Exception:
                                    logger.exception("adaptive rerank failed")
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
                            # Background: update live model availability only —
                            # do NOT recompute sticky best-model rankings.
                            await registry.refresh_from_hub(
                                hub,
                                fetch_docs=settings.catalog_fetch_docs,
                                run_probes=run_probes,
                                recompute_rankings=False,
                            )
                        except Exception:
                            logger.exception("periodic catalog refresh failed")
                    # NMK-406: periodic learning persistence
                    now_ts = time.monotonic()
                    if now_ts - last_learning_save >= learning_save_interval:
                        try:
                            registry.learning.save()
                            last_learning_save = now_ts
                        except Exception:
                            logger.exception("periodic learning save failed")

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

        _init_accounts(app, settings)
        _init_analytics(app, settings)
        _configure_request_logs(app, settings)
        await _start_analytics(app)

        logger.info(
            "Nimmakai v%s ready — providers=%s, routing=%s, analytics=%s, accounts=%s",
            __version__,
            [p.id for p in store.enabled_providers()],
            settings.routing_enabled,
            bool(getattr(app.state, "trace_writer", None)),
            bool(getattr(app.state, "accounts", None)),
        )
        try:
            yield
        finally:
            await _stop_analytics(app)
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

    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    from nimmakai.compat import openai_error

    @app.exception_handler(HTTPException)
    async def openai_http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        """Unwrap FastAPI ``detail`` so clients see a top-level OpenAI ``error`` object."""
        detail = exc.detail
        if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
            body: dict[str, Any] = detail
        elif isinstance(detail, dict):
            body = openai_error(
                str(detail.get("message") or detail)[:2000],
                code=str(detail.get("code") or "http_error"),
                type_=str(
                    detail.get("type")
                    or (
                        "invalid_request_error"
                        if exc.status_code < 500
                        else "server_error"
                    )
                ),
            )
        else:
            body = openai_error(
                str(detail)[:2000],
                code="http_error",
                type_=(
                    "invalid_request_error"
                    if exc.status_code < 500
                    else "server_error"
                ),
            )
        headers = dict(exc.headers or {})
        if exc.status_code in (401, 403):
            headers.setdefault("WWW-Authenticate", "Bearer")
        return JSONResponse(
            status_code=exc.status_code, content=body, headers=headers
        )

    @app.exception_handler(Exception)
    async def openai_unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("unhandled error path=%s", request.url.path)
        return JSONResponse(
            status_code=500,
            content=openai_error(
                "Internal Server Error",
                code="internal_error",
                type_="server_error",
            ),
        )

    cors_origins = [
        o.strip()
        for o in settings.cors_allow_origins.split(",")
        if o.strip()
    ]
    # Credentials require explicit origins (never "*")
    cors_credentials = bool(cors_origins) and cors_origins != ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_credentials,
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

    # Serve Vite build assets (dist/) with aggressive caching
    dist_path = Path(__file__).parent / "static" / "dist"
    _mount_vite_assets(app, dist_path)

    app.include_router(accounts.router)
    app.include_router(admin.router)
    app.include_router(openai.router)
    app.include_router(analytics.router)

    def _dashboard_html() -> HTMLResponse:
        # Serve Vite build if available, fall back to legacy single-file dashboard
        dist_index = Path(__file__).parent / "static" / "dist" / "index.html"
        if dist_index.is_file():
            return HTMLResponse(
                content=dist_index.read_text(encoding="utf-8"),
                headers={"Cache-Control": "no-cache"},
            )
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
