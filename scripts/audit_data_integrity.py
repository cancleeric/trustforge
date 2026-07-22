#!/usr/bin/env python3
"""Run the read-only five-coin OHLCV checksum and contract audit."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trustforge.data_integrity import DataIntegrityError, audit_ohlcv_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        audit = audit_ohlcv_dataset(args.repo_root)
    except DataIntegrityError as exc:
        print(f"FAIL CLOSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ohlcv_audit": audit}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
