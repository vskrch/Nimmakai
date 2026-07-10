"""Health and pool observability endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from nimmakai import __version__

router = APIRouter(tags=["admin"])


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    pool = getattr(request.app.state, "pool", None)
    keys = len(pool) if pool is not None else 0
    return JSONResponse(
        {
            "status": "ok",
            "version": __version__,
            "nim_keys_configured": keys,
        }
    )


@router.get("/stats")
async def stats(request: Request) -> JSONResponse:
    """Per-key RPM / latency / cooldown snapshot (no secret values)."""
    pool = request.app.state.pool
    return JSONResponse(
        {
            "version": __version__,
            "keys": pool.snapshot(),
        }
    )
