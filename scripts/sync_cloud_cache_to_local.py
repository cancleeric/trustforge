#!/usr/bin/env python3
"""Copy the DynamoDB connector cache into the local JSON development cache.

This is a read-only cloud operation. The destination is replaced atomically so
the local web process never observes a partially synchronized cache.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "ap-southeast-2"))
    parser.add_argument(
        "--table",
        default=os.getenv("TRUSTFORGE_CACHE_TABLE", "trustforge-connector-cache"),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("out/connector_cache/connector_cache.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    import boto3

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    items: list[dict[str, Any]] = []
    scan_args: dict[str, Any] = {
        "ProjectionExpression": "source_id, coin, docs_json, fetched_at",
    }
    while True:
        response = table.scan(**scan_args)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_args["ExclusiveStartKey"] = last_key

    local: dict[str, dict[str, Any]] = {}
    for item in items:
        source = str(item.get("source_id", ""))
        coin = str(item.get("coin", ""))
        fetched_at = item.get("fetched_at")
        if not source or fetched_at is None:
            continue
        try:
            docs = json.loads(str(item.get("docs_json", "[]")))
        except json.JSONDecodeError:
            continue
        if not isinstance(docs, list):
            continue
        local[f"{source}:{coin.upper()}"] = {
            "docs": docs,
            "fetched_at": float(fetched_at),
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=args.out.parent, prefix=f".{args.out.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(local, handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_name, args.out)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise

    print(f"synced {len(local)} cache entries to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
