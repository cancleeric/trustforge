"""Load and validate approved policy revisions from disk.

The loader bridges the skill artifact storage (JSON files identified by
content-hash) with the typed policy compiler.  It ensures:
    1. Only approved/baseline revisions are loadable (staged = invisible)
    2. Hash integrity is verified on read
    3. The loaded artifact passes all security guards before compilation

Public API:
    load_approved_policy(family, revision_hash, root, log_path) → TypedPolicy
    load_all_effective(root, log_path) → dict[str, TypedPolicy]
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..skill_changes import active_revision, default_log_path
from ..skills import (
    SKILL_FAMILIES,
    artifact_hash,
    default_skill_root,
    load_artifact,
    skill_id_for,
)
from .compiler import compile_policy
from .schema import FAMILY_SCHEMA


def load_approved_policy(
    family: str,
    revision_hash: str,
    *,
    root: Path | None = None,
    log_path: Path | None = None,
) -> Any:
    """Load a specific revision and compile it into a TypedPolicy.

    Args:
        family: One of SKILL_FAMILIES (source/analysis/report/evaluation/improvement).
        revision_hash: SHA-256 content hash of the artifact.
        root: Skill artifact root directory (default: skills/hermes/).
        log_path: Path to the skill change log (default: out/skill_changes.jsonl).

    Returns:
        Frozen TypedPolicy dataclass instance.

    Raises:
        ValueError: If family is invalid, artifact missing, or hash mismatch.
        SecurityError: If guards reject the artifact content.
    """
    if family not in FAMILY_SCHEMA:
        raise ValueError(f"unsupported policy family: {family!r}")

    # load_artifact already validates hash integrity and basic structure.
    artifact = load_artifact(family, revision_hash, root=root)
    return compile_policy(artifact)


def _resolve_revision(
    family: str,
    *,
    root: Path | None = None,
    log_path: Path | None = None,
) -> tuple[str, str]:
    """Resolve the active revision hash for a family.

    Returns:
        (revision_hash, origin) where origin is "approved" or "baseline".
    """
    skill_root = root or default_skill_root()
    _log_path = log_path or default_log_path()

    revision_hash = active_revision(skill_id_for(family), log_path=_log_path)
    if revision_hash is not None:
        return revision_hash, "approved"

    # Fall back to baseline: exactly one file in the family directory.
    candidates = sorted((skill_root / family).glob("*.json"))
    if len(candidates) != 1:
        raise ValueError(f"exactly one baseline is required for {family}")
    return candidates[0].stem, "baseline"


def load_all_effective(
    *,
    root: Path | None = None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Load effective policies for all families.

    Only approved or baseline revisions are returned — staged candidates are
    invisible.  This is the primary entry point for the PolicyExecutor.

    Returns:
        Dict mapping family name → frozen TypedPolicy instance.

    Raises:
        ValueError: If any family lacks a valid baseline/approved artifact.
        SecurityError: If any artifact fails guard checks.
    """
    result: dict[str, Any] = {}
    skill_root = root or default_skill_root()
    _log_path = log_path or default_log_path()

    for family in sorted(SKILL_FAMILIES):
        revision_hash, _origin = _resolve_revision(
            family, root=skill_root, log_path=_log_path
        )
        result[family] = load_approved_policy(
            family, revision_hash, root=skill_root, log_path=_log_path
        )

    return result
