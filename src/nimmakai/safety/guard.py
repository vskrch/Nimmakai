"""AccountGuard: jitter + sticky + global concurrency around requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nimmakai.safety.concurrency import GlobalConcurrencyGate
from nimmakai.safety.jitter import apply_jitter
from nimmakai.safety.sticky import StickySessionStore

if TYPE_CHECKING:
    from nimmakai.balancer import KeyPool
    from nimmakai.config import Settings


@dataclass
class GuardContext:
    session_id: str | None
    preferred_key_id: str | None


class AccountGuard:
    def __init__(self, settings: Settings, pool: KeyPool) -> None:
        self.settings = settings
        self.pool = pool
        max_global = settings.global_max_in_flight
        if max_global <= 0:
            max_global = len(pool) * settings.nim_max_in_flight_per_key
        self.gate = GlobalConcurrencyGate(max_global)
        self.sticky = StickySessionStore(
            ttl_seconds=settings.sticky_session_ttl_seconds,
        )

    async def before_request(
        self,
        *,
        headers: Any,
        proxy_token: str | None = None,
        body: dict | None = None,
    ) -> GuardContext:
        session_id = None
        preferred = None
        if self.settings.sticky_sessions_enabled:
            session_id = self.sticky.resolve_session_id(
                headers, proxy_token=proxy_token, body=body
            )
            preferred = self.sticky.get(session_id)

        await self.gate.acquire(max_wait=30.0)
        await apply_jitter(
            enabled=self.settings.safety_jitter_enabled,
            min_ms=self.settings.safety_jitter_ms_min,
            max_ms=self.settings.safety_jitter_ms_max,
        )
        return GuardContext(session_id=session_id, preferred_key_id=preferred)

    async def after_request(
        self,
        ctx: GuardContext,
        *,
        key_id: str | None = None,
        success: bool = True,
    ) -> None:
        await self.gate.release()
        if (
            self.settings.sticky_sessions_enabled
            and ctx.session_id
            and key_id
            and success
        ):
            self.sticky.put(ctx.session_id, key_id)

    def pool_exhausted_error(self) -> dict:
        return {
            "error": {
                "message": (
                    "All NIM keys unavailable (quarantined, budget, or rate-limited). "
                    "Retry later."
                ),
                "type": "server_error",
                "code": "nimmakai_pool_exhausted",
            }
        }
