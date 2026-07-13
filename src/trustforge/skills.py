"""Immutable outer-skill registry for Hermes formal runs.

The Trust Layer is intentionally not configurable here.  Only the five outer
policy families may evolve, and a run resolves their exact content hash before
it starts so a later approval or rollback cannot rewrite its audit trail.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .skill_changes import active_revision, default_log_path

SKILL_FAMILIES = frozenset({"source", "analysis", "report", "evaluation", "improvement"})
FORBIDDEN_KEYS = frozenset({"trust_weights", "core", "time_boundary", "evidence_binding"})


def _home() -> Path:
    configured = os.getenv("TRUSTFORGE_HOME")
    if configured:
        return Path(configured)
    # Source checkout: <root>/src/trustforge/skills.py.  Deployment bundle:
    # <root>/trustforge/skills.py.  Find the artifact root instead of relying
    # on a fixed parent count.
    module_path = Path(__file__).resolve()
    for candidate in module_path.parents:
        if (candidate / "skills" / "hermes").is_dir():
            return candidate
    return module_path.parents[2]


def default_skill_root() -> Path:
    return Path(os.getenv("TRUSTFORGE_SKILL_ROOT", str(_home() / "skills" / "hermes")))


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def artifact_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_artifact(value: dict[str, Any]) -> None:
    family = value.get("family")
    if family not in SKILL_FAMILIES:
        raise ValueError(f"unsupported skill family: {family!r}")
    if not isinstance(value.get("rules"), list) or not value["rules"]:
        raise ValueError("skill artifact needs non-empty rules")
    if set(value) & FORBIDDEN_KEYS:
        raise ValueError("outer skills may not override deterministic core controls")


def skill_id_for(family: str) -> str:
    if family not in SKILL_FAMILIES:
        raise ValueError(f"unsupported skill family: {family!r}")
    return f"outer-{family}"


def artifact_path(family: str, revision_hash: str, *, root: Path | None = None) -> Path:
    return (root or default_skill_root()) / family / f"{revision_hash}.json"


def write_artifact(value: dict[str, Any], *, root: Path | None = None) -> tuple[str, Path]:
    validate_artifact(value)
    revision_hash = artifact_hash(value)
    target = artifact_path(str(value["family"]), revision_hash, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_text(encoding="utf-8") != canonical_json(value) + "\n":
        raise ValueError("immutable skill artifact hash collision")
    if not target.exists():
        target.write_text(canonical_json(value) + "\n", encoding="utf-8")
    return revision_hash, target


def load_artifact(family: str, revision_hash: str, *, root: Path | None = None) -> dict[str, Any]:
    target = artifact_path(family, revision_hash, root=root)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"skill artifact unavailable: {family}@{revision_hash}") from exc
    if not isinstance(value, dict):
        raise ValueError("skill artifact must be an object")
    validate_artifact(value)
    if artifact_hash(value) != revision_hash:
        raise ValueError("skill artifact hash mismatch")
    return value


def resolve_active_skills(*, root: Path | None = None, log_path: Path | None = None) -> list[dict[str, Any]]:
    """Resolve a frozen set for a run; unapproved candidates are invisible."""
    skill_root = root or default_skill_root()
    resolved = []
    for family in sorted(SKILL_FAMILIES):
        revision_hash = active_revision(skill_id_for(family), log_path=log_path or default_log_path())
        if revision_hash is None:
            # The committed baseline is the immutable initial revision.  Once a
            # mutable revision is approved, only the append-only pointer wins.
            candidates = sorted((skill_root / family).glob("*.json"))
            if len(candidates) != 1:
                raise ValueError(f"exactly one baseline is required for {family}")
            revision_hash = candidates[0].stem
            origin = "baseline"
        else:
            origin = "approved"
        artifact = load_artifact(family, revision_hash, root=skill_root)
        resolved.append({"family": family, "revision": revision_hash, "origin": origin, "artifact": artifact})
    return resolved


def run_skill_manifest(*, root: Path | None = None, log_path: Path | None = None) -> dict[str, Any]:
    return {
        "core_controls": {
            "time_boundary": "immutable",
            "evidence_binding": "immutable",
            "trust_layer": "immutable",
            "formal_run_isolation": "immutable",
        },
        "outer_skills": resolve_active_skills(root=root, log_path=log_path),
    }
