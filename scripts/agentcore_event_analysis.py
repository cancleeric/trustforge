#!/usr/bin/env python3
"""Run one AgentCore analysis pass; this script never installs a daemon."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from trustforge.agent.agentcore_event import changed_coins, run_changed_analyses


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="COIN=PATH",
        help="repeatable coin input path",
    )
    parser.add_argument("--state-file", type=Path)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="invoke AgentCore; without this flag only report changes",
    )
    args = parser.parse_args()

    sources: dict[str, Path] = {}
    for item in args.source:
        coin, separator, path = item.partition("=")
        if not separator or not coin or not path:
            parser.error("--source must use COIN=PATH")
        sources[coin.upper()] = Path(path)
    if not sources:
        parser.error("at least one --source is required")

    previous: dict[str, float] = {}
    if args.state_file and args.state_file.exists():
        previous = json.loads(args.state_file.read_text(encoding="utf-8"))

    if args.execute:
        receipt = run_changed_analyses(sources, previous=previous)
    else:
        changed, snapshot = changed_coins(sources, previous)
        receipt = {
            "kind": "agentcore_event_analysis_dry_run",
            "changed_coins": changed,
            "snapshot": snapshot,
            "results": [],
        }
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

