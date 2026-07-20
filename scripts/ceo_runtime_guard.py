#!/usr/bin/env python3
"""Security and lifecycle primitives for the local CEO runner."""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import tempfile
import re
from datetime import datetime, timezone
from pathlib import Path

SENSITIVE_PATTERN = re.compile(
    r"(?i)(?:bearer\s+\S+|gh[opsu]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|AKIA[A-Z0-9]{16}|(?:token|password|secret|api[_-]?key)\s*[:=]\s*\S+)"
)


def canonical_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    resolved = path.resolve(strict=True)
    if absolute != resolved:
        raise ValueError(f"path is not canonical or contains a symlink: {path}")
    return resolved


def secure_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError(f"directory is a symlink: {path}")
    if path.exists():
        resolved = canonical_path(path)
    else:
        parent = secure_directory(path.parent)
        path = parent / path.name
        path.mkdir(mode=0o700)
        resolved = canonical_path(path)
    os.chmod(resolved, 0o700)
    return resolved


def secure_file(path: Path) -> Path:
    parent = secure_directory(path.parent)
    destination = parent / path.name
    if destination.is_symlink():
        raise ValueError(f"file is a symlink: {destination}")
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    os.chmod(destination, 0o600)
    return destination


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def validate_lane(repo_root: Path, lane: Path) -> dict:
    root = canonical_path(repo_root)
    candidate = canonical_path(lane)
    if lane.is_symlink() or candidate.parent != canonical_path(lane.parent):
        raise ValueError("lane path is a symlink or outside its canonical parent")
    root_common = Path(_git(root, "rev-parse", "--git-common-dir"))
    lane_common = Path(_git(candidate, "rev-parse", "--git-common-dir"))
    if not root_common.is_absolute():
        root_common = root / root_common
    if not lane_common.is_absolute():
        lane_common = candidate / lane_common
    if root_common.resolve() != lane_common.resolve():
        raise ValueError("lane git-common-dir does not match repository")
    root_remote = _git(root, "remote", "get-url", "origin")
    lane_remote = _git(candidate, "remote", "get-url", "origin")
    if root_remote != lane_remote:
        raise ValueError("lane origin URL does not match repository")
    return {"valid": True, "path": str(candidate), "git_common_dir": str(root_common.resolve()), "origin": root_remote}


def _atomic_json(path: Path, value: dict) -> None:
    parent = secure_directory(path.parent)
    if path.is_symlink():
        raise ValueError(f"destination is a symlink: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def acquire_lock(lock_dir: Path, *, pid: int, now: datetime, stale_seconds: int) -> dict:
    try:
        lock_dir.mkdir(mode=0o700)
    except FileExistsError:
        if lock_dir.is_symlink() or not lock_dir.is_dir():
            raise ValueError("lock path is unsafe")
        metadata_path = lock_dir / "active.json"
        try:
            metadata = json.loads(metadata_path.read_text())
            owner_pid = int(metadata["pid"])
            heartbeat = datetime.fromisoformat(metadata["heartbeat_at"])
            alive = owner_pid > 1 and _pid_alive(owner_pid)
            stale = (now.astimezone(timezone.utc) - heartbeat.astimezone(timezone.utc)).total_seconds() > stale_seconds
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"existing lock metadata is invalid: {exc}") from exc
        if alive or not stale:
            return {"acquired": False, "pid": owner_pid, "stale": stale, "alive": alive}
        metadata_path.unlink()
        lock_dir.rmdir()
        lock_dir.mkdir(mode=0o700)
    metadata = {
        "pid": pid,
        "started_at": now.astimezone(timezone.utc).isoformat(),
        "heartbeat_at": now.astimezone(timezone.utc).isoformat(),
    }
    _atomic_json(lock_dir / "active.json", metadata)
    return {"acquired": True, **metadata}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def heartbeat(lock_dir: Path, *, pid: int, now: datetime) -> dict:
    metadata_path = lock_dir / "active.json"
    metadata = json.loads(metadata_path.read_text())
    if int(metadata.get("pid", 0)) != pid:
        raise ValueError("lock PID changed")
    metadata["heartbeat_at"] = now.astimezone(timezone.utc).isoformat()
    _atomic_json(metadata_path, metadata)
    return metadata


def release_lock(lock_dir: Path, *, pid: int) -> None:
    metadata_path = lock_dir / "active.json"
    metadata = json.loads(metadata_path.read_text())
    if int(metadata.get("pid", 0)) != pid:
        raise ValueError("refusing to release a lock owned by another PID")
    metadata_path.unlink()
    lock_dir.rmdir()


def build_prompt(report_path: Path, base_prompt: Path, destination: Path, *, issue: int) -> dict:
    report = json.loads(report_path.read_text())
    item = next((candidate for candidate in report.get("execution_queue", []) if int(candidate.get("issue", 0)) == issue), None)
    issue_data = next((candidate for candidate in report.get("issues", []) if int(candidate.get("number", 0)) == issue), None)
    if item is None or issue_data is None:
        raise ValueError("issue is absent from trusted inventory snapshot")
    snapshot = {"queue": item, "issue": issue_data, "generated_at": report.get("generated_at")}
    content = (
        base_prompt.read_text()
        + "\n\nTRUSTED LOCAL SNAPSHOT (GitHub/network access is forbidden):\n```json\n"
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + "\n```\n"
    )
    parent = secure_directory(destination.parent)
    if destination.is_symlink():
        raise ValueError("prompt destination is a symlink")
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"prompt": str(destination), "issue": issue}


def redact_file(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("refusing to redact a symlink")
    content = path.read_text(errors="replace")
    redacted = SENSITIVE_PATTERN.sub("[REDACTED]", content)
    _atomic_text(path, redacted)


def classify_progress(*, agent_exit: int, before: str, after: str, descendant: bool, clean: bool) -> dict:
    if agent_exit != 0:
        return {"progress": False, "reason": f"agent_exit_{agent_exit}"}
    if not after or after == before:
        return {"progress": False, "reason": "no_new_commit"}
    if not descendant:
        return {"progress": False, "reason": "invalid_commit_history"}
    if not clean:
        return {"progress": False, "reason": "dirty_after_agent"}
    return {"progress": True, "reason": "new_verified_commit", "commit": after}


def _atomic_text(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=canonical_path(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--dir", type=Path, required=True)
    prepare.add_argument("--file", type=Path, action="append", default=[])
    lane = sub.add_parser("validate-lane")
    lane.add_argument("--root", type=Path, required=True)
    lane.add_argument("--lane", type=Path, required=True)
    lock = sub.add_parser("acquire-lock")
    lock.add_argument("--lock", type=Path, required=True)
    lock.add_argument("--pid", type=int, required=True)
    lock.add_argument("--stale-seconds", type=int, required=True)
    beat = sub.add_parser("heartbeat")
    beat.add_argument("--lock", type=Path, required=True)
    beat.add_argument("--pid", type=int, required=True)
    release = sub.add_parser("release-lock")
    release.add_argument("--lock", type=Path, required=True)
    release.add_argument("--pid", type=int, required=True)
    prompt = sub.add_parser("build-prompt")
    prompt.add_argument("--report", type=Path, required=True)
    prompt.add_argument("--base-prompt", type=Path, required=True)
    prompt.add_argument("--destination", type=Path, required=True)
    prompt.add_argument("--issue", type=int, required=True)
    redact = sub.add_parser("redact-file")
    redact.add_argument("--file", type=Path, required=True)
    classify = sub.add_parser("classify-progress")
    classify.add_argument("--agent-exit", type=int, required=True)
    classify.add_argument("--before", required=True)
    classify.add_argument("--after", required=True)
    classify.add_argument("--descendant", choices=("true", "false"), required=True)
    classify.add_argument("--clean", choices=("true", "false"), required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = {"directory": str(secure_directory(args.dir)), "files": [str(secure_file(path)) for path in args.file]}
        elif args.command == "validate-lane":
            result = validate_lane(args.root, args.lane)
        elif args.command == "acquire-lock":
            result = acquire_lock(args.lock, pid=args.pid, now=datetime.now(timezone.utc), stale_seconds=args.stale_seconds)
        elif args.command == "heartbeat":
            result = heartbeat(args.lock, pid=args.pid, now=datetime.now(timezone.utc))
        elif args.command == "release-lock":
            release_lock(args.lock, pid=args.pid)
            result = {"released": True}
        elif args.command == "build-prompt":
            result = build_prompt(args.report, args.base_prompt, args.destination, issue=args.issue)
        elif args.command == "redact-file":
            redact_file(args.file)
            result = {"redacted": str(args.file)}
        else:
            result = classify_progress(
                agent_exit=args.agent_exit, before=args.before, after=args.after,
                descendant=args.descendant == "true", clean=args.clean == "true",
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, **result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
