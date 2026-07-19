#!/usr/bin/env python3
"""Local health watchdog for the independent CEO development cycle."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_MAX_AGE = timedelta(minutes=40)
FUTURE_TOLERANCE = timedelta(minutes=5)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("updated_at is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("updated_at has no timezone")
    return parsed.astimezone(timezone.utc)


def health_diagnostics(
    status_path: Path,
    *,
    now: datetime,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> dict:
    now = now.astimezone(timezone.utc)
    if not status_path.exists():
        return {"severity": "critical", "reason": "status_missing", "status_path": str(status_path)}
    try:
        status = json.loads(status_path.read_text())
        if not isinstance(status, dict):
            raise ValueError("status root is not an object")
        updated_at = _parse_timestamp(status.get("updated_at"))
        mtime = datetime.fromtimestamp(status_path.stat().st_mtime, timezone.utc)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "severity": "critical",
            "reason": "status_corrupt",
            "error": str(exc)[:500],
            "status_path": str(status_path),
        }
    if updated_at > now + FUTURE_TOLERANCE or mtime > now + FUTURE_TOLERANCE:
        return {
            "severity": "critical",
            "reason": "status_timestamp_in_future",
            "updated_at": updated_at.isoformat(),
            "mtime": mtime.isoformat(),
            "status_path": str(status_path),
        }
    age_seconds = max(0.0, (now - updated_at).total_seconds(), (now - mtime).total_seconds())
    return {
        "severity": "critical" if age_seconds > max_age.total_seconds() else "healthy",
        "reason": "status_stale" if age_seconds > max_age.total_seconds() else "status_fresh",
        "age_seconds": age_seconds,
        "updated_at": updated_at.isoformat(),
        "mtime": mtime.isoformat(),
        "status_path": str(status_path),
    }


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


def run_watchdog(
    status_path: Path,
    alert_path: Path,
    *,
    now: datetime,
    notify: bool = False,
) -> dict:
    diagnostics = health_diagnostics(status_path, now=now)
    diagnostics["checked_at"] = now.astimezone(timezone.utc).isoformat()
    if diagnostics["severity"] == "critical":
        _atomic_write(alert_path, diagnostics)
        if notify and shutil.which("logger"):
            subprocess.run(
                ["logger", "-t", "trustforge-ceo-watchdog", f"CRITICAL {diagnostics['reason']} {status_path}"],
                check=False,
            )
    elif alert_path.exists():
        alert_path.unlink()
    return diagnostics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--alert", type=Path, required=True)
    args = parser.parse_args()
    diagnostics = run_watchdog(args.status, args.alert, now=datetime.now(timezone.utc), notify=True)
    print(json.dumps(diagnostics, separators=(",", ":")))
    return 1 if diagnostics["severity"] == "critical" else 0


if __name__ == "__main__":
    raise SystemExit(main())
