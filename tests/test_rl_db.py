from __future__ import annotations

import json
import time
from pathlib import Path
import pytest
from potato.catalog.db import PotatoDB


def test_rl_policy_crud(tmp_path: Path):
    db_path = tmp_path / "test_rl.db"
    db = PotatoDB(db_path)
    
    # Empty initially
    policies = db.load_rl_policy()
    assert policies == {}
    
    # Upsert policy
    payload = {"a_inv": [[1.0]*12]*12, "b": [0.5]*12, "request_count": 10}
    db.upsert_rl_policy("test-model", json.dumps(payload), time.time())
    
    policies = db.load_rl_policy()
    assert "test-model" in policies
    assert policies["test-model"]["request_count"] == 10
    
    # Clear policy
    db.clear_rl_policy("test-model")
    assert db.load_rl_policy() == {}
