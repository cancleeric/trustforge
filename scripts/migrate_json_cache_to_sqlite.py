#!/usr/bin/env python3
"""One-time migration from the legacy local JSON cache into SQLite."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from trustforge.ingestion.cache import (  # noqa: E402
    SQLiteCacheBackend,
    _default_json_path,
    _default_sqlite_path,
)


def migrate(json_path: Path, sqlite_path: Path) -> tuple[int, int]:
    if not json_path.exists():
        raise FileNotFoundError(json_path)
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("JSON cache root must be an object")

    target = SQLiteCacheBackend(sqlite_path)
    migrated = 0
    skipped = 0
    try:
        for key, entry in raw.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                skipped += 1
                continue
            docs = entry.get("docs")
            fetched_at = entry.get("fetched_at")
            if not isinstance(docs, list) or fetched_at is None:
                skipped += 1
                continue
            try:
                target.set_if_newer(key, docs, float(fetched_at))
            except (TypeError, ValueError):
                skipped += 1
                continue
            migrated += 1
    finally:
        target.close()
    return migrated, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=_default_json_path())
    parser.add_argument("--sqlite", type=Path, default=_default_sqlite_path())
    args = parser.parse_args()
    migrated, skipped = migrate(args.json, args.sqlite)
    print(f"migrated={migrated} skipped={skipped} sqlite={args.sqlite}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
