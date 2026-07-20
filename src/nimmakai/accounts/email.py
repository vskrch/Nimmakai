"""Email delivery — stub (active) + SMTP (implemented, not wired into routes yet)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol

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
    """Logs email and returns preview text for local / stub UX."""

    def send(self, msg: OutboundEmail) -> dict:
        logger.info(
            "email stub to=%s subject=%s\n%s", msg.to, msg.subject, msg.text
        )
        return {"ok": True, "backend": "stub", "preview_text": msg.text}


@dataclass
class SmtpConfig:
    """SMTP transport settings (stdlib smtplib)."""

    host: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    from_address: str = "noreply@localhost"
    from_name: str = "Nimmakai"
    use_tls: bool = True  # STARTTLS on connect (typical port 587)
    use_ssl: bool = False  # Implicit SSL (typical port 465)
    timeout: float = 30.0

    def validate(self) -> None:
        if not self.host or not str(self.host).strip():
            raise ValueError("SMTP host is required")
        if self.use_tls and self.use_ssl:
            raise ValueError("Set only one of use_tls (STARTTLS) or use_ssl")
        if not self.from_address or "@" not in self.from_address:
            raise ValueError("SMTP from_address must be a valid email")


class SmtpEmailSender:
    """
    Send mail via SMTP (stdlib only).

    Not used by account routes yet — enable later via EMAIL_BACKEND=smtp
    and wiring ``get_email_sender(..., settings=...)`` in routes.
    """

    def __init__(self, config: SmtpConfig) -> None:
        config.validate()
        self._cfg = config

    def send(self, msg: OutboundEmail) -> dict:
        email = EmailMessage()
        from_hdr = self._cfg.from_address
        if self._cfg.from_name:
            email["From"] = f"{self._cfg.from_name} <{from_hdr}>"
        else:
            email["From"] = from_hdr
        email["To"] = msg.to
        email["Subject"] = msg.subject
        email.set_content(msg.text)
        if msg.html:
            email.add_alternative(msg.html, subtype="html")

        try:
            self._deliver(email)
        except Exception as exc:
            logger.exception(
                "smtp send failed to=%s subject=%s", msg.to, msg.subject
            )
            return {
                "ok": False,
                "backend": "smtp",
                "error": str(exc),
            }
        logger.info("smtp sent to=%s subject=%s", msg.to, msg.subject)
        return {"ok": True, "backend": "smtp"}

    def _deliver(self, email: EmailMessage) -> None:
        cfg = self._cfg
        if cfg.use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                cfg.host, cfg.port, timeout=cfg.timeout, context=context
            ) as smtp:
                self._auth_and_send(smtp, email)
            return

        with smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout) as smtp:
            smtp.ehlo()
            if cfg.use_tls:
                context = ssl.create_default_context()
                smtp.starttls(context=context)
                smtp.ehlo()
            self._auth_and_send(smtp, email)

    def _auth_and_send(self, smtp: smtplib.SMTP, email: EmailMessage) -> None:
        cfg = self._cfg
        if cfg.username:
            smtp.login(cfg.username, cfg.password or "")
        smtp.send_message(email)


def build_verify_email(
    *,
    to: str,
    verify_url: str,
    product_name: str = "Nimmakai",
) -> OutboundEmail:
    """Verification-link email body (used by signup when email is wired)."""
    text = (
        f"Welcome to {product_name}.\n\n"
        f"Verify your email:\n{verify_url}\n\n"
        f"After verification an admin must approve your account "
        f"before an API key is issued.\n"
    )
    html = (
        f"<p>Welcome to {product_name}.</p>"
        f"<p><a href=\"{verify_url}\">Verify your email</a></p>"
        f"<p>After verification an admin must approve your account "
        f"before an API key is issued.</p>"
        f"<p style=\"color:#888;font-size:12px\">If the button fails, open:<br>"
        f"<code>{verify_url}</code></p>"
    )
    return OutboundEmail(
        to=to,
        subject=f"Verify your {product_name} account",
        text=text,
        html=html,
    )


def build_otp_email(
    *,
    to: str,
    code: str,
    purpose: str = "sign-in",
    expires_minutes: int = 10,
    product_name: str = "Nimmakai",
) -> OutboundEmail:
    """
    One-time code email body.

    OTP issuance/storage is not implemented yet — this builder is ready
    for a future login/2FA flow.
    """
    code_n = str(code).strip()
    text = (
        f"Your {product_name} {purpose} code is: {code_n}\n\n"
        f"It expires in {expires_minutes} minutes. "
        f"If you did not request this, ignore this email.\n"
    )
    html = (
        f"<p>Your {product_name} {purpose} code:</p>"
        f"<p style=\"font-size:28px;letter-spacing:4px;font-weight:700\">"
        f"{code_n}</p>"
        f"<p>Expires in {expires_minutes} minutes.</p>"
        f"<p style=\"color:#888;font-size:12px\">"
        f"If you did not request this, ignore this email.</p>"
    )
    return OutboundEmail(
        to=to,
        subject=f"Your {product_name} code: {code_n}",
        text=text,
        html=html,
    )


def smtp_config_from_settings(settings: Any) -> SmtpConfig:
    """Map Settings / duck-typed object → SmtpConfig."""
    return SmtpConfig(
        host=str(getattr(settings, "smtp_host", None) or ""),
        port=int(getattr(settings, "smtp_port", 587) or 587),
        username=getattr(settings, "smtp_username", None) or None,
        password=getattr(settings, "smtp_password", None) or None,
        from_address=str(
            getattr(settings, "smtp_from", None) or "noreply@localhost"
        ),
        from_name=str(getattr(settings, "smtp_from_name", None) or "Nimmakai"),
        use_tls=bool(getattr(settings, "smtp_use_tls", True)),
        use_ssl=bool(getattr(settings, "smtp_use_ssl", False)),
        timeout=float(getattr(settings, "smtp_timeout", 30.0) or 30.0),
    )


def get_email_sender(
    backend: str = "stub",
    *,
    settings: Any | None = None,
) -> EmailSender:
    """
    Resolve email backend.

    ``smtp`` is implemented but account routes still call this with
    ``backend="stub"`` (or without settings). To activate SMTP later:

      1. Set EMAIL_BACKEND=smtp and SMTP_* env vars
      2. Pass settings into get_email_sender(settings.email_backend, settings=settings)
    """
    name = (backend or "stub").strip().lower()
    if name == "smtp":
        if settings is None:
            logger.warning(
                "EMAIL_BACKEND=smtp but settings not passed — falling back to stub"
            )
            return StubEmailSender()
        try:
            return SmtpEmailSender(smtp_config_from_settings(settings))
        except ValueError as exc:
            logger.error("invalid SMTP config (%s) — falling back to stub", exc)
            return StubEmailSender()
    if name == "resend":
        logger.warning("EMAIL_BACKEND=resend not implemented — using stub")
        return StubEmailSender()
    return StubEmailSender()
