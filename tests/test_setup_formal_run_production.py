from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from botocore.exceptions import ClientError
import pytest

SCRIPT = Path(__file__).parents[1] / "deploy" / "setup_formal_run_production.py"
SPEC = importlib.util.spec_from_file_location("setup_formal_run_production", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_authorization_requires_exact_regular_eric_token(tmp_path):
    token = tmp_path / "eric-auth-20260802-trustforge-formal-run-prod.token"
    token.touch()
    MODULE._require_authorization(token)
    wrong = tmp_path / "eric-auth-20260802-trustforge-other.token"
    wrong.touch()
    with pytest.raises(SystemExit, match="filename"):
        MODULE._require_authorization(wrong)


class _Ssm:
    def __init__(self):
        self.values = {}

    def get_parameter(self, *, Name, WithDecryption):
        if Name not in self.values:
            raise ClientError({"Error": {"Code": "ParameterNotFound"}}, "GetParameter")
        return {"Parameter": {
            "Name": Name, "Version": self.values[Name], "Type": "SecureString",
        }}

    def put_parameter(self, *, Name, Description, Type, Value, Overwrite):
        assert Type == "SecureString" and Overwrite is False and len(Value) >= 32
        self.values[Name] = 1
        return {"Version": 1}


def test_secret_setup_is_create_only_and_never_returns_values():
    client = _Ssm()
    first = MODULE._ensure_parameters(client, "/trustforge/runtime")
    second = MODULE._ensure_parameters(client, "/trustforge/runtime")
    assert all(item["created"] is True for item in first)
    assert all(item["created"] is False for item in second)
    assert all(set(item) == {"name", "version", "created"} for item in first + second)


def test_table_contract_enables_ttl_and_pitr():
    calls = []

    class Client:
        exceptions = SimpleNamespace(ResourceNotFoundException=type("Missing", (Exception,), {}))

        def describe_table(self, *, TableName):
            return {"Table": {
                "KeySchema": [
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
                "SSEDescription": {"Status": "ENABLED"},
            }}

        def describe_time_to_live(self, *, TableName):
            return {"TimeToLiveDescription": {"TimeToLiveStatus": "DISABLED"}}

        def update_time_to_live(self, **kwargs):
            calls.append(("ttl", kwargs))

        def update_continuous_backups(self, **kwargs):
            calls.append(("pitr", kwargs))

        def describe_continuous_backups(self, *, TableName):
            return {"ContinuousBackupsDescription": {
                "PointInTimeRecoveryDescription": {
                    "PointInTimeRecoveryStatus": "ENABLED",
                },
            }}

    MODULE._ensure_table(Client(), "trustforge-formal-run")
    assert calls[0][1]["TimeToLiveSpecification"] == {
        "Enabled": True, "AttributeName": "expires_at"
    }
    assert calls[1][1]["PointInTimeRecoverySpecification"] == {
        "PointInTimeRecoveryEnabled": True
    }
