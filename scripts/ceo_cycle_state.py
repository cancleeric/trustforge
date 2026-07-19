#!/usr/bin/env python3
"""Atomically persist CEO cycle outcomes and development-stall alerts."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import re
from datetime import datetime, timezone
from pathlib import Path

SENSITIVE_PATTERN = re.compile(
    r"(?i)(?:bearer\s+\S+|gh[opsu]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|AKIA[A-Z0-9]{16}|(?:token|password|secret|api[_-]?key)\s*[:=]\s*\S+)"
)
SENSITIVE_KEY_PATTERN = re.compile(r"(?:token|secret|password|authorization|api[_-]?key)", re.IGNORECASE)


def _sanitize(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SENSITIVE_KEY_PATTERN.search(str(key)) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return SENSITIVE_PATTERN.sub("[REDACTED]", value)
    return value


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"refusing symlink status path: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(_sanitize(value), handle, ensure_ascii=False, indent=2)
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
        "completed": [],
        "failed": [],
        "process_success": process_success,
        "load_diagnostics": load_diagnostics,
    }
    for line in events.splitlines():
        kind, issue_text, *detail = line.split("\t")
        issue = int(issue_text) if issue_text else 0
        if kind in ("selected", "dispatched", "progress"):
            payload[kind].append(issue)
        elif kind in ("completed", "failed") and detail:
            item = json.loads(detail[0])
            item["issue"] = issue
            payload[kind].append(item)
        elif kind == "blocked" and detail and detail[0].startswith("{"):
            diagnostics = json.loads(detail[0])
            diagnostics["issue"] = issue or None
            payload[kind].append(diagnostics)
        elif kind in ("skipped", "blocked"):
            payload[kind].append({"issue": issue or None, "reason": detail[0] if detail else "unknown"})
    return payload


def mark_active(path: Path, *, pid: int, started_at: str, heartbeat_path: Path) -> dict:
    previous = _read(path)
    previous.update(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "active": {
                "pid": pid,
                "started_at": started_at,
                "heartbeat_path": str(heartbeat_path),
            },
        }
    )
    _atomic_write(path, previous)
    return previous


def record_cycle(
    path: Path,
    *,
    selected: list[int],
    dispatched: list[int],
    skipped: list[dict],
    blocked: list[dict],
    process_success: bool,
    progress: list[int],
    completed: list[dict] | None = None,
    failed: list[dict] | None = None,
    load_diagnostics: dict,
) -> dict:
    completed = completed or []
    failed = failed or []
    previous = _read(path)
    zero_dispatch = int(previous.get("consecutive_zero_dispatch", 0)) + 1 if not dispatched else 0
    no_progress = int(previous.get("consecutive_no_progress", 0)) + 1 if not progress else 0
    longest_streak = max(zero_dispatch, no_progress)
    severity = "critical" if longest_streak >= 3 else ("warning" if longest_streak >= 2 else None)
    previous_history = previous.get("history", {}) if isinstance(previous.get("history"), dict) else {}
    commit_times = [item.get("commit_at") for item in completed if item.get("commit_at")]
    history = {
        "last_dispatch_at": max(
            [item.get("dispatched_at") for item in completed + failed if item.get("dispatched_at")],
            default=previous_history.get("last_dispatch_at"),
        ),
        "last_commit_at": max(commit_times, default=previous_history.get("last_commit_at")),
        "last_pr_at": previous_history.get("last_pr_at"),
    }
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
        "completed": completed,
        "failed": failed,
        "process_success": process_success,
        "development_progress": bool(progress),
        "load_diagnostics": load_diagnostics,
        "consecutive_zero_dispatch": zero_dispatch,
        "consecutive_no_progress": no_progress,
        "watchdog_severity": severity,
        "history": history,
        "active": False,
    }
    state = _sanitize(state)
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
    parser.add_argument("--events", type=Path)
    parser.add_argument("--process-success", choices=("true", "false"))
    parser.add_argument("--load-diagnostics")
    parser.add_argument("--mark-active", action="store_true")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--started-at")
    parser.add_argument("--heartbeat-path", type=Path)
    args = parser.parse_args()
    if args.mark_active:
        if args.pid is None or not args.started_at or args.heartbeat_path is None:
            parser.error("--mark-active requires --pid, --started-at and --heartbeat-path")
        print(json.dumps(mark_active(args.status, pid=args.pid, started_at=args.started_at, heartbeat_path=args.heartbeat_path), separators=(",", ":")))
        return 0
    if args.events is None or args.process_success is None or args.load_diagnostics is None:
        parser.error("cycle completion requires --events, --process-success and --load-diagnostics")
    payload = payload_from_events(
        args.events.read_text(),
        process_success=args.process_success == "true",
        load_diagnostics=json.loads(args.load_diagnostics),
    )
    print(json.dumps(record_cycle(args.status, **payload), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
