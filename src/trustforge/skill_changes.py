"""Append-only, approval-gated change history for mutable Hermes skills."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


def _home() -> Path:
    return Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2])))


def default_log_path() -> Path:
    return Path(os.getenv("TRUSTFORGE_SKILL_CHANGE_LOG", str(_home() / "out" / "skill_changes.jsonl")))


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


def _append(record: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    target = path or default_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"event_id": uuid.uuid4().hex, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record}
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


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
    records = _read(log_path or default_log_path())
    if not any(r.get("action") == "staged" and r.get("skill_id") == skill_id and r.get("skill_hash") == skill_hash for r in records):
        raise ValueError("only a recorded staged revision can be approved")
    active = active_revision(skill_id, records)
    return _append({"action": "approved", "skill_id": skill_id, "skill_hash": skill_hash, "previous_hash": active, "evidence": evidence}, log_path)


def rollback(skill_id: str, target_hash: str, reason: str, *, log_path: Path | None = None) -> dict[str, Any]:
    """Switch the active revision pointer to an earlier approved revision."""
    records = _read(log_path or default_log_path())
    if not any(r.get("action") == "approved" and r.get("skill_id") == skill_id and r.get("skill_hash") == target_hash for r in records):
        raise ValueError("rollback target must be a previously approved revision")
    return _append({"action": "rolled_back", "skill_id": skill_id, "skill_hash": target_hash, "previous_hash": active_revision(skill_id, records), "reason": reason}, log_path)


def active_revision(skill_id: str, records: list[dict[str, Any]] | None = None, *, log_path: Path | None = None) -> str | None:
    for record in reversed(records if records is not None else _read(log_path or default_log_path())):
        if record.get("skill_id") == skill_id and record.get("action") in {"approved", "rolled_back"}:
            return str(record.get("skill_hash"))
    return None


def change_history(*, log_path: Path | None = None) -> list[dict[str, Any]]:
    """Return the append-only outer-skill history for read-only control planes."""
    return _read(log_path or default_log_path())
