#!/usr/bin/env python3
"""Offline validation for versioned asset-intrinsic records and PIT views."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from trustforge.asset_intrinsic import AssetIntrinsicRepository, load_asset_intrinsic_records


def _utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("as-of must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("as-of must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path)
    parser.add_argument(
        "--as-of",
        type=_utc_timestamp,
        help="PIT validation timestamp; defaults to the latest record fetched_at",
    )
    args = parser.parse_args(argv)
    try:
        records_path = args.records.resolve()
        records = load_asset_intrinsic_records(
            records_path, evidence_root=records_path.parent.parent
        )
        if not records:
            raise ValueError("asset intrinsic records must not be empty")
        as_of = args.as_of or max(record.fetched_at for record in records)
        repository = AssetIntrinsicRepository(records)
        asset_ids = sorted({record.profile.asset_id for record in records})
        views = {
            asset_id: repository.pit_view(asset_id, as_of)
            for asset_id in asset_ids
        }
        if not any(view is not None for view in views.values()):
            raise ValueError("no records are PIT-visible at requested as-of")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"asset intrinsic validation failed: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "ok": True,
                "records": len(records),
                "assets": len(asset_ids),
                "pit_visible_assets": sum(view is not None for view in views.values()),
                "as_of": as_of.isoformat().replace("+00:00", "Z"),
                "network_used": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
