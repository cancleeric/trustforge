from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from scripts import run_multi_angle_batch_sandbox as runner
from scripts.run_multi_angle_batch_sandbox import (
    _load_allowlist,
    _table_name_from_arn,
    _verify_identity,
    _verify_table_safety,
)


class _STS:
    def __init__(self, account, arn):
        self.account = account
        self.arn = arn

    def get_caller_identity(self):
        return {"Account": self.account, "Arn": self.arn}


def test_sandbox_identity_requires_exact_account_and_role():
    expected = "arn:aws:sts::123456789012:assumed-role/trustforge-sandbox/session"
    assert _verify_identity(
        sts_client=_STS("123456789012", expected),
        expected_account="123456789012",
        expected_role_arn=expected,
    )["Arn"] == expected
    with pytest.raises(RuntimeError):
        _verify_identity(
            sts_client=_STS("999999999999", expected),
            expected_account="123456789012",
            expected_role_arn=expected,
        )


def test_sandbox_identity_rejects_root():
    with pytest.raises(RuntimeError, match="root"):
        _verify_identity(
            sts_client=_STS("123456789012", "arn:aws:iam::123456789012:root"),
            expected_account="123456789012",
            expected_role_arn="arn:aws:iam::123456789012:root",
        )


def test_table_arn_must_match_account_region_and_plain_table():
    arn = "arn:aws:dynamodb:us-east-1:123456789012:table/trustforge-sandbox"
    assert (
        _table_name_from_arn(arn, account="123456789012", region="us-east-1")
        == "trustforge-sandbox"
    )
    with pytest.raises(ValueError):
        _table_name_from_arn(arn, account="999999999999", region="us-east-1")
    with pytest.raises(ValueError):
        _table_name_from_arn(
            f"{arn}/index/by-state", account="123456789012", region="us-east-1"
        )


class _Dynamo:
    def __init__(self, *, pitr="ENABLED", tags=None):
        self.pitr = pitr
        self.tags = tags or [{"Key": "Environment", "Value": "sandbox"}]

    def describe_table(self, **_kwargs):
        return {
            "Table": {
                "TableArn": "arn:aws:dynamodb:us-east-1:123456789012:table/tf-sandbox",
                "TableStatus": "ACTIVE",
                "KeySchema": [
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                "AttributeDefinitions": [
                    {"AttributeName": "pk", "AttributeType": "S"},
                    {"AttributeName": "sk", "AttributeType": "S"},
                ],
                "SSEDescription": {"Status": "ENABLED"},
            }
        }

    def describe_continuous_backups(self, **_kwargs):
        return {
            "ContinuousBackupsDescription": {
                "PointInTimeRecoveryDescription": {
                    "PointInTimeRecoveryStatus": self.pitr
                }
            }
        }

    def list_tags_of_resource(self, **_kwargs):
        return {"Tags": self.tags}


def test_table_safety_requires_exact_arn_encryption_pitr_keys_and_tag():
    arn = "arn:aws:dynamodb:us-east-1:123456789012:table/tf-sandbox"
    _verify_table_safety(
        dynamodb_client=_Dynamo(),
        table_name="tf-sandbox",
        expected_table_arn=arn,
    )
    with pytest.raises(RuntimeError, match="PITR"):
        _verify_table_safety(
            dynamodb_client=_Dynamo(pitr="DISABLED"),
            table_name="tf-sandbox",
            expected_table_arn=arn,
        )
    with pytest.raises(RuntimeError, match="tag"):
        _verify_table_safety(
            dynamodb_client=_Dynamo(tags=[{"Key": "Environment", "Value": "prod"}]),
            table_name="tf-sandbox",
            expected_table_arn=arn,
        )


def test_versioned_allowlist_is_disabled_by_default(monkeypatch, tmp_path):
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        json.dumps(
            {
                "enabled": False,
                "account_id_sha256": "",
                "caller_arn_sha256": "",
                "table_arn_sha256": "",
                "region": "us-east-1",
                "config_version": "v1",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "_ALLOWLIST_PATH", allowlist)
    with pytest.raises(RuntimeError, match="disabled"):
        _load_allowlist()


def test_enabled_allowlist_resolves_only_commit_bound_identity(monkeypatch, tmp_path):
    values = {
        "account_id": "123456789012",
        "caller_arn": (
            "arn:aws:sts::123456789012:assumed-role/"
            "trustforge-896-sandbox-runner/reviewed-session"
        ),
        "table_arn": (
            "arn:aws:dynamodb:us-east-1:123456789012:"
            "table/trustforge-issue896-sandbox-3"
        ),
    }
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps({
        "enabled": True,
        "region": "us-east-1",
        "config_version": "v1",
        **{
            f"{field}_sha256": hashlib.sha256(value.encode()).hexdigest()
            for field, value in values.items()
        },
    }), encoding="utf-8")
    monkeypatch.setattr(runner, "_ALLOWLIST_PATH", allowlist)
    for field, env_name in runner._IDENTITY_ENV.items():
        monkeypatch.setenv(env_name, values[field])
    assert _load_allowlist() == {**values, "region": "us-east-1", "config_version": "v1"}
    monkeypatch.setenv("TRUSTFORGE_SANDBOX_ACCOUNT_ID", "999999999999")
    with pytest.raises(RuntimeError, match="reviewed allowlist"):
        _load_allowlist()


def test_runbook_lists_underlying_transaction_item_permissions():
    runbook = (
        Path(__file__).resolve().parents[1]
        / "docs/runbooks/MULTI-ANGLE-ATOMIC-BATCH-MIGRATION-ROLLBACK.md"
    ).read_text(encoding="utf-8")
    for action in (
        "dynamodb:TransactWriteItems",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
    ):
        assert action in runbook


def test_committed_allowlist_is_locked_after_proof():
    allowlist = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "deploy/config/multi-angle-batch-sandbox.json"
        ).read_text(encoding="utf-8")
    )
    assert allowlist["enabled"] is False


def test_committed_runner_policy_is_exact_table_except_sts_identity():
    policy = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "deploy/iam/issue-896-sandbox-runner-policy.json"
        ).read_text(encoding="utf-8")
    )
    statements = {statement["Sid"]: statement for statement in policy["Statement"]}
    assert statements["CallerIdentity"] == {
        "Sid": "CallerIdentity",
        "Effect": "Allow",
        "Action": "sts:GetCallerIdentity",
        "Resource": "*",
    }
    table_arn = (
        "arn:aws:dynamodb:us-east-1:${aws:PrincipalAccount}:"
        "table/trustforge-issue896-sandbox-3"
    )
    assert statements["SandboxTableOnly"]["Resource"] == table_arn
    assert set(statements["SandboxTableOnly"]["Action"]) == {
        "dynamodb:TransactWriteItems",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:GetItem",
        "dynamodb:BatchGetItem",
        "dynamodb:DescribeTable",
        "dynamodb:DescribeContinuousBackups",
        "dynamodb:ListTagsOfResource",
    }
