#!/usr/bin/env python3
"""Batch-replay archived daily source snapshots without any live fetches."""
from __future__ import annotations
import argparse, json, sys
from datetime import date
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from trustforge.historical_replay import replay_date_range
from trustforge.ingestion.cache import get_cache_backend
from trustforge.replay import load_source_snapshot

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coin", required=True)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--query", default="回放當日多源市場資訊")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    start, end = date.fromisoformat(args.from_date), date.fromisoformat(args.to_date)
    if end < start: parser.error("--to-date must be on or after --from-date")
    backend = get_cache_backend()
    results, skipped = replay_date_range(
        args.coin, start, end, query=args.query,
        load_snapshot=lambda coin, day: load_source_snapshot(
            backend, coin, day, archive_type="backfilled_archive",
        ),
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        day = str(result["snapshot_at"])[:10]
        (args.out_dir / f"{args.coin.lower()}-{day}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index = {"coin": args.coin, "from_date": args.from_date, "to_date": args.to_date, "replayed": len(results), "skipped": skipped}
    (args.out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(index, ensure_ascii=False))
    return 0
if __name__ == "__main__": raise SystemExit(main())
