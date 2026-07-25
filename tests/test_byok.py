from __future__ import annotations

import pytest
from potato.accounts.byok import decrypt_api_key, encrypt_api_key, mask_key


def test_byok_encryption_roundtrip():
    secret = "potato-master-secret-12345"
    key = "nvapi-abcdef1234567890qwertyuiop"

    encrypted = encrypt_api_key(key, secret)
    assert encrypted != key
    assert len(encrypted) > 20

    decrypted = decrypt_api_key(encrypted, secret)
    assert decrypted == key


def test_byok_wrong_secret_fails():
    secret = "correct-secret"
    wrong_secret = "wrong-secret"
    key = "gsk_123456789"

    encrypted = encrypt_api_key(key, secret)
    with pytest.raises(ValueError, match="Decryption failed"):
        decrypt_api_key(encrypted, wrong_secret)


def test_mask_key():
    assert mask_key("sk-potato-1234567890abcdef") == "sk-p...cdef"
    assert mask_key("short") == "sh...rt"
