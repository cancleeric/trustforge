#!/usr/bin/env python3
"""Inspect or apply #885 atomic multi-angle batch reconciliation."""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from trustforge.multi_angle_batch_store import (
    DynamoDBAtomicMultiAngleBatchStore,
    SQLiteAtomicMultiAngleBatchStore,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run stale atomic batch reconciliation by default."
    )
    backend = parser.add_mutually_exclusive_group()
    backend.add_argument("--sqlite", type=Path, metavar="PATH")
    backend.add_argument("--dynamodb-table", metavar="TABLE")
    parser.add_argument("--region")
    parser.add_argument(
        "--allow-aws",
        action="store_true",
        help="required before the command may construct an AWS client",
    )
    parser.add_argument(
        "--stale-seconds", type=int, default=600, metavar="SECONDS"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform eligible settlements; omission is always dry-run",
    )
    return parser


def _emit(payload: dict[str, Any], *, stream=None) -> None:
    stream = stream or sys.stdout
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), file=stream)


def run(
    args: argparse.Namespace, *,
    sqlite_store_factory=None,
    dynamodb_store_factory=None,
    boto3_module=None,
) -> dict[str, Any]:
    if args.stale_seconds < 1:
        raise ValueError("--stale-seconds must be at least 1")
    if args.sqlite is None and args.dynamodb_table is None:
        raise ValueError("exactly one backend is required: --sqlite or --dynamodb-table")
    if args.sqlite is not None:
        sqlite_store_factory = (
            sqlite_store_factory or SQLiteAtomicMultiAngleBatchStore
        )
        if args.allow_aws or args.region:
            raise ValueError("--allow-aws/--region are invalid with --sqlite")
        if not args.sqlite.is_file():
            raise ValueError("--sqlite must identify an existing database file")
        backend = f"sqlite:{args.sqlite}"
        store = sqlite_store_factory(str(args.sqlite))
    else:
        dynamodb_store_factory = (
            dynamodb_store_factory or DynamoDBAtomicMultiAngleBatchStore
        )
        if not args.allow_aws:
            raise ValueError("--dynamodb-table requires explicit --allow-aws")
        if not args.region:
            raise ValueError("--dynamodb-table requires explicit --region")
        if boto3_module is None:
            import boto3 as boto3_module
        client = boto3_module.client("dynamodb", region_name=args.region)
        store = dynamodb_store_factory(
            client=client, table_name=args.dynamodb_table
        )
        backend = f"dynamodb:{args.region}:{args.dynamodb_table}"
    cutoff = int(time.time()) - args.stale_seconds
    report = store.reconcile_stale_batches(
        stale_before=cutoff, apply=args.apply
    )
    return {
        "status": "ok",
        "mode": "apply" if args.apply else "dry-run",
        "backend": backend,
        "stale_before": cutoff,
        "report": report,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        _emit(run(args))
        return 0
    except SystemExit:
        raise
    except ValueError as exc:
        _emit(
            {"status": "error", "error_type": "validation", "error": str(exc)},
            stream=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 — CLI boundary returns structured failure
        _emit(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            stream=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
