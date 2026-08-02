#!/usr/bin/env python3
"""Provision the production formal-run authority without exposing secret values."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

SECRET_NAMES = (
    "TRUSTFORGE_FORMAL_CALLER_SECRET",
    "TRUSTFORGE_FORMAL_IDEMPOTENCY_SECRET",
    "TRUSTFORGE_FORMAL_RETENTION_SECRET",
    "TRUSTFORGE_FORMAL_FINGERPRINT_SECRET",
    "TRUSTFORGE_FORMAL_CONTENT_SECRET",
)


def _require_authorization(path: Path) -> None:
    if not re.fullmatch(r"eric-auth-\d{8}-trustforge-formal-run-prod\.token", path.name):
        raise SystemExit("invalid Eric authorization filename")
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SystemExit("Eric authorization must be a regular non-symlink file")
    if info.st_uid != os.getuid():
        raise SystemExit("Eric authorization owner mismatch")


def _ensure_table(client, table: str) -> None:
    try:
        description = client.describe_table(TableName=table)["Table"]
    except client.exceptions.ResourceNotFoundException:
        client.create_table(
            TableName=table,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            SSESpecification={"Enabled": True},
        )
        client.get_waiter("table_exists").wait(TableName=table)
        description = client.describe_table(TableName=table)["Table"]
    expected = [("pk", "HASH"), ("sk", "RANGE")]
    actual = [(item["AttributeName"], item["KeyType"]) for item in description["KeySchema"]]
    if actual != expected or description.get("BillingModeSummary", {}).get("BillingMode") != "PAY_PER_REQUEST":
        raise SystemExit("formal-run table schema/billing contract mismatch")
    if description.get("SSEDescription", {}).get("Status") not in {"ENABLED", "ENABLING"}:
        raise SystemExit("formal-run table encryption contract mismatch")
    ttl = client.describe_time_to_live(TableName=table).get("TimeToLiveDescription", {})
    if ttl.get("TimeToLiveStatus") not in {"ENABLED", "ENABLING"}:
        client.update_time_to_live(
            TableName=table,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
        )
    elif ttl.get("AttributeName") != "expires_at":
        raise SystemExit("formal-run table TTL attribute mismatch")
    client.update_continuous_backups(
        TableName=table,
        PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
    )
    recovery = client.describe_continuous_backups(TableName=table)["ContinuousBackupsDescription"]
    if recovery.get("PointInTimeRecoveryDescription", {}).get("PointInTimeRecoveryStatus") not in {
        "ENABLED", "ENABLING",
    }:
        raise SystemExit("formal-run table PITR contract mismatch")


def _ensure_parameters(client, prefix: str) -> list[dict[str, object]]:
    result = []
    for logical_name in SECRET_NAMES:
        name = f"{prefix}/{logical_name}"
        try:
            current = client.get_parameter(Name=name, WithDecryption=False)["Parameter"]
            if current.get("Type") != "SecureString":
                raise SystemExit(f"formal-run parameter type mismatch: {name}")
            result.append({"name": name, "version": current["Version"], "created": False})
            continue
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ParameterNotFound":
                raise
        response = client.put_parameter(
            Name=name,
            Description="TrustForge formal-run production HMAC authority",
            Type="SecureString",
            Value=secrets.token_urlsafe(48),
            Overwrite=False,
        )
        result.append({"name": name, "version": response["Version"], "created": True})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-west-2"))
    parser.add_argument("--table", default="trustforge-formal-run")
    parser.add_argument("--prefix", default="/trustforge/runtime")
    parser.add_argument("--role", default="trustforge-ec2")
    args = parser.parse_args()
    _require_authorization(args.authorization)
    if args.region not in {"us-west-2", "us-east-1"}:
        raise SystemExit("competition production region is not allowed")
    session = boto3.Session(region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    dynamodb = session.client("dynamodb")
    _ensure_table(dynamodb, args.table)
    parameters = _ensure_parameters(session.client("ssm"), args.prefix.rstrip("/"))
    table_arn = f"arn:aws:dynamodb:{args.region}:{account}:table/{args.table}"
    session.client("iam").put_role_policy(
        RoleName=args.role,
        PolicyName="trustforge-formal-run",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": [
                    "dynamodb:DescribeTable", "dynamodb:GetItem", "dynamodb:PutItem",
                    "dynamodb:UpdateItem", "dynamodb:DeleteItem",
                    "dynamodb:TransactGetItems", "dynamodb:TransactWriteItems",
                ],
                "Resource": table_arn,
            }],
        }, separators=(",", ":")),
    )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "trustforge.formal-run-production-setup/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "account": account,
        "region": args.region,
        "table": args.table,
        "table_arn": table_arn,
        "ttl_attribute": "expires_at",
        "pitr": True,
        "parameters": parameters,
        "secret_values_recorded": False,
        "iam_policy": "trustforge-formal-run",
        "scan_allowed": False,
    }
    args.receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(args.receipt, 0o600)
    print(args.receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
