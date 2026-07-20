# SMTP email (verify links + OTP)

Nimmakai includes an **SMTP email sender** (stdlib `smtplib` — no extra deps).  
Account routes still use the **stub** backend today so local/dev keeps working without a mail server.

| Backend | Status | Behavior |
|---------|--------|----------|
| `stub` (default) | **Active** | Logs email body; signup JSON includes `verify_url` |
| `smtp` | **Implemented, not wired** | Ready in `SmtpEmailSender`; routes do not pass `settings` yet |
| `resend` | Placeholder | Falls back to stub |

## What is implemented

- `SmtpEmailSender` / `SmtpConfig` — STARTTLS (587) or SSL (465)
- `build_verify_email(...)` — HTML + text verification link
- `build_otp_email(...)` — HTML + text one-time code body  
  (OTP **issuance/storage is not implemented** — builder only)

Source: `src/nimmakai/accounts/email.py`

## Environment variables

```bash
# Keep stub until you wire routes (see below)
EMAIL_BACKEND=stub

# When enabling SMTP:
# EMAIL_BACKEND=smtp
# SMTP_HOST=smtp.example.com
# SMTP_PORT=587
# SMTP_USERNAME=apikey_or_user
# SMTP_PASSWORD=secret
# SMTP_FROM=noreply@yourdomain.com
# SMTP_FROM_NAME=Nimmakai
# SMTP_USE_TLS=true          # STARTTLS (default)
# SMTP_USE_SSL=false         # set true + port 465 for implicit SSL
# SMTP_TIMEOUT=30
# PUBLIC_BASE_URL=https://your-app.example.com
```

### Provider examples

**Gmail (app password)**  
`SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USE_TLS=true`

**Amazon SES**  
`SMTP_HOST=email-smtp.<region>.amazonaws.com`, `SMTP_PORT=587`, SES SMTP credentials

**Mailgun / SendGrid / Postmark**  
Use their SMTP host + API user/password; set `SMTP_FROM` to a verified sender

**DigitalOcean App Platform**  
Add the `SMTP_*` secrets in the app env UI. Keep `EMAIL_BACKEND=stub` until routes are wired.

## How to wire later (checklist)

Routes currently call:

```python
get_email_sender(getattr(settings, "email_backend", "stub") or "stub")
```

Change signup / approve (and any future OTP path) to:

```python
from nimmakai.accounts.email import build_verify_email, get_email_sender

sender = get_email_sender(settings.email_backend, settings=settings)
msg = build_verify_email(to=email, verify_url=verify_url)
result = sender.send(msg)
```

For OTP (when you add codes to `email_tokens` or a new table):

```python
from nimmakai.accounts.email import build_otp_email

msg = build_otp_email(to=email, code="123456", purpose="sign-in", expires_minutes=10)
sender.send(msg)
```

Then set `EMAIL_BACKEND=smtp` and the `SMTP_*` vars.  
Stop returning `verify_url` in the signup JSON for production (stub-only convenience).

## Manual smoke test (without wiring routes)

```python
from nimmakai.accounts.email import SmtpConfig, SmtpEmailSender, build_verify_email

sender = SmtpEmailSender(SmtpConfig(
    host="smtp.example.com",
    port=587,
    username="user",
    password="pass",
    from_address="noreply@example.com",
    use_tls=True,
))
sender.send(build_verify_email(
    to="you@example.com",
    verify_url="https://app.example.com/auth/verify?token=test",
))
```

## Security notes

- Never commit `SMTP_PASSWORD`
- Prefer app passwords / SMTP credentials scoped to send-only
- Use `SESSION_SECURE_COOKIE=true` and HTTPS when verify links go to production
- Rate-limit signup before enabling real SMTP to avoid abuse as an open relay front
