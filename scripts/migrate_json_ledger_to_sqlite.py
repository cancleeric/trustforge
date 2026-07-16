#!/usr/bin/env python3
"""將既有 JSONL 成本帳本一次性、可重跑地匯入 SQLite。"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from trustforge.ledger import JsonlLedger, SQLiteLedger


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()

    source = JsonlLedger(args.source)
    target = SQLiteLedger(args.database)
    imported = 0
    skipped = 0
    for record in source.read_all():
        try:
            target.append(record)
            imported += 1
        except sqlite3.IntegrityError:
            skipped += 1
    print(f"成本帳本匯入完成：新增 {imported}，已存在 {skipped}，總計 {len(target.read_all())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
