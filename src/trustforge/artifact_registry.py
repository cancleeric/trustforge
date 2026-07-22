"""Generic immutable artifact registry and revision pointer contracts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


PointerStage = Literal["staged", "active"]
PointerAction = Literal["stage", "activate", "rollback"]


def artifact_sha256(payload: bytes, metadata: dict[str, Any] | None = None) -> str:
    """Hash payload plus canonical metadata for immutable registry identity."""
    digest = hashlib.sha256()
    digest.update(payload)
    if metadata:
        digest.update(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True)
class ArtifactRecord:
    """Immutable artifact record."""

    artifact_id: str
    sha256: str
    payload: bytes
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0


@dataclass(frozen=True)
class RevisionPointer:
    """Generic revision pointer for a family/key without app-domain knowledge."""

    name: str
    active_artifact_id: str | None = None
    staged_artifact_id: str | None = None
    version: int = 0


@dataclass(frozen=True)
class PointerEvent:
    """Append-only pointer transition evidence."""

    action: PointerAction
    name: str
    artifact_id: str | None
    actor: str
    at: float
    reason: str = ""


@runtime_checkable
class ArtifactRegistry(Protocol):
    """Provider-neutral immutable artifact registry."""

    provider_id: str

    def put(
        self,
        payload: bytes,
        *,
        metadata: dict[str, Any] | None = None,
        now: float = 0.0,
    ) -> ArtifactRecord:
        """Store immutable payload and return its content-addressed record."""
        ...

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        """Return an artifact record without mutating pointer state."""
        ...


@runtime_checkable
class RevisionPointerStore(Protocol):
    """Generic stage/activate/rollback revision pointer lifecycle."""

    provider_id: str

    def pointer(self, name: str) -> RevisionPointer:
        """Return current pointer state."""
        ...

    def stage(self, name: str, artifact_id: str, *, actor: str, now: float = 0.0) -> RevisionPointer:
        """Stage an artifact without changing active formal-run pointer."""
        ...

    def activate(self, name: str, *, actor: str, now: float = 0.0) -> RevisionPointer:
        """Promote staged artifact to active pointer."""
        ...

    def rollback(self, name: str, artifact_id: str, *, actor: str, now: float = 0.0) -> RevisionPointer:
        """Move active pointer back to a known artifact."""
        ...

    def history(self, name: str | None = None) -> tuple[PointerEvent, ...]:
        """Return append-only pointer transition history."""
        ...


class InMemoryArtifactRegistry:
    """In-memory immutable registry useful for tests and local adapters."""

    provider_id = "memory-artifacts"

    def __init__(self) -> None:
        self._records: dict[str, ArtifactRecord] = {}

    def put(
        self,
        payload: bytes,
        *,
        metadata: dict[str, Any] | None = None,
        now: float = 0.0,
    ) -> ArtifactRecord:
        record_metadata = dict(metadata or {})
        digest = artifact_sha256(payload, record_metadata)
        artifact_id = f"sha256:{digest}"
        existing = self._records.get(artifact_id)
        if existing is not None:
            return existing
        record = ArtifactRecord(
            artifact_id=artifact_id,
            sha256=digest,
            payload=bytes(payload),
            metadata=record_metadata,
            created_at=now,
        )
        self._records[artifact_id] = record
        return record

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        return self._records.get(artifact_id)


class InMemoryRevisionPointerStore:
    """Append-only in-memory revision pointer store."""

    provider_id = "memory-revision-pointers"

    def __init__(self, registry: ArtifactRegistry) -> None:
        self.registry = registry
        self._pointers: dict[str, RevisionPointer] = {}
        self._history: list[PointerEvent] = []

    def pointer(self, name: str) -> RevisionPointer:
        return self._pointers.get(name, RevisionPointer(name=name))

    def stage(self, name: str, artifact_id: str, *, actor: str, now: float = 0.0) -> RevisionPointer:
        self._require_artifact(artifact_id)
        current = self.pointer(name)
        updated = RevisionPointer(
            name=name,
            active_artifact_id=current.active_artifact_id,
            staged_artifact_id=artifact_id,
            version=current.version + 1,
        )
        self._pointers[name] = updated
        self._history.append(PointerEvent("stage", name, artifact_id, actor, now))
        return updated

    def activate(self, name: str, *, actor: str, now: float = 0.0) -> RevisionPointer:
        current = self.pointer(name)
        if current.staged_artifact_id is None:
            raise ValueError("cannot activate without staged artifact")
        updated = RevisionPointer(
            name=name,
            active_artifact_id=current.staged_artifact_id,
            staged_artifact_id=None,
            version=current.version + 1,
        )
        self._pointers[name] = updated
        self._history.append(PointerEvent("activate", name, updated.active_artifact_id, actor, now))
        return updated

    def rollback(self, name: str, artifact_id: str, *, actor: str, now: float = 0.0) -> RevisionPointer:
        self._require_artifact(artifact_id)
        current = self.pointer(name)
        updated = RevisionPointer(
            name=name,
            active_artifact_id=artifact_id,
            staged_artifact_id=current.staged_artifact_id,
            version=current.version + 1,
        )
        self._pointers[name] = updated
        self._history.append(PointerEvent("rollback", name, artifact_id, actor, now))
        return updated

    def history(self, name: str | None = None) -> tuple[PointerEvent, ...]:
        if name is None:
            return tuple(self._history)
        return tuple(event for event in self._history if event.name == name)

    def _require_artifact(self, artifact_id: str) -> None:
        if self.registry.get(artifact_id) is None:
            raise ValueError("unknown artifact")
