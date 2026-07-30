"""Append-only, approval-gated change history for mutable Hermes skills."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - production and supported local hosts are POSIX.
    fcntl = None  # type: ignore[assignment]


def _home() -> Path:
    return Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2])))


def default_log_path() -> Path:
    return Path(os.getenv("TRUSTFORGE_SKILL_CHANGE_LOG", str(_home() / "out" / "skill_changes.jsonl")))


def _canonical_log_path(path: Path) -> Path:
    """Follow the legacy compatibility symlink before selecting its lock file."""
    return path.expanduser().resolve(strict=False)


@contextmanager
def _locked_log(path: Path) -> Iterator[Path]:
    """Serialize governance writes with the deployment reconciler."""
    if fcntl is None:
        raise RuntimeError("skill change log locking requires POSIX fcntl support")

    requested = path.expanduser()
    while True:
        target = _canonical_log_path(requested)
        lock_paths = sorted(
            {
                candidate.with_name(f"{candidate.name}.lock")
                for candidate in (requested, target)
            },
            key=str,
        )
        handles = []
        try:
            for lock_path in lock_paths:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                handle = lock_path.open("a+", encoding="utf-8")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handles.append(handle)

            locked_target = _canonical_log_path(requested)
            if locked_target != target:
                continue
            yield locked_target
            return
        finally:
            for handle in reversed(handles):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _append_unlocked(record: dict[str, Any], target: Path) -> dict[str, Any]:
    record = {"event_id": uuid.uuid4().hex, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record}
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def _append(record: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    with _locked_log(path or default_log_path()) as target:
        return _append_unlocked(record, target)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def stage(skill_id: str, content: str, summary: str, *, log_path: Path | None = None) -> dict[str, Any]:
    """Record a candidate skill revision; it is inert until explicitly approved."""
    if not skill_id or not summary:
        raise ValueError("skill_id and summary are required")
    return _append({"action": "staged", "skill_id": skill_id, "skill_hash": content_hash(content), "summary": summary, "approval_required": True}, log_path)


def approve(skill_id: str, skill_hash: str, evidence: dict[str, Any], *, log_path: Path | None = None) -> dict[str, Any]:
    """Activate a previously staged revision only when QA/replay evidence is named."""
    if not evidence:
        raise ValueError("approval requires validation evidence")
    with _locked_log(log_path or default_log_path()) as target:
        records = _read(target)
        if not any(r.get("action") == "staged" and r.get("skill_id") == skill_id and r.get("skill_hash") == skill_hash for r in records):
            raise ValueError("only a recorded staged revision can be approved")
        active = active_revision(skill_id, records)
        return _append_unlocked({"action": "approved", "skill_id": skill_id, "skill_hash": skill_hash, "previous_hash": active, "evidence": evidence}, target)


def rollback(skill_id: str, target_hash: str, reason: str, *, log_path: Path | None = None) -> dict[str, Any]:
    """Switch the active revision pointer to an earlier approved revision."""
    with _locked_log(log_path or default_log_path()) as target:
        records = _read(target)
        if not any(r.get("action") == "approved" and r.get("skill_id") == skill_id and r.get("skill_hash") == target_hash for r in records):
            raise ValueError("rollback target must be a previously approved revision")
        return _append_unlocked({"action": "rolled_back", "skill_id": skill_id, "skill_hash": target_hash, "previous_hash": active_revision(skill_id, records), "reason": reason}, target)


def active_revision(skill_id: str, records: list[dict[str, Any]] | None = None, *, log_path: Path | None = None) -> str | None:
    for record in reversed(records if records is not None else _read(_canonical_log_path(log_path or default_log_path()))):
        if record.get("skill_id") == skill_id and record.get("action") in {"approved", "rolled_back"}:
            return str(record.get("skill_hash"))
    return None


def change_history(*, log_path: Path | None = None) -> list[dict[str, Any]]:
    """Return the append-only outer-skill history for read-only control planes."""
    return _read(_canonical_log_path(log_path or default_log_path()))
