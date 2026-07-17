#!/usr/bin/env python3
"""Run a full deterministic Hermes replay from one archived daily snapshot."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from trustforge.historical_replay import replay_snapshot
from trustforge.ingestion.cache import get_cache_backend
from trustforge.replay import load_source_snapshot

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coin", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--query", default="回放當日多源市場資訊")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    snapshot = load_source_snapshot(
        get_cache_backend(), args.coin, args.date, archive_type="backfilled_archive",
    )
    if snapshot is None:
        parser.error("archived snapshot not found")
    result = replay_snapshot(snapshot, query=args.query)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0
if __name__ == "__main__": raise SystemExit(main())
