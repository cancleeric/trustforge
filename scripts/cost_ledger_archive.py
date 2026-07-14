#!/usr/bin/env python3
"""Create, verify, or drill-restore TrustForge's append-only cost ledger."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.ledger import get_ledger  # noqa: E402
from trustforge.ledger_archive import export_csv, export_jsonl, restore_jsonl_archive, verify_jsonl_archive  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="write a ledger archive and manifest")
    export.add_argument("--format", choices=("jsonl", "csv"), default="jsonl")
    export.add_argument("--out", type=Path, required=True)
    verify = commands.add_parser("verify", help="verify a JSONL archive against its manifest")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--manifest", type=Path)
    restore = commands.add_parser("restore-drill", help="restore a verified JSONL archive to a new local file")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--manifest", type=Path)
    restore.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "export":
        result = export_jsonl(get_ledger(), args.out) if args.format == "jsonl" else export_csv(get_ledger(), args.out)
    elif args.command == "verify":
        result = verify_jsonl_archive(args.archive, args.manifest)
    else:
        result = restore_jsonl_archive(args.archive, args.out, manifest_path=args.manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
