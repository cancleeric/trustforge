from __future__ import annotations

import json

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
                "account_id": "123456789012",
                "caller_arn": "arn:aws:sts::123456789012:assumed-role/sandbox/session",
                "table_arn": (
                    "arn:aws:dynamodb:us-east-1:123456789012:"
                    "table/tf-sandbox"
                ),
                "region": "us-east-1",
                "config_version": "v1",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "_ALLOWLIST_PATH", allowlist)
    with pytest.raises(RuntimeError, match="disabled"):
        _load_allowlist()
