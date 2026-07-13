#!/usr/bin/env python3
"""Import licensed historical source records into leakage-safe daily archives.

Input is JSONL, one object per document: coin, source, published_at, retrieved_at,
text, url, provider and license. Current web search/RSS output is deliberately not accepted
as a substitute for a historical provider record.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from trustforge.ingestion.cache import get_cache_backend  # noqa: E402
from trustforge.replay import store_backfilled_source_snapshot  # noqa: E402
from trustforge.schema import COIN_POOL  # noqa: E402


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _content_hash(document: dict) -> str:
    payload = {key: value for key, value in document.items() if key != "content_sha256"}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--coin", choices=COIN_POOL, required=True)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    args = parser.parse_args(argv)
    start = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    providers: dict[str, dict] = {}
    for line in args.input.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        if str(raw.get("coin", "")).upper() != args.coin:
            continue
        for key in ("source", "published_at", "retrieved_at", "text", "provider", "license"):
            if not raw.get(key):
                raise ValueError(f"historical input missing {key}")
        published = _epoch(str(raw["published_at"]))
        _epoch(str(raw["retrieved_at"]))
        day = datetime.fromtimestamp(published, tz=timezone.utc).strftime("%Y-%m-%d")
        document = dict(raw)
        document["content_sha256"] = _content_hash(document)
        grouped[day][str(raw["source"])].append(document)
        providers[str(raw["provider"])] = {"provider": raw["provider"], "license": raw["license"]}
    backend = get_cache_backend(); date = start
    written = 0
    while date <= end:
        day = date.strftime("%Y-%m-%d"); boundary = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=timezone.utc).timestamp()
        sources = [{"source": source, "documents": docs} for source, docs in grouped.get(day, {}).items()]
        if sources:
            result = store_backfilled_source_snapshot(backend, args.coin, day, sources, snapshot_epoch=boundary, provider_manifest={"providers": sorted(providers.values(), key=lambda x: x["provider"])})
            if not result.ok:
                raise RuntimeError(f"durable write failed for {day}: {result.error}")
            written += 1
        from datetime import timedelta
        date += timedelta(days=1)
    print(json.dumps({"coin": args.coin, "archive_type": "backfilled_archive", "days_written": written}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
