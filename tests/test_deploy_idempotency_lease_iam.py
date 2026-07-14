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


def test_idempotency_lease_setup_is_repeatable_when_ttl_is_enabled(tmp_path):
    """A second deploy must not fail because DynamoDB TTL is already enabled."""
    calls = tmp_path / "aws-calls.log"
    fake_aws = tmp_path / "aws"
    fake_aws.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\n' \"$*\" >> {calls!s}\n"
        "case \"$*\" in\n"
        "  'sts get-caller-identity '*) echo 123456789012 ;;\n"
        "  'dynamodb describe-table '*) exit 0 ;;\n"
        "  'dynamodb describe-time-to-live '*) echo ENABLED ;;\n"
        "  'iam put-role-policy '*) exit 0 ;;\n"
        "  *) echo \"unexpected aws call: $*\" >&2; exit 9 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_aws.chmod(0o755)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "dynamodb update-time-to-live" not in calls.read_text(encoding="utf-8")
