"""Email delivery — stub now, Resend-ready interface."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass
class OutboundEmail:
    to: str
    subject: str
    text: str
    html: str | None = None


class EmailSender(Protocol):
    def send(self, msg: OutboundEmail) -> dict: ...


class StubEmailSender:
    """Logs email and returns verify URL in result for non-prod UX."""

    def send(self, msg: OutboundEmail) -> dict:
        logger.info(
            "email stub to=%s subject=%s\n%s", msg.to, msg.subject, msg.text
        )
        return {"ok": True, "backend": "stub", "preview_text": msg.text}


def get_email_sender(backend: str = "stub") -> EmailSender:
    # Future: if backend == "resend": return ResendEmailSender(...)
    return StubEmailSender()
