#!/usr/bin/env python3
"""Hermes bounded autonomous research loop.

Cron/systemd invokes this script. It delegates fetching and snapshot creation to
the existing hardened scheduler; it does not create a second crawler or allow an
LLM to make unbounded network calls.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.execlog import RUNTIME_BUDGET_SEC
from trustforge.hermes import autonomy_enabled, autonomous_cycle_plan, manifest
from trustforge.runtime_control import runtime_control
from trustforge.schema import COIN_POOL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a bounded Hermes autonomous research cycle")
    parser.add_argument("--coin", action="append", choices=COIN_POOL, dest="coins")
    parser.add_argument("--dry-run", action="store_true", help="print the manifest and planned actions only")
    parser.add_argument("--max-budget-sec", type=int, default=RUNTIME_BUDGET_SEC)
    args = parser.parse_args(argv)
    if not 1 <= args.max_budget_sec <= RUNTIME_BUDGET_SEC:
        parser.error(f"--max-budget-sec must be 1..{RUNTIME_BUDGET_SEC}")
    control = runtime_control()
    if not control.enabled:
        print(f"[hermes_cycle] runtime disabled ({control.source}); no scheduled work executed")
        return 0
    enabled, source = autonomy_enabled()
    if not enabled:
        print(f"[hermes_cycle] autonomy disabled ({source}); no scheduled work executed")
        return 0
    plan = autonomous_cycle_plan(args.coins)
    plan["manifest"] = manifest()
    plan["max_budget_sec"] = args.max_budget_sec
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    started = time.monotonic()
    for action in plan["actions"]:
        remaining = args.max_budget_sec - (time.monotonic() - started)
        if remaining <= 0:
            print("[hermes_cycle] budget exhausted before action", file=sys.stderr)
            return 2
        result = subprocess.run(
            [sys.executable, *action["argv"]], cwd=REPO, timeout=max(1, int(remaining)), check=False,
        )
        if result.returncode:
            if action["tool"] == "refresh_sources":
                # Connectors can be partially unavailable while the archive and
                # remaining sources still produce an auditable usable snapshot.
                print(
                    f"[hermes_cycle] refresh_sources degraded ({result.returncode}); continuing",
                    file=sys.stderr,
                )
                continue
            print(f"[hermes_cycle] {action['tool']} failed ({result.returncode})", file=sys.stderr)
            return result.returncode
    print(f"[hermes_cycle] completed in {time.monotonic() - started:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
