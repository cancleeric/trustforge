from __future__ import annotations

import json
import subprocess
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "deploy"
    / "setup_atomic_batch_dynamodb.sh"
)


def test_atomic_authority_policy_covers_transaction_subactions():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--print-policy"],
        check=True,
        capture_output=True,
        text=True,
    )
    policy = json.loads(result.stdout)
    statement = policy["Statement"][0]

    assert statement["Resource"].endswith(
        ":table/trustforge-multi-angle-batches"
    )
    assert set(statement["Action"]) >= {
        "dynamodb:TransactWriteItems",
        "dynamodb:ConditionCheckItem",
        "dynamodb:UpdateItem",
        "dynamodb:PutItem",
        "dynamodb:Scan",
    }

    assert "Resource" in statement and isinstance(statement["Resource"], str)
    assert "*" not in statement["Resource"]
    actions = statement["Action"]
    if isinstance(actions, str):
        actions = [actions]
    assert "*" not in actions
    for action in actions:
        assert "*" not in action


def test_atomic_authority_enables_pitr_and_uses_composite_key():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "AttributeName=pk,KeyType=HASH" in script
    assert "AttributeName=sk,KeyType=RANGE" in script
    assert 'HASH_KEY" != "pk"' in script
    assert 'RANGE_KEY" != "sk"' in script
    assert 'PK_TYPE" != "S"' in script
    assert 'SK_TYPE" != "S"' in script
    assert "incompatible key schema" in script
    assert "PointInTimeRecoveryEnabled=true" in script
    assert "--sse-specification Enabled=true,SSEType=KMS" in script
    assert "atomic authority table encryption is not enabled" in script
