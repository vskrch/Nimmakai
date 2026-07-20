"""SMTP email sender unit tests (mocked transport — not wired into routes)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nimmakai.accounts.email import (
    OutboundEmail,
    SmtpConfig,
    SmtpEmailSender,
    StubEmailSender,
    build_otp_email,
    build_verify_email,
    get_email_sender,
    smtp_config_from_settings,
)


def test_build_verify_email_contains_link():
    msg = build_verify_email(
        to="a@b.co", verify_url="https://app.example/auth/verify?token=abc"
    )
    assert msg.to == "a@b.co"
    assert "https://app.example/auth/verify?token=abc" in msg.text
    assert msg.html is not None
    assert "verify?token=abc" in msg.html


def test_build_otp_email_contains_code():
    msg = build_otp_email(to="a@b.co", code="482910", purpose="sign-in")
    assert "482910" in msg.text
    assert "482910" in msg.subject
    assert msg.html is not None


def test_smtp_config_rejects_both_tls_and_ssl():
    with pytest.raises(ValueError, match="only one"):
        SmtpConfig(
            host="smtp.example.com",
            from_address="noreply@example.com",
            use_tls=True,
            use_ssl=True,
        ).validate()


def test_smtp_config_from_settings():
    s = SimpleNamespace(
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_username="u",
        smtp_password="p",
        smtp_from="noreply@example.com",
        smtp_from_name="Nimmakai",
        smtp_use_tls=False,
        smtp_use_ssl=True,
        smtp_timeout=15.0,
    )
    cfg = smtp_config_from_settings(s)
    assert cfg.host == "smtp.example.com"
    assert cfg.port == 465
    assert cfg.use_ssl is True
    assert cfg.use_tls is False


def test_get_email_sender_default_stub():
    assert isinstance(get_email_sender("stub"), StubEmailSender)
    assert isinstance(get_email_sender("smtp"), StubEmailSender)  # no settings → stub


def test_get_email_sender_smtp_with_settings():
    s = SimpleNamespace(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username=None,
        smtp_password=None,
        smtp_from="noreply@example.com",
        smtp_from_name="Nimmakai",
        smtp_use_tls=True,
        smtp_use_ssl=False,
        smtp_timeout=30.0,
    )
    sender = get_email_sender("smtp", settings=s)
    assert isinstance(sender, SmtpEmailSender)


@patch("nimmakai.accounts.email.smtplib.SMTP")
def test_smtp_sender_starttls_send(mock_smtp_cls):
    smtp = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = smtp

    sender = SmtpEmailSender(
        SmtpConfig(
            host="smtp.example.com",
            port=587,
            username="user",
            password="pass",
            from_address="noreply@example.com",
            use_tls=True,
        )
    )
    result = sender.send(
        OutboundEmail(to="a@b.co", subject="Hi", text="body", html="<p>body</p>")
    )
    assert result["ok"] is True
    assert result["backend"] == "smtp"
    smtp.ehlo.assert_called()
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("user", "pass")
    smtp.send_message.assert_called_once()


@patch("nimmakai.accounts.email.smtplib.SMTP_SSL")
def test_smtp_sender_ssl_send(mock_ssl_cls):
    smtp = MagicMock()
    mock_ssl_cls.return_value.__enter__.return_value = smtp

    sender = SmtpEmailSender(
        SmtpConfig(
            host="smtp.example.com",
            port=465,
            from_address="noreply@example.com",
            use_tls=False,
            use_ssl=True,
        )
    )
    result = sender.send(OutboundEmail(to="a@b.co", subject="Hi", text="body"))
    assert result["ok"] is True
    smtp.send_message.assert_called_once()
    smtp.login.assert_not_called()


@patch("nimmakai.accounts.email.smtplib.SMTP")
def test_smtp_sender_failure_returns_ok_false(mock_smtp_cls):
    mock_smtp_cls.return_value.__enter__.side_effect = OSError("connection refused")
    sender = SmtpEmailSender(
        SmtpConfig(host="smtp.example.com", from_address="noreply@example.com")
    )
    result = sender.send(OutboundEmail(to="a@b.co", subject="Hi", text="body"))
    assert result["ok"] is False
    assert "connection refused" in result["error"]
