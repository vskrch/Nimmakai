from __future__ import annotations

from pathlib import Path
import pytest
from potato.accounts.byok import decrypt_api_key, encrypt_api_key
from potato.catalog.db import PotatoDB


def test_byok_database_and_encryption_flow(tmp_path: Path):
    db_path = tmp_path / "test_tenant.db"
    db = PotatoDB(db_path)

    account_id = "usr_tenant_123"
    master_secret = "master-secret-456"
    raw_key = "gsk_groq_production_key_sample"

    # Encrypt key
    ciphertext = encrypt_api_key(raw_key, master_secret)
    assert ciphertext != raw_key

    # Save to SQLite
    db.upsert_user_provider_key(
        account_id=account_id,
        provider_id="groq",
        api_key_ciphertext=ciphertext,
        enabled=1,
        note="User A Groq production key",
        updated_at=123456.7,
    )

    # Query keys
    keys = db.load_user_provider_keys(account_id)
    assert len(keys) == 1
    assert keys[0]["provider_id"] == "groq"

    # Decrypt key
    decrypted = decrypt_api_key(keys[0]["api_key_ciphertext"], master_secret)
    assert decrypted == raw_key

    # Isolation check: User B has no keys
    assert len(db.load_user_provider_keys("usr_tenant_999")) == 0

    # Delete key
    db.delete_user_provider_key(account_id, "groq")
    assert len(db.load_user_provider_keys(account_id)) == 0
