"""Multi-tenant accounts: signup, verify, approve, API keys, sessions."""

from potato.accounts.email import (
    OutboundEmail,
    SmtpConfig,
    SmtpEmailSender,
    StubEmailSender,
    build_otp_email,
    build_verify_email,
    get_email_sender,
)
from potato.accounts.schema import migrate_accounts
from potato.accounts.store import (
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SUSPENDED,
    STATUS_UNVERIFIED,
    AccountStore,
)

__all__ = [
    "AccountStore",
    "OutboundEmail",
    "SmtpConfig",
    "SmtpEmailSender",
    "StubEmailSender",
    "build_otp_email",
    "build_verify_email",
    "get_email_sender",
    "migrate_accounts",
    "STATUS_ACTIVE",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "STATUS_SUSPENDED",
    "STATUS_UNVERIFIED",
]
