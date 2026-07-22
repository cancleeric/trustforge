"""Immutable artifact registry and revision pointer contract tests (#413)."""
from __future__ import annotations

import pytest

from trustforge.artifact_registry import (
    ArtifactRegistry,
    InMemoryArtifactRegistry,
    InMemoryRevisionPointerStore,
    RevisionPointerStore,
    artifact_sha256,
)


def test_registry_and_pointer_store_are_runtime_checkable():
    registry = InMemoryArtifactRegistry()
    pointers = InMemoryRevisionPointerStore(registry)

    assert isinstance(registry, ArtifactRegistry)
    assert isinstance(pointers, RevisionPointerStore)


def test_artifact_identity_is_content_and_metadata_addressed():
    registry = InMemoryArtifactRegistry()

    first = registry.put(b"payload", metadata={"version": 1}, now=10.0)
    duplicate = registry.put(b"payload", metadata={"version": 1}, now=20.0)
    changed_metadata = registry.put(b"payload", metadata={"version": 2}, now=20.0)

    assert first == duplicate
    assert first.created_at == 10.0
    assert first.artifact_id == f"sha256:{artifact_sha256(b'payload', {'version': 1})}"
    assert changed_metadata.artifact_id != first.artifact_id
    assert registry.get(first.artifact_id) == first


def test_stage_does_not_change_active_formal_run_pointer():
    registry = InMemoryArtifactRegistry()
    pointers = InMemoryRevisionPointerStore(registry)
    artifact = registry.put(b"candidate")

    staged = pointers.stage("policy-pack", artifact.artifact_id, actor="gray", now=1.0)
    current = pointers.pointer("policy-pack")

    assert staged.active_artifact_id is None
    assert staged.staged_artifact_id == artifact.artifact_id
    assert current == staged
    assert pointers.history("policy-pack")[0].action == "stage"


def test_activate_requires_staged_artifact_and_records_append_only_history():
    registry = InMemoryArtifactRegistry()
    pointers = InMemoryRevisionPointerStore(registry)

    with pytest.raises(ValueError, match="cannot activate"):
        pointers.activate("policy-pack", actor="human", now=1.0)

    artifact = registry.put(b"candidate")
    pointers.stage("policy-pack", artifact.artifact_id, actor="gray", now=2.0)
    active = pointers.activate("policy-pack", actor="human", now=3.0)

    assert active.active_artifact_id == artifact.artifact_id
    assert active.staged_artifact_id is None
    assert [event.action for event in pointers.history("policy-pack")] == ["stage", "activate"]
    assert [event.actor for event in pointers.history("policy-pack")] == ["gray", "human"]


def test_rollback_requires_known_artifact_and_preserves_staged_candidate():
    registry = InMemoryArtifactRegistry()
    pointers = InMemoryRevisionPointerStore(registry)
    stable = registry.put(b"stable")
    candidate = registry.put(b"candidate")

    pointers.stage("policy-pack", stable.artifact_id, actor="gray")
    pointers.activate("policy-pack", actor="human")
    pointers.stage("policy-pack", candidate.artifact_id, actor="gray")

    with pytest.raises(ValueError, match="unknown artifact"):
        pointers.rollback("policy-pack", "sha256:missing", actor="human")

    rolled_back = pointers.rollback("policy-pack", stable.artifact_id, actor="human", now=4.0)

    assert rolled_back.active_artifact_id == stable.artifact_id
    assert rolled_back.staged_artifact_id == candidate.artifact_id
    assert [event.action for event in pointers.history("policy-pack")] == [
        "stage",
        "activate",
        "stage",
        "rollback",
    ]


def test_contract_does_not_embed_hermes_family_names():
    forbidden = {"connectors", "improvement", "evaluation", "source-frequency", "hermes"}
    names = {
        *InMemoryArtifactRegistry.__dict__,
        *InMemoryRevisionPointerStore.__dict__,
    }

    assert names.isdisjoint(forbidden)
