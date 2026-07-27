"""Public chat endpoints — no account required.

Serves the /chat web UI's backend. Uses the gateway's own provider keys
(admin-configured) so anyone can chat without signing up. Per-IP rate limited.

Gate: settings.public_chat_enabled (default true).
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from typing import Any
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from potato.compat import openai_error
from potato.config import get_settings

logger = logging.getLogger("potato.public_chat")

router = APIRouter()

# ponytail: in-memory per-IP sliding-window rate limiter. Per-account locks if
# throughput matters across multiple workers (single-process default).
_RPM_WINDOW = 60.0
_hits: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str, rpm: int) -> bool:
    now = time.time()
    q = _hits[ip]
    # drop entries older than the window
    while q and now - q[0] > _RPM_WINDOW:
        q.popleft()
    if len(q) >= rpm:
        return False
    q.append(now)
    return True


def _settings(request: Request) -> Any:
    return getattr(request.app.state, "settings", None) or get_settings()


def _public_disabled(settings: Any) -> JSONResponse | None:
    if not getattr(settings, "public_chat_enabled", True):
        return JSONResponse(
            content=openai_error(
                "Public chat is disabled on this gateway.",
                code="public_chat_disabled",
                type_="invalid_request_error",
            ),
            status_code=403,
        )
    return None


@router.get("/chat/api/models")
async def public_models(request: Request) -> JSONResponse:
    """List available models for the public chat picker (no auth)."""
    settings = _settings(request)
    blocked = _public_disabled(settings)
    if blocked is not None:
        return blocked
    registry = getattr(request.app.state, "registry", None)
    if registry is None or not registry.live_ids:
        return JSONResponse(
            content={"object": "list", "data": []},
        )
    data = []
    for mid in sorted(registry.active_live_ids()):
        item: dict[str, Any] = {
            "id": mid,
            "object": "model",
            "created": 0,
            "owned_by": mid.split("/", 1)[0] if "/" in mid else "unknown",
        }
        data.append(registry.enrich_model_entry(item))
    if getattr(settings, "inject_auto_model", True):
        autos = registry.synthetic_auto_models()
        data = [*autos, *data]
    return JSONResponse(content={"object": "list", "data": data})


@router.post("/chat/api/completions", response_model=None)
async def public_completions(request: Request) -> JSONResponse | StreamingResponse:
    """Public chat completion (no auth). Per-IP rate limited. Uses gateway keys."""
    settings = _settings(request)
    blocked = _public_disabled(settings)
    if blocked is not None:
        return blocked

    ip = _client_ip(request)
    if not _check_rate_limit(ip, int(getattr(settings, "public_chat_rpm", 20))):
        return JSONResponse(
            content=openai_error(
                f"Rate limit reached ({settings.public_chat_rpm} req/min for anonymous chat). "
                "Create an account for higher limits.",
                code="public_rate_limited",
                type_="rate_limit_error",
            ),
            status_code=429,
            headers={"Retry-After": "60"},
        )

    # Reuse the internal _chat_like pipeline but skip require_active_user.
    # We import here to avoid circular imports and to set a request-state flag.
    from potato.routes.openai import _chat_like

    # Mark as anonymous-public so trace attribution logs it
    request.state.public_chat = True

    # Temporarily allow insecure auth on the request scope so require_active_user
    # resolves to an anonymous legacy_admin context (the gateway serves itself).
    # We do this by stashing a synthetic auth context that downstream code reads.
    from potato.auth import AuthContext

    request.state.auth = AuthContext(
        token=None,
        user_id=None,
        email=None,
        role="anonymous",
        status="active",
        is_admin=False,
        via="public_chat",
    )

    return await _chat_like(request, upstream_path="/chat/completions")


@router.get("/chat/api/health")
async def public_health(request: Request) -> JSONResponse:
    """Lightweight health for the chat UI (no auth)."""
    settings = _settings(request)
    blocked = _public_disabled(settings)
    if blocked is not None:
        return blocked
    registry = getattr(request.app.state, "registry", None)
    hub = getattr(request.app.state, "hub", None)
    live_models = len(registry.live_ids) if registry else 0
    active_providers = len(hub.active_provider_ids()) if hub else 0
    return JSONResponse(
        {
            "status": "ok" if live_models and active_providers else "degraded",
            "public_chat": True,
            "live_models": live_models,
            "active_providers": active_providers,
            "rpm_limit": settings.public_chat_rpm,
        }
    )


# ── Server-side web search proxy ────────────────────────────────────
# Browser-side search is CORS-blocked by Google/Bing. This proxy runs
# server-side (no CORS limits) and queries DuckDuckGo HTML (full web
# results, no API key needed) + Wikipedia (encyclopedic fallback).
# The chat UI calls /chat/api/search?q=... and feeds results as context.

_MAX_SEARCH_RESULTS = 8
_SNIPPET_CHARS = 400

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)


def _strip_html(text: str) -> str:
    text = _HTML_TAG_RE.sub("", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def _search_ddg_html(query: str) -> list[dict[str, str]]:
    """Scrape DuckDuckGo HTML results — full web, no API key, no CORS (server-side)."""
    try:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; PotatoGateway/1.0)",
                    "Accept": "text/html",
                },
            )
        if res.status_code != 200:
            return []
        results: list[dict[str, str]] = []
        for m in _DDG_RESULT_RE.finditer(res.text):
            raw_url = m.group(1)
            title = _strip_html(m.group(2))
            snippet = _strip_html(m.group(3))[:_SNIPPET_CHARS]
            # DDG wraps URLs in a redirect — unwrap the actual URL
            if "uddg=" in raw_url:
                from urllib.parse import parse_qs, urlparse

                parsed = urlparse(raw_url)
                qs = parse_qs(parsed.query)
                raw_url = qs.get("uddg", [raw_url])[0]
            if title and raw_url:
                results.append({"title": title, "snippet": snippet, "url": raw_url, "source": "duckduckgo"})
            if len(results) >= _MAX_SEARCH_RESULTS:
                break
        return results
    except Exception:
        logger.debug("DDG HTML search failed", exc_info=True)
        return []


async def _search_wikipedia(query: str) -> list[dict[str, str]]:
    """Wikipedia API — encyclopedic fallback, CORS-friendly but limited scope."""
    try:
        url = (
            f"https://en.wikipedia.org/w/api.php?action=query&list=search"
            f"&srsearch={quote_plus(query)}&format=json&origin=*&srlimit=3"
        )
        async with httpx.AsyncClient(timeout=6.0) as client:
            res = await client.get(url)
        if res.status_code != 200:
            return []
        body = res.json()
        items = body.get("query", {}).get("search", [])
        return [
            {
                "title": item.get("title", ""),
                "snippet": _strip_html(item.get("snippet", ""))[:_SNIPPET_CHARS],
                "url": f"https://en.wikipedia.org/?curid={item.get('pageid', '')}",
                "source": "wikipedia",
            }
            for item in items
        ]
    except Exception:
        return []


@router.get("/chat/api/search")
async def public_search(request: Request) -> JSONResponse:
    """Server-side web search proxy for the chat UI.

    Queries DuckDuckGo HTML (full web, no API key) + Wikipedia. Returns
    results the browser can feed as context to the LLM. Per-IP rate-limited
    so the gateway isn't abused as a free search API.
    """
    settings = _settings(request)
    blocked = _public_disabled(settings)
    if blocked is not None:
        return blocked
    ip = _client_ip(request)
    if not _check_rate_limit(ip, int(getattr(settings, "public_chat_rpm", 20))):
        return JSONResponse(
            content=openai_error(
                "Search rate limit reached. Try again shortly.",
                code="search_rate_limited",
                type_="rate_limit_error",
            ),
            status_code=429,
            headers={"Retry-After": "60"},
        )
    q = (request.query_params.get("q") or "").strip()
    if not q or len(q) > 500:
        return JSONResponse(
            content={"query": q, "results": [], "error": "query must be 1-500 chars"},
            status_code=400,
        )
    # Run both sources concurrently, prefer DDG (full web) then Wikipedia
    ddg_task, wiki_task = _search_ddg_html(q), _search_wikipedia(q)
    import asyncio

    ddg_results, wiki_results = await asyncio.gather(ddg_task, wiki_task, return_exceptions=True)
    if isinstance(ddg_results, Exception):
        ddg_results = []
    if isinstance(wiki_results, Exception):
        wiki_results = []
    # De-dup by title, DDG first (full web), then Wikipedia (encyclopedic)
    seen: set[str] = set()
    results: list[dict[str, str]] = []
    for r in [*ddg_results, *wiki_results]:
        key = r.get("title", "").lower()[:60]
        if not key or key in seen:
            continue
        seen.add(key)
        results.append(r)
        if len(results) >= _MAX_SEARCH_RESULTS:
            break
    return JSONResponse(content={"query": q, "results": results})
