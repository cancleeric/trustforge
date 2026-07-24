"""Ports for the approval-gated outer-upgrade domain.

The queue owns policy and persistence.  Concrete Hermes skill storage lives
behind these ports so importing the domain never imports the skill subsystem.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, ContextManager, Protocol


class OperationDisplacedError(RuntimeError):
    """A completed operation receipt no longer owns the active pointer."""


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    subject: str
    tenant_id: str
    actions: frozenset[str]
    expires_at: datetime

    def is_expired(self) -> bool:
        expiry = self.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= datetime.now(timezone.utc)


@dataclass(frozen=True)
class UpgradeCandidate:
    family: str
    revision: str
    artifact_hash: str
    artifact: dict[str, Any]
    module_id: str


@dataclass(frozen=True)
class PointerChange:
    family: str
    revision: str
    previous_revision: str | None
    operation_id: str


@dataclass(frozen=True)
class SandboxAttestation:
    db_identity: str
    proposal_id: str
    candidate_family: str
    candidate_revision: str
    run_id: str
    runner_version: str
    artifact_hash: str
    details_checksum: str
    passed: bool
    completed_at: datetime
    details: dict[str, Any]
    key_id: str
    proof: str


class SandboxAttestationVerifier(Protocol):
    def compact(
        self,
        *,
        db_identity: str,
        exact_capabilities: dict[str, dict[str, Any]],
    ) -> int: ...

    def reject(
        self,
        attestation: SandboxAttestation,
        *,
        operation_binding: str,
        db_identity: str,
    ) -> None: ...

    def consume(
        self,
        attestation: SandboxAttestation,
        *,
        already_persisted: bool,
        operation_binding: str,
        db_identity: str,
    ) -> ContextManager[None]: ...


class AuthorityAdapter(Protocol):
    def require(
        self,
        principal: AuthenticatedPrincipal,
        action: str,
        *,
        tenant_id: str,
    ) -> str: ...


class ModuleCatalog(Protocol):
    def resolve(self, family: str, revision: str, artifact_hash: str) -> UpgradeCandidate: ...


class ControlPlaneCatalog(Protocol):
    def manifest(self) -> dict[str, Any]: ...
    def history(self) -> list[dict[str, Any]]: ...


class ActivationHandler(Protocol):
    def current_revision(self, family: str) -> str | None: ...

    def activate(
        self,
        candidate: UpgradeCandidate,
        *,
        proposal_id: str,
        operation_id: str,
        expected_revision: str | None,
    ) -> PointerChange: ...


class RollbackHandler(Protocol):
    def rollback(
        self,
        family: str,
        target_revision: str,
        *,
        reason: str,
        operation_id: str,
        expected_revision: str,
    ) -> PointerChange: ...
