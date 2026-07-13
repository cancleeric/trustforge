#!/usr/bin/env python3
"""Stage, approve, or roll back mutable TrustForge skill revisions."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from trustforge.skill_changes import approve, rollback, stage  # noqa: E402
from trustforge.skills import canonical_json, skill_id_for, validate_artifact, write_artifact  # noqa: E402

def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    staged = sub.add_parser("stage"); staged.add_argument("skill_id"); staged.add_argument("file", type=Path); staged.add_argument("--summary", required=True)
    approved = sub.add_parser("approve"); approved.add_argument("skill_id"); approved.add_argument("skill_hash"); approved.add_argument("--evidence", required=True, help="JSON validation evidence")
    reverted = sub.add_parser("rollback"); reverted.add_argument("skill_id"); reverted.add_argument("skill_hash"); reverted.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if args.action == "stage":
        raw = args.file.read_text(encoding="utf-8")
        try:
            artifact = json.loads(raw)
        except json.JSONDecodeError:
            artifact = None
        if isinstance(artifact, dict) and "family" in artifact:
            validate_artifact(artifact)
            if args.skill_id != skill_id_for(str(artifact["family"])):
                parser.error("skill_id must match the artifact family")
            revision, stored = write_artifact(artifact)
            result = stage(args.skill_id, canonical_json(artifact), args.summary, log_path=args.log)
            if result["skill_hash"] != revision:
                raise RuntimeError("artifact and stage hashes differ")
            result["artifact_path"] = str(stored)
        else:
            result = stage(args.skill_id, raw, args.summary, log_path=args.log)
    elif args.action == "approve": result = approve(args.skill_id, args.skill_hash, json.loads(args.evidence), log_path=args.log)
    else: result = rollback(args.skill_id, args.skill_hash, args.reason, log_path=args.log)
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())
