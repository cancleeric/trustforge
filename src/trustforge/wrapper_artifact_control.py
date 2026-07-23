"""Wrapper artifact sandbox, activation, and offline rollback gates."""
from __future__ import annotations

from typing import Any

from .artifact_registry import ArtifactRegistry, RevisionPointer, RevisionPointerStore
from .upgrade_state_machine import is_human_approval_actor


class WrapperArtifactError(ValueError):
    pass


def sandbox_wrapper_artifact(
    probe_report: dict[str, Any],
    *,
    artifact_id: str,
    checksum: str,
    expected_checksum: str,
) -> dict[str, Any]:
    """Validate a candidate artifact without production pointer side effects."""

    if probe_report.get("status") != "verified":
        return {"status": "disabled", "reason": "modelhub_not_verified", "side_effect": "none"}
    if checksum != expected_checksum:
        return {"status": "failed", "reason": "checksum_mismatch", "side_effect": "none"}
    return {
        "status": "sandbox_passed",
        "artifact_id": artifact_id,
        "checksum": checksum,
        "role": "candidate",
        "side_effect": "none",
    }


def activate_wrapper_artifact(
    probe_report: dict[str, Any],
    registry: ArtifactRegistry,
    pointers: RevisionPointerStore,
    *,
    pointer_name: str,
    artifact_id: str,
    actor: str,
    checksum: str,
    config_snapshot: dict[str, Any],
    rollback_target: str | None,
    now: float = 0.0,
) -> RevisionPointer:
    if probe_report.get("status") != "verified":
        raise WrapperArtifactError("ModelHub is not verified")
    if not is_human_approval_actor(actor):
        raise WrapperArtifactError("wrapper activation requires human actor")
    if not isinstance(config_snapshot, dict) or not config_snapshot:
        raise WrapperArtifactError("activation requires config snapshot")
    record = registry.get(artifact_id)
    if record is None:
        raise WrapperArtifactError("unknown artifact")
    if record.sha256 != checksum or artifact_id != f"sha256:{checksum}":
        raise WrapperArtifactError("artifact checksum mismatch")
    if record.metadata.get("role") != "candidate":
        raise WrapperArtifactError("wrapper artifact must remain a candidate")
    current = pointers.pointer(pointer_name)
    if current.active_artifact_id != rollback_target:
        raise WrapperArtifactError("rollback target must match current active artifact")
    pointers.stage(pointer_name, artifact_id, actor=actor, now=now)
    return pointers.activate(pointer_name, actor=actor, now=now)


def rollback_wrapper_artifact(
    pointers: RevisionPointerStore,
    *,
    pointer_name: str,
    rollback_target: str,
    actor: str,
    now: float = 0.0,
) -> RevisionPointer:
    if not is_human_approval_actor(actor):
        raise WrapperArtifactError("wrapper rollback requires human actor")
    return pointers.rollback(pointer_name, rollback_target, actor=actor, now=now)
