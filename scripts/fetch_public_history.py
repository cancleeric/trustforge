#!/usr/bin/env python3
"""Fetch a supported public historical source into provenance-complete JSONL."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.historical_sources import parse_alternative_me_history, parse_sec_master_index  # noqa: E402
from trustforge.ingestion.safe_fetch import fetch_url  # noqa: E402


def _boundary(value: str, *, end: bool = False) -> float:
    moment = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    if end:
        moment = moment.replace(hour=23, minute=59, second=59)
    return moment.timestamp()


def _quarters(start: datetime, end: datetime):
    year, quarter = start.year, (start.month - 1) // 3 + 1
    end_pair = (end.year, (end.month - 1) // 3 + 1)
    while (year, quarter) <= end_pair:
        yield year, quarter
        quarter += 1
        if quarter == 5:
            year, quarter = year + 1, 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("alternative-me-fng", "sec-gov"), required=True)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--user-agent", default=os.getenv("TRUSTFORGE_SEC_USER_AGENT", ""),
                        help="SEC requires an identifying organization/contact user agent")
    args = parser.parse_args(argv)
    start, end = _boundary(args.from_date), _boundary(args.to_date, end=True)
    if end < start:
        parser.error("--to-date must be on or after --from-date")
    retrieved_at = time.time()
    if args.source == "alternative-me-fng":
        raw = fetch_url("https://api.alternative.me/fng/?limit=0", user_agent="TrustForge/1.0 historical-research", timeout=15, max_bytes=5_000_000)
        payload = json.loads(raw)
        rows = parse_alternative_me_history(payload, retrieved_at=retrieved_at, start_epoch=start, end_epoch=end)
    else:
        if not args.user_agent.strip():
            parser.error("--user-agent or TRUSTFORGE_SEC_USER_AGENT is required for sec-gov")
        rows = []
        start_date = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
        end_date = datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)
        for year, quarter in _quarters(start_date, end_date):
            url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx"
            raw = fetch_url(url, user_agent=args.user_agent.strip(), timeout=30, max_bytes=32_000_000)
            rows.extend(parse_sec_master_index(raw.decode("latin-1"), retrieved_at=retrieved_at, start_epoch=start, end_epoch=end))
        rows.sort(key=lambda row: (row["published_at"], row["coin"], row.get("accession", "")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"source": args.source, "rows": len(rows), "days": len({row["published_at"][:10] for row in rows}), "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
