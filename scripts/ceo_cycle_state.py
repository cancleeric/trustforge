#!/usr/bin/env python3
"""Atomically persist CEO cycle outcomes and development-stall alerts."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def payload_from_events(events: str, *, process_success: bool, load_diagnostics: dict) -> dict:
    payload = {
        "selected": [],
        "dispatched": [],
        "skipped": [],
        "blocked": [],
        "progress": [],
        "process_success": process_success,
        "load_diagnostics": load_diagnostics,
    }
    for line in events.splitlines():
        kind, issue_text, *detail = line.split("\t")
        issue = int(issue_text) if issue_text else 0
        if kind in ("selected", "dispatched", "progress"):
            payload[kind].append(issue)
        elif kind == "blocked" and detail and detail[0].startswith("{"):
            diagnostics = json.loads(detail[0])
            diagnostics["issue"] = issue or None
            payload[kind].append(diagnostics)
        elif kind in ("skipped", "blocked"):
            payload[kind].append({"issue": issue or None, "reason": detail[0] if detail else "unknown"})
    return payload


def record_cycle(
    path: Path,
    *,
    selected: list[int],
    dispatched: list[int],
    skipped: list[dict],
    blocked: list[dict],
    process_success: bool,
    progress: list[int],
    load_diagnostics: dict,
) -> dict:
    previous = _read(path)
    zero_dispatch = int(previous.get("consecutive_zero_dispatch", 0)) + 1 if not dispatched else 0
    no_progress = int(previous.get("consecutive_no_progress", 0)) + 1 if not progress else 0
    longest_streak = max(zero_dispatch, no_progress)
    severity = "critical" if longest_streak >= 3 else ("warning" if longest_streak >= 2 else None)
    state = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "selected": selected,
        "dispatched": dispatched,
        "skipped": skipped,
        "blocked": blocked,
        "inventory_errors": [
            error
            for item in blocked
            if item.get("reason") == "inventory_error"
            for error in item.get("errors", [])
        ],
        "progress": progress,
        "process_success": process_success,
        "development_progress": bool(progress),
        "load_diagnostics": load_diagnostics,
        "consecutive_zero_dispatch": zero_dispatch,
        "consecutive_no_progress": no_progress,
        "watchdog_severity": severity,
    }
    _atomic_write(path, state)
    alert = path.with_name("alert.json")
    if severity:
        _atomic_write(
            alert,
            {
                "updated_at": state["updated_at"],
                "severity": severity,
                "consecutive_zero_dispatch": zero_dispatch,
                "consecutive_no_progress": no_progress,
                "status": str(path),
            },
        )
    elif alert.exists():
        alert.unlink()
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--process-success", choices=("true", "false"), required=True)
    parser.add_argument("--load-diagnostics", required=True)
    args = parser.parse_args()
    payload = payload_from_events(
        args.events.read_text(),
        process_success=args.process_success == "true",
        load_diagnostics=json.loads(args.load_diagnostics),
    )
    print(json.dumps(record_cycle(args.status, **payload), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
