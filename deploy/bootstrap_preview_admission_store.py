#!/usr/bin/env python3
"""Idempotently create only the non-secret preview control rows."""

from __future__ import annotations

import argparse


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
    if type(initial_shard) is not int or initial_shard < 0:
        raise ValueError("invalid initial shard")
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", required=True)
    parser.add_argument("--initial-shard", required=True, type=int)
    parser.add_argument("--region")
    args = parser.parse_args()
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "dynamodb",
        region_name=args.region,
        config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
    )
    bootstrap(client, args.table, args.initial_shard)


if __name__ == "__main__":
    main()
