#!/usr/bin/env python3
"""Emit a source-by-coin cache freshness dashboard JSON artifact."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from trustforge.freshness import dashboard  # noqa: E402
from trustforge.scheduler_log import get_recent_scheduler_runs  # noqa: E402
from trustforge.ingestion.cache import get_cache_backend  # noqa: E402
from trustforge.schema import COIN_POOL  # noqa: E402
from fetch_scheduler import build_registry  # noqa: E402

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO / "out" / "cache-freshness-latest.json")
    parser.add_argument("--recent-runs", type=int, default=30)
    args = parser.parse_args(argv)
    result = dashboard(get_cache_backend(), COIN_POOL, build_registry(), runs=get_recent_scheduler_runs(args.recent_runs))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
