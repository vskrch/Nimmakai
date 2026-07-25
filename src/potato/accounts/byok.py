"""Bring Your Own Key (BYOK) Encryption & Key Store Manager.

Provides authenticated encryption at rest (AES-256-style HMAC XOR stream)
for tenant upstream API keys using an application master secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any


def _derive_key(master_secret: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", master_secret.encode("utf-8"), salt, 100000, 32)


def encrypt_api_key(api_key: str, master_secret: str) -> str:
    """
    Encrypt plaintext API key with master_secret using PBKDF2 + HMAC-SHA256 authenticated keystream.
    Returns URL-safe base64 string containing salt, ciphertext, and tag.
    """
    if not api_key:
        return ""

    salt = os.urandom(16)
    key = _derive_key(master_secret, salt)

    # Keystream generation (CTR mode style)
    data = api_key.encode("utf-8")
    counter = 0
    keystream = bytearray()
    while len(keystream) < len(data):
        c_bytes = counter.to_bytes(4, "big")
        block = hmac.new(key, salt + c_bytes, hashlib.sha256).digest()
        keystream.extend(block)
        counter += 1

    ciphertext = bytes(a ^ b for a, b in zip(data, keystream[: len(data)]))
    tag = hmac.new(key, salt + ciphertext, hashlib.sha256).digest()[:16]

    payload = salt + tag + ciphertext
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decrypt_api_key(encrypted_key: str, master_secret: str) -> str:
    """
    Decrypt base64-encoded encrypted API key string.
    Raises ValueError if ciphertext is malformed or tag fails verification.
    """
    if not encrypted_key:
        return ""

    try:
        payload = base64.urlsafe_b64decode(encrypted_key.encode("ascii"))
        if len(payload) < 32:
            raise ValueError("Payload too short")

        salt = payload[:16]
        expected_tag = payload[16:32]
        ciphertext = payload[32:]

        key = _derive_key(master_secret, salt)
        actual_tag = hmac.new(key, salt + ciphertext, hashlib.sha256).digest()[:16]

        if not hmac.compare_digest(actual_tag, expected_tag):
            raise ValueError("HMAC tag verification failed — key tampering or wrong master secret")

        counter = 0
        keystream = bytearray()
        while len(keystream) < len(ciphertext):
            c_bytes = counter.to_bytes(4, "big")
            block = hmac.new(key, salt + c_bytes, hashlib.sha256).digest()
            keystream.extend(block)
            counter += 1

        plaintext = bytes(a ^ b for a, b in zip(ciphertext, keystream[: len(ciphertext)]))
        return plaintext.decode("utf-8")
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}") from e


def mask_key(api_key: str) -> str:
    """Return human-readable masked version of key (e.g. sk-n...4f2a)."""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return api_key[:2] + "..." + api_key[-2:]
    return api_key[:4] + "..." + api_key[-4:]
