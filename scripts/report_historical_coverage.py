#!/usr/bin/env python3
"""Write an auditable daily coverage report for historical source archives."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.historical_sources import historical_coverage_report  # noqa: E402
from trustforge.ingestion.cache import get_cache_backend  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = historical_coverage_report(
        get_cache_backend(), date.fromisoformat(args.from_date), date.fromisoformat(args.to_date),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "from_date": report["from_date"], "to_date": report["to_date"],
        "coins": {
            coin: {"snapshot_days": item["snapshot_days"], "snapshot_coverage": item["snapshot_coverage"]}
            for coin, item in report["coins"].items()
        },
        "out": str(args.out),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
