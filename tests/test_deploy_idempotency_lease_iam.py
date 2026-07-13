"""The shared lease table policy must stay least-privilege and table-scoped."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "setup_idempotency_lease_dynamodb.sh"


def _policy(table: str = "trustforge-analyze-leases") -> dict:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--print-policy"],
        capture_output=True,
        text=True,
        env={**os.environ, "TRUSTFORGE_LEASE_TABLE": table},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_idempotency_lease_policy_is_minimal_and_table_scoped():
    statement = _policy()["Statement"][0]
    assert set(statement["Action"]) == {
        "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem",
    }
    assert "dynamodb:*" not in statement["Action"]
    assert statement["Resource"].endswith("table/trustforge-analyze-leases")


def test_idempotency_lease_policy_honors_table_override():
    assert _policy("lease-table-canary")["Statement"][0]["Resource"].endswith(
        "table/lease-table-canary"
    )
