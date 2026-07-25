"""Password hashing (stdlib scrypt) and API key helpers."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, hash_hex = stored.split("$", 2)
        if algo != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
        )
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_api_key() -> tuple[str, str, str]:
    """Return (raw_key, prefix, key_hash). Raw shown once to the user."""
    secret = secrets.token_urlsafe(24)
    raw = f"sk-nk-{secret}"
    prefix = raw[:15] + "…"
    return raw, prefix, hash_token(raw)


def new_session_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def new_email_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def new_id(prefix: str = "u") -> str:
    return f"{prefix}_{secrets.token_hex(8)}"
