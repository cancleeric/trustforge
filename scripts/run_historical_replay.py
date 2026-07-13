#!/usr/bin/env python3
"""Generate a leakage-safe historical replay and calibration diagnostic."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.calibration import replay_report  # noqa: E402
from trustforge.ingestion.cache import get_cache_backend, get_trust_history  # noqa: E402
from trustforge.ingestion.prices import load_ohlcv  # noqa: E402
from trustforge.schema import COIN_POOL  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TrustForge historical replay from point-in-time snapshots")
    parser.add_argument("--coin", choices=COIN_POOL, required=True)
    parser.add_argument("--days", type=int, default=365, help="number of UTC snapshot days to inspect")
    parser.add_argument("--end-date", help="UTC YYYY-MM-DD end date; defaults to today")
    parser.add_argument("--data-dir", default=str(REPO / "data" / "data"))
    parser.add_argument("--out", help="optional JSON report path")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be >= 1")

    snapshots = get_trust_history(args.coin, args.days, get_cache_backend(), end_date=args.end_date)
    report = replay_report(args.coin, snapshots, load_ohlcv(args.coin, args.data_dir))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
