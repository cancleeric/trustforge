#!/usr/bin/env python3
"""Fetch a supported public historical source into provenance-complete JSONL."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.historical_sources import parse_alternative_me_history  # noqa: E402
from trustforge.ingestion.safe_fetch import fetch_url  # noqa: E402


def _boundary(value: str, *, end: bool = False) -> float:
    moment = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    if end:
        moment = moment.replace(hour=23, minute=59, second=59)
    return moment.timestamp()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("alternative-me-fng",), required=True)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    start, end = _boundary(args.from_date), _boundary(args.to_date, end=True)
    if end < start:
        parser.error("--to-date must be on or after --from-date")
    retrieved_at = time.time()
    raw = fetch_url("https://api.alternative.me/fng/?limit=0", user_agent="TrustForge/1.0 historical-research", timeout=15, max_bytes=5_000_000)
    payload = json.loads(raw)
    rows = parse_alternative_me_history(payload, retrieved_at=retrieved_at, start_epoch=start, end_epoch=end)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"source": args.source, "rows": len(rows), "days": len({row["published_at"][:10] for row in rows}), "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
