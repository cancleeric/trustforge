#!/usr/bin/env python3
"""Report whether a CEO worktree is safe to reuse."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def blocking_status_entries(status: str) -> list[str]:
    blockers = []
    for entry in status.split("\0"):
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if code == "??" and (path == ".venv" or path.startswith(".venv/")):
            continue
        blockers.append(entry)
    return blockers


def lane_diagnostics(path: Path) -> dict:
    result = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=False,
    )
    blockers = blocking_status_entries(result.stdout) if result.returncode == 0 else [result.stderr.strip() or "git status failed"]
    return {"clean": not blockers, "blockers": blockers, "ignored": ["untracked root .venv/"], "path": str(path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(lane_diagnostics(args.path), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
