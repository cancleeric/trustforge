#!/usr/bin/env python3
"""Non-provisioning sandbox proof runner for #896.

The runner refuses ambient/root/unexpected identities before touching DynamoDB.
It never creates, updates, or deletes infrastructure.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trustforge.multi_angle_batch_store import (
    AtomicBatchRequest,
    DynamoDBAtomicMultiAngleBatchStore,
)

_ALLOWLIST_PATH = (
    Path(__file__).resolve().parents[1]
    / "deploy/config/multi-angle-batch-sandbox.json"
)
_SANDBOX_TAG_KEY = "Environment"
_SANDBOX_TAG_VALUE = "sandbox"
_IDENTITY_ENV = {
    "account_id": "TRUSTFORGE_SANDBOX_ACCOUNT_ID",
    "caller_arn": "TRUSTFORGE_SANDBOX_CALLER_ARN",
    "table_arn": "TRUSTFORGE_SANDBOX_TABLE_ARN",
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _verify_identity(
    *, sts_client, expected_account: str, expected_role_arn: str
) -> dict:
    identity = sts_client.get_caller_identity()
    arn = identity.get("Arn", "")
    if arn.endswith(":root"):
        raise RuntimeError("AWS root identity is forbidden")
    if identity.get("Account") != expected_account or arn != expected_role_arn:
        raise RuntimeError("AWS identity does not exactly match expected account and role ARN")
    return identity


def _table_name_from_arn(table_arn: str, *, account: str, region: str) -> str:
    prefix = f"arn:aws:dynamodb:{region}:{account}:table/"
    if not table_arn.startswith(prefix):
        raise ValueError("table ARN does not exactly match expected account and region")
    table_name = table_arn.removeprefix(prefix)
    if not table_name or "/" in table_name:
        raise ValueError("table ARN must identify one table, not an index/resource suffix")
    return table_name


def _verify_table_safety(
    *,
    dynamodb_client,
    table_name: str,
    expected_table_arn: str,
) -> None:
    table = dynamodb_client.describe_table(TableName=table_name).get("Table", {})
    if (
        table.get("TableArn") != expected_table_arn
        or table.get("TableStatus") != "ACTIVE"
    ):
        raise RuntimeError("DynamoDB table ARN/status does not match sandbox allowlist")
    key_schema = {
        entry.get("AttributeName"): entry.get("KeyType")
        for entry in table.get("KeySchema", [])
    }
    definitions = {
        entry.get("AttributeName"): entry.get("AttributeType")
        for entry in table.get("AttributeDefinitions", [])
    }
    if key_schema != {"pk": "HASH", "sk": "RANGE"} or not all(
        definitions.get(name) == "S" for name in ("pk", "sk")
    ):
        raise RuntimeError("sandbox table must use string pk/sk keys")
    if table.get("SSEDescription", {}).get("Status") != "ENABLED":
        raise RuntimeError("sandbox table encryption must be enabled")
    backup = dynamodb_client.describe_continuous_backups(
        TableName=table_name
    ).get("ContinuousBackupsDescription", {})
    if (
        backup.get("PointInTimeRecoveryDescription", {})
        .get("PointInTimeRecoveryStatus")
        != "ENABLED"
    ):
        raise RuntimeError("sandbox table PITR must be enabled")
    tags = dynamodb_client.list_tags_of_resource(
        ResourceArn=expected_table_arn
    ).get("Tags", [])
    if not any(
        tag.get("Key") == _SANDBOX_TAG_KEY
        and tag.get("Value") == _SANDBOX_TAG_VALUE
        for tag in tags
    ):
        raise RuntimeError("sandbox table is missing the required environment tag")


def _load_allowlist() -> dict[str, str]:
    try:
        raw = json.loads(_ALLOWLIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("reviewed sandbox allowlist is unavailable") from exc
    required = (
        "account_id_sha256",
        "caller_arn_sha256",
        "table_arn_sha256",
        "region",
        "config_version",
    )
    if raw.get("enabled") is not True or any(
        not isinstance(raw.get(field), str) or not raw[field]
        for field in required
    ):
        raise RuntimeError("reviewed sandbox allowlist is disabled or incomplete")
    resolved = {field: raw[field] for field in ("region", "config_version")}
    for field, env_name in _IDENTITY_ENV.items():
        value = os.getenv(env_name, "")
        if not value:
            raise RuntimeError(f"required sandbox setting {env_name} is missing")
        expected_digest = raw[f"{field}_sha256"]
        actual_digest = hashlib.sha256(value.encode()).hexdigest()
        if not hmac.compare_digest(actual_digest, expected_digest):
            raise RuntimeError(f"sandbox setting {env_name} does not match reviewed allowlist")
        resolved[field] = value
    if not re.fullmatch(r"[0-9]{12}", resolved["account_id"]):
        raise RuntimeError("sandbox account id must be 12 digits")
    expected_caller_prefixes = (
        f"arn:aws:iam::{resolved['account_id']}:role/trustforge-896-sandbox-runner",
        f"arn:aws:sts::{resolved['account_id']}:assumed-role/trustforge-896-sandbox-runner/",
    )
    if not (
        resolved["caller_arn"] == expected_caller_prefixes[0]
        or resolved["caller_arn"].startswith(expected_caller_prefixes[1])
    ):
        raise RuntimeError("sandbox caller must use the reviewed runner role")
    expected_table = (
        f"arn:aws:dynamodb:{resolved['region']}:{resolved['account_id']}:"
        "table/trustforge-issue896-sandbox-3"
    )
    if resolved["table_arn"] != expected_table:
        raise RuntimeError("sandbox table ARN does not match the reviewed table")
    return resolved


def _request(batch_id: str, day: str, config_version: str) -> AtomicBatchRequest:
    return AtomicBatchRequest(
        batch_id=batch_id,
        caller_hash=_hash("issue-896-sandbox-runner"),
        idempotency_key_hash=_hash(batch_id),
        request_fingerprint=_hash(f"payload:{batch_id}"),
        coin="BTC",
        snapshot_id=f"snap-{batch_id}",
        day=day,
        batch_cost_usd=Decimal("0.00001"),
        config_version=config_version,
        created_at=int(datetime.now(UTC).timestamp()),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-sandbox", action="store_true")
    args = parser.parse_args()
    if not args.confirm_sandbox:
        parser.error("--confirm-sandbox is required")

    import boto3

    allowlist = _load_allowlist()
    session = boto3.Session(region_name=allowlist["region"])
    _verify_identity(
        sts_client=session.client("sts"),
        expected_account=allowlist["account_id"],
        expected_role_arn=allowlist["caller_arn"],
    )
    table_name = _table_name_from_arn(
        allowlist["table_arn"],
        account=allowlist["account_id"],
        region=allowlist["region"],
    )
    dynamodb = session.client("dynamodb")
    _verify_table_safety(
        dynamodb_client=dynamodb,
        table_name=table_name,
        expected_table_arn=allowlist["table_arn"],
    )
    store = DynamoDBAtomicMultiAngleBatchStore(
        client=dynamodb, table_name=table_name
    )
    day = datetime.now(UTC).date().isoformat()
    first = _request(f"spike-{uuid.uuid4()}", day, allowlist["config_version"])
    admitted = store.create_batch(first)
    replay = store.create_batch(first)  # consistent manifest read is mandatory
    competitor = store.create_batch(
        _request(f"spike-{uuid.uuid4()}", day, allowlist["config_version"])
    )
    if not (
        admitted.admitted
        and len(admitted.job_ids) == 5
        and replay.replayed
        and replay.job_ids == admitted.job_ids
        and not competitor.admitted
    ):
        raise RuntimeError("sandbox atomic admission/replay/competition proof failed")
    print(
        f"proof=passed batch_id={admitted.batch_id} jobs=5 "
        "replay=verified competitor=denied"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
