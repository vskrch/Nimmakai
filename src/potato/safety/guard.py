"""AccountGuard: jitter + sticky + global concurrency around requests."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from potato.safety.concurrency import GlobalConcurrencyGate
from potato.safety.jitter import apply_jitter
from potato.safety.sticky import StickySessionStore

if TYPE_CHECKING:
    from potato.balancer import KeyPool
    from potato.config import Settings

logger = logging.getLogger(__name__)

_RPM_WINDOW = 60.0
_RPD_WINDOW = 86400.0


@dataclass
class GuardContext:
    session_id: str | None
    preferred_key_id: str | None
    preferred_model: str | None = None


class AccountGuard:
    def __init__(
        self,
        settings: Settings,
        pool: KeyPool,
        *,
        capacity_hint: int | None = None,
    ) -> None:
        self.settings = settings
        self.pool = pool
        max_global = settings.global_max_in_flight
        if max_global <= 0:
            # Prefer multi-provider capacity hint when provided (F-09)
            if capacity_hint is not None and capacity_hint > 0:
                max_global = capacity_hint
            else:
                max_global = len(pool) * settings.nim_max_in_flight_per_key
        self.gate = GlobalConcurrencyGate(max_global)
        self.sticky = StickySessionStore(
            ttl_seconds=settings.sticky_session_ttl_seconds,
        )
        # Per-user rate limiters (in-memory, single-process).
        # Only populated when the corresponding limit is > 0.
        self._user_rpm: dict[str, deque[float]] = defaultdict(deque)
        self._user_rpd: dict[str, deque[float]] = defaultdict(deque)
        self.user_rpm_limit: int = int(getattr(settings, "user_rpm_limit", 0) or 0)
        self.user_rpd_limit: int = int(getattr(settings, "user_rpd_limit", 0) or 0)

    def resize_gate(self, capacity: int) -> None:
        """Recompute global concurrency from sum of active provider pools."""
        if self.settings.global_max_in_flight > 0:
            return  # explicit override wins
        if capacity > 0:
            self.gate.max_in_flight = capacity

    def _check_user_rate_limit(self, proxy_token: str) -> dict | None:
        """Check per-user RPM/RPD limits. Returns error body dict if exceeded, None if OK.

        No-op when both limits are 0 (backward-compatible unlimited default).
        """
        if not proxy_token:
            return None
        if self.user_rpm_limit <= 0 and self.user_rpd_limit <= 0:
            return None

        now = time.time()
        # RPM check
        if self.user_rpm_limit > 0:
            q = self._user_rpm[proxy_token]
            while q and now - q[0] > _RPM_WINDOW:
                q.popleft()
            if len(q) >= self.user_rpm_limit:
                from potato.compat import openai_error

                logger.info("user rpm limit hit: token=%s rpm=%d", proxy_token[:8], len(q))
                return openai_error(
                    f"Rate limit exceeded ({self.user_rpm_limit} req/min). Retry later.",
                    code="user_rate_limited",
                    type_="rate_limit_error",
                )
            q.append(now)
        # RPD check
        if self.user_rpd_limit > 0:
            q = self._user_rpd[proxy_token]
            while q and now - q[0] > _RPD_WINDOW:
                q.popleft()
            if len(q) >= self.user_rpd_limit:
                from potato.compat import openai_error

                logger.info("user rpd limit hit: token=%s rpd=%d", proxy_token[:8], len(q))
                return openai_error(
                    f"Daily rate limit exceeded ({self.user_rpd_limit} req/day). Retry tomorrow.",
                    code="user_daily_limited",
                    type_="rate_limit_error",
                )
            q.append(now)
        return None

    async def before_request(
        self,
        *,
        headers: Any,
        proxy_token: str | None = None,
        body: dict | None = None,
    ) -> GuardContext:
        # Per-user rate limit check (no-op when limits are 0)
        if proxy_token:
            rate_err = self._check_user_rate_limit(proxy_token)
            if rate_err is not None:
                raise RateLimitedError(rate_err)

        session_id = None
        preferred = None
        preferred_model = None
        if self.settings.sticky_sessions_enabled:
            session_id = self.sticky.resolve_session_id(headers, proxy_token=proxy_token, body=body)
            preferred = self.sticky.get(session_id)
            preferred_model = self.sticky.get_model(session_id)

        await self.gate.acquire(max_wait=30.0)
        try:
            await apply_jitter(
                enabled=self.settings.safety_jitter_enabled,
                min_ms=self.settings.safety_jitter_ms_min,
                max_ms=self.settings.safety_jitter_ms_max,
            )
            return GuardContext(
                session_id=session_id,
                preferred_key_id=preferred,
                preferred_model=preferred_model,
            )
        except BaseException:
            await self.gate.release()
            raise

    async def after_request(
        self,
        ctx: GuardContext,
        *,
        key_id: str | None = None,
        model_id: str | None = None,
        success: bool = True,
        pin_model: bool = False,
    ) -> None:
        await self.gate.release()
        if not (self.settings.sticky_sessions_enabled and ctx.session_id and success):
            return
        # OpenRouter: only pin model on success; failed routes re-select next turn
        if pin_model and model_id:
            self.sticky.put_both(ctx.session_id, key_id=key_id, model_id=model_id)
        elif key_id:
            self.sticky.put(ctx.session_id, key_id)

    def pool_exhausted_error(self) -> dict:
        return {
            "error": {
                "message": (
                    "All NIM keys unavailable (quarantined, budget, or rate-limited). Retry later."
                ),
                "type": "server_error",
                "code": "potato_pool_exhausted",
            }
        }


class RateLimitedError(Exception):
    """Raised by before_request when per-user rate limit is hit."""

    def __init__(self, response: Any) -> None:
        self.response = response
