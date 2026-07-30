#!/usr/bin/env python3
"""Idempotently create only the non-secret preview control rows."""

from __future__ import annotations

import argparse
import re

MAX_EPOCH_MINUTE = 4_223_371_679
TABLE_RE = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")


def verify_store(client: object, table: str, table_arn: str, kms_key_arn: str) -> None:
    described = client.describe_table(TableName=table)["Table"]
    ttl = client.describe_time_to_live(TableName=table)["TimeToLiveDescription"]
    backups = client.describe_continuous_backups(TableName=table)[
        "ContinuousBackupsDescription"
    ]
    tags = client.list_tags_of_resource(ResourceArn=table_arn)["Tags"]
    if (
        described.get("TableName") != table
        or described.get("TableArn") != table_arn
        or described.get("TableStatus") != "ACTIVE"
        or described.get("BillingModeSummary", {}).get("BillingMode")
        != "PAY_PER_REQUEST"
        or described.get("KeySchema")
        != [
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ]
        or described.get("AttributeDefinitions")
        != [
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ]
        or described.get("SSEDescription", {}).get("KMSMasterKeyArn")
        != kms_key_arn
        or described.get("SSEDescription", {}).get("Status") != "ENABLED"
        or described.get("SSEDescription", {}).get("SSEType") != "KMS"
        or ttl
        != {"TimeToLiveStatus": "ENABLED", "AttributeName": "ttl"}
        or backups.get("PointInTimeRecoveryDescription", {}).get(
            "PointInTimeRecoveryStatus"
        )
        != "ENABLED"
        or backups.get("ContinuousBackupsStatus") != "ENABLED"
        or {
            item.get("Key"): item.get("Value")
            for item in tags
            if type(item) is dict
        }.get("TrustForgeComponent")
        != "preview-admission"
    ):
        raise RuntimeError("preview store verification failed")


def _put_if_absent(client: object, table: str, item: dict[str, object]) -> None:
    try:
        client.put_item(
            TableName=table,
            Item=item,
            ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
        )
    except Exception as exc:
        response = getattr(exc, "response", {})
        code = response.get("Error", {}).get("Code") if type(response) is dict else None
        if code != "ConditionalCheckFailedException":
            raise
        existing = client.get_item(
            TableName=table,
            Key={"pk": item["pk"], "sk": item["sk"]},
            ConsistentRead=True,
        )
        if existing.get("Item") != item:
            raise RuntimeError("existing bootstrap row does not match") from None


def bootstrap(client: object, table: str, initial_shard: int) -> None:
    if (
        type(table) is not str
        or not TABLE_RE.fullmatch(table)
        or type(initial_shard) is not int
        or not 0 <= initial_shard <= MAX_EPOCH_MINUTE
    ):
        raise ValueError("invalid bootstrap target")
    _put_if_absent(
        client,
        table,
        {
            "pk": {"S": "PAP#1#CONTROL"},
            "sk": {"S": "ADMISSION#QUARANTINE"},
            "kind": {"S": "preview_admission_quarantine"},
            "schema_version": {"N": "1"},
            "state": {"S": "open"},
            "generation": {"N": "0"},
            "version": {"N": "0"},
        },
    )
    _put_if_absent(
        client,
        table,
        {
            "pk": {"S": "PAP#1#RECOVERY"},
            "sk": {"S": "LEASE#WATERMARK"},
            "kind": {"S": "preview_recovery_watermark"},
            "schema_version": {"N": "1"},
            "version": {"N": "0"},
            "shard": {"N": str(initial_shard)},
        },
    )


def verify_and_bootstrap(
    client: object,
    table: str,
    table_arn: str,
    kms_key_arn: str,
    initial_shard: int,
) -> None:
    verify_store(client, table, table_arn, kms_key_arn)
    bootstrap(client, table, initial_shard)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-aws", action="store_true")
    parser.add_argument("--table", required=True)
    parser.add_argument("--table-arn", required=True)
    parser.add_argument("--table-kms-key-arn", required=True)
    parser.add_argument("--initial-shard", required=True, type=int)
    parser.add_argument("--region")
    args = parser.parse_args()
    if not args.allow_aws:
        parser.error("--allow-aws is required")
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "dynamodb",
        region_name=args.region,
        config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
    )
    verify_and_bootstrap(
        client,
        args.table,
        args.table_arn,
        args.table_kms_key_arn,
        args.initial_shard,
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        print("preview_admission_bootstrap=failed")
        raise SystemExit(1) from None
