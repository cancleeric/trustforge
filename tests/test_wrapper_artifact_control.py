import pytest

from trustforge.artifact_registry import InMemoryArtifactRegistry, InMemoryRevisionPointerStore
from trustforge.wrapper_artifact_control import (
    WrapperArtifactError,
    activate_wrapper_artifact,
    rollback_wrapper_artifact,
    sandbox_wrapper_artifact,
)


VERIFIED = {"status": "verified"}
UNVERIFIED = {"status": "unverified"}


def test_modelhub_unverified_disables_sandbox_without_side_effect():
    result = sandbox_wrapper_artifact(
        UNVERIFIED,
        artifact_id="sha256:abc",
        checksum="abc",
        expected_checksum="abc",
    )

    assert result == {"status": "disabled", "reason": "modelhub_not_verified", "side_effect": "none"}


def test_wrapper_artifact_is_candidate_only_and_activation_is_human_bound():
    registry = InMemoryArtifactRegistry()
    pointers = InMemoryRevisionPointerStore(registry)
    current = registry.put(b"current", metadata={"role": "candidate"})
    candidate = registry.put(b"candidate", metadata={"role": "candidate"})
    pointers.stage("wrapper", current.artifact_id, actor="gray")
    pointers.activate("wrapper", actor="gray")

    activated = activate_wrapper_artifact(
        VERIFIED,
        registry,
        pointers,
        pointer_name="wrapper",
        artifact_id=candidate.artifact_id,
        actor="eric",
        checksum=candidate.sha256,
        config_snapshot={"threshold": 0.5},
        rollback_target=current.artifact_id,
    )

    assert activated.active_artifact_id == candidate.artifact_id
    assert pointers.history("wrapper")[-1].action == "activate"


def test_wrapper_activation_rejects_checksum_mismatch_automation_and_truth_role():
    registry = InMemoryArtifactRegistry()
    pointers = InMemoryRevisionPointerStore(registry)
    truth = registry.put(b"truth", metadata={"role": "truth"})

    with pytest.raises(WrapperArtifactError, match="human actor"):
        activate_wrapper_artifact(
            VERIFIED,
            registry,
            pointers,
            pointer_name="wrapper",
            artifact_id=truth.artifact_id,
            actor="codex-bot",
            checksum=truth.sha256,
            config_snapshot={"threshold": 0.5},
            rollback_target=None,
        )
    with pytest.raises(WrapperArtifactError, match="checksum"):
        activate_wrapper_artifact(
            VERIFIED,
            registry,
            pointers,
            pointer_name="wrapper",
            artifact_id=truth.artifact_id,
            actor="eric",
            checksum="0" * 64,
            config_snapshot={"threshold": 0.5},
            rollback_target=None,
        )
    with pytest.raises(WrapperArtifactError, match="candidate"):
        activate_wrapper_artifact(
            VERIFIED,
            registry,
            pointers,
            pointer_name="wrapper",
            artifact_id=truth.artifact_id,
            actor="eric",
            checksum=truth.sha256,
            config_snapshot={"threshold": 0.5},
            rollback_target=None,
        )


def test_wrapper_activation_requires_rollback_target_and_offline_rollback_uses_local_pointer():
    registry = InMemoryArtifactRegistry()
    pointers = InMemoryRevisionPointerStore(registry)
    current = registry.put(b"current", metadata={"role": "candidate"})
    candidate = registry.put(b"candidate", metadata={"role": "candidate"})
    pointers.stage("wrapper", current.artifact_id, actor="gray")
    pointers.activate("wrapper", actor="gray")

    with pytest.raises(WrapperArtifactError, match="rollback target"):
        activate_wrapper_artifact(
            VERIFIED,
            registry,
            pointers,
            pointer_name="wrapper",
            artifact_id=candidate.artifact_id,
            actor="eric",
            checksum=candidate.sha256,
            config_snapshot={"threshold": 0.5},
            rollback_target="sha256:wrong",
        )
    activate_wrapper_artifact(
        VERIFIED,
        registry,
        pointers,
        pointer_name="wrapper",
        artifact_id=candidate.artifact_id,
        actor="eric",
        checksum=candidate.sha256,
        config_snapshot={"threshold": 0.5},
        rollback_target=current.artifact_id,
    )
    rolled_back = rollback_wrapper_artifact(
        pointers,
        pointer_name="wrapper",
        rollback_target=current.artifact_id,
        actor="eric",
    )

    assert rolled_back.active_artifact_id == current.artifact_id
