"""Wrapper artifact controlled upgrade: sandbox, human activation, offline rollback.

This module implements the third-track "Wrapper controlled upgrade" plan.  Four
invariants drive the design and are enforced here, not in caller code:

1. **A wrapper never approves or activates its own candidate.**  The principal
   that records a ``candidate_build`` or a ``sandbox_replay`` cannot be the
   reviewer that signs the approval.  The approval principal must be a
   distinct, authenticated :class:`ReviewerPrincipal`.

2. **Human activation is a hard boundary.**  ``activate`` requires a typed
   :class:`ApprovalRecord` whose ``binding_checksum`` was computed by this
   controller over the exact ``(proposal, candidate, sandbox, config_snapshot,
   rollback_target)`` tuple.  Callers cannot construct a valid
   :class:`ApprovalRecord` by hand: the controller mints one only through
   :meth:`WrapperArtifactController.request_approval`, after verifying the
   reviewer, the candidate identity, and that the sandbox passed.

3. **Triple binding.**  ``config_snapshot`` + candidate ``artifact`` +
   ``rollback_target`` are bound cryptographically into the approval record.
   The activation step re-derives the binding checksum and rejects any drift.
   ``rollback_target`` must point at an *already-approved* prior activation's
   artifact, so the rollback target is always a known-good state.

4. **ModelHub unverified => disabled.**  ``activate`` accepts the *raw probe
   observation* plus the :class:`ProbeRequirement` and runs the
   :mod:`trustforge.modelhub_readonly_probe` evaluator inline.  The caller
   cannot pass a pre-baked ``{"status": "verified"}`` dict; only a legitimate
   observation/requirement pair that survives the fail-closed evaluator unlocks
   activation.  Rollback deliberately does **not** require a ModelHub probe:
   rolling back to a known-good local artifact must succeed when ModelHub is
   offline.

The module is no-DB by design.  State lives in the in-memory controller and in
the :class:`~trustforge.artifact_registry.RevisionPointerStore` it is wired
against; persistence of either is the caller's responsibility.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .artifact_registry import (
    ArtifactRegistry,
    ArtifactRecord,
    RevisionPointerStore,
)
from .modelhub_readonly_probe import (
    ProbeRequirement,
    evaluate_modelhub_readonly_probe,
)
from .wrapper_state_machine import (
    INITIAL_WRAPPER_STATE,
    TERMINAL_WRAPPER_STATE,
    transition,
)


class WrapperArtifactError(ValueError):
    """Raised for any refused wrapper artifact operation.

    Wrapping ``ValueError`` keeps the controller's public contract consistent
    with the rest of the upgrade domain (which raises ``ValueError`` for
    invariant violations) while giving tests and reviewers a single type to
    catch.
    """


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: Any) -> bytes:
    """Stable canonical encoding used for every checksum in this module."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_hex(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _require_nonempty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WrapperArtifactError(f"{label} must be a non-empty string")
    return value.strip()


def _canonical_subject(s: str) -> str:
    """Canonicalize a principal subject for structural comparison.

    Applies NFKC normalization (which folds compatibility-equivalent
    homoglyphs such as fullwidth ASCII, ligatures, and superscripts),
    strips surrounding whitespace, and casefolds.  Used both at principal
    construction time (so every stored subject is already in canonical form)
    and at every separation-of-duties comparison, so that the
    proposer-vs-reviewer and runner-vs-reviewer self-approval checks cannot
    be bypassed by whitespace, case, or NFKC-equivalent homoglyph
    differences (CISO H1 cases 1 and 2).

    Note that NFKC does **not** fold cross-script confusables (e.g. Cyrillic
    U+0430 ``а`` vs Latin ``a``); those are rejected at construction time by
    :func:`_assert_no_mixed_script`, so a mixed-script spoofing subject can
    never enter the controller (CISO H1 case 3).
    """
    return unicodedata.normalize("NFKC", s).strip().casefold()


def _assert_no_mixed_script(canonical: str) -> None:
    """Reject Latin + non-Latin letter mixtures in a principal subject.

    NFKC folds compatibility-equivalent homoglyphs but does not fold
    cross-script confusables such as Cyrillic ``а`` (U+0430) standing in for
    Latin ``a`` — the exact vector of CISO H1 case 3.  A principal ``subject``
    is a machine identifier (a user/service id), not a free-form display
    name; a string that mixes ASCII letters with non-ASCII letters has no
    legitimate identifier use and is the necessary precondition for a
    cross-script homoglyph spoof.  Pure-ASCII identifiers (the existing
    convention) and pure-non-Latin identifiers (e.g. a CJK id) remain valid.
    """
    has_ascii_letter = False
    has_non_ascii_letter = False
    for ch in canonical:
        if unicodedata.category(ch)[0] != "L":  # only letters carry spoof risk
            continue
        if ch.isascii():
            has_ascii_letter = True
        else:
            has_non_ascii_letter = True
        if has_ascii_letter and has_non_ascii_letter:
            raise WrapperArtifactError(
                "principal subject mixes ASCII and non-ASCII letters; "
                "cross-script confusable subjects are forbidden"
            )


# --------------------------------------------------------------------------- #
# Typed domain objects
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DiagnosticSource:
    """Provenance for a proposal: where the diagnostic signal came from.

    A proposal without a diagnostic source is rejected at creation time, which
    closes the "provenance missing" attack: a wrapper cannot elevate an
    artifact whose origin is unattributed.
    """

    diagnostic_id: str
    observer: str
    generated_at: datetime

    def checksum(self) -> str:
        return _sha256_hex(
            {
                "kind": "wrapper.diagnostic/v1",
                "diagnostic_id": self.diagnostic_id,
                "observer": self.observer,
                "generated_at": self.generated_at.astimezone(timezone.utc).isoformat(),
            }
        )


@dataclass(frozen=True)
class DatasetManifest:
    """Content-addressed dataset manifest bound to a candidate build."""

    manifest_id: str
    sha256: str

    def checksum(self) -> str:
        return _sha256_hex(
            {
                "kind": "wrapper.dataset-manifest/v1",
                "manifest_id": self.manifest_id,
                "sha256": self.sha256,
            }
        )


@dataclass(frozen=True)
class RiskAssessment:
    """Typed risk assessment attached to a proposal."""

    assessment_id: str
    risk_level: str  # "low" | "medium" | "high"
    evaluator: str

    def checksum(self) -> str:
        return _sha256_hex(
            {
                "kind": "wrapper.risk-assessment/v1",
                "assessment_id": self.assessment_id,
                "risk_level": self.risk_level,
                "evaluator": self.evaluator,
            }
        )


@dataclass(frozen=True)
class CandidateArtifact:
    """A candidate wrapper artifact referenced by a proposal.

    ``artifact_id`` is the registry's content-addressed id (``sha256:<hex>``);
    ``payload_sha256`` is the hex digest of the artifact payload alone.  The
    controller verifies ``artifact_id == "sha256:" + payload_sha256`` at every
    binding step, so a caller cannot swap a checksum for an unrelated one.
    """

    artifact_id: str
    payload_sha256: str
    dataset_manifest: DatasetManifest

    def checksum(self) -> str:
        return _sha256_hex(
            {
                "kind": "wrapper.candidate-artifact/v1",
                "artifact_id": self.artifact_id,
                "payload_sha256": self.payload_sha256,
                "dataset_manifest": self.dataset_manifest.checksum(),
            }
        )


@dataclass(frozen=True)
class SandboxReplayResult:
    """Outcome of a sandbox replay run, bound to a specific candidate."""

    run_id: str
    runner_version: str
    candidate_artifact_id: str
    completed_at: datetime
    passed: bool
    replay_sha256: str

    def checksum(self) -> str:
        return _sha256_hex(
            {
                "kind": "wrapper.sandbox-replay/v1",
                "run_id": self.run_id,
                "runner_version": self.runner_version,
                "candidate_artifact_id": self.candidate_artifact_id,
                "completed_at": self.completed_at.astimezone(timezone.utc).isoformat(),
                "passed": bool(self.passed),
                "replay_sha256": self.replay_sha256,
            }
        )


@dataclass(frozen=True)
class ReviewerPrincipal:
    """Authenticated human reviewer authorized to approve a wrapper upgrade.

    ``subject`` and ``role`` are deliberately simple typed fields rather than
    free-form strings: a controller compares principals by structural equality,
    not by string matching against a blacklist.  ``expires_at`` is consulted on
    every authorization and may not be naive.

    ``subject`` is canonicalized at construction time via
    :func:`_canonical_subject` (NFKC + strip + casefold) and rejected if it
    mixes ASCII with non-ASCII letters (:func:`_assert_no_mixed_script`), so
    two principals that differ only by whitespace, case, NFKC-equivalent
    homoglyphs, or cross-script confusables cannot both exist — the
    separation-of-duties check in :meth:`request_approval` therefore cannot
    be spoofed by subject cosmetic differences.
    """

    subject: str
    role: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.subject, str) or not self.subject.strip():
            raise WrapperArtifactError("reviewer subject must be a non-empty string")
        canonical = _canonical_subject(self.subject)
        _assert_no_mixed_script(canonical)
        object.__setattr__(self, "subject", canonical)
        if not isinstance(self.role, str) or not self.role.strip():
            raise WrapperArtifactError("reviewer role must be a non-empty string")
        object.__setattr__(self, "role", self.role.strip())

    def is_expired(self, *, now: datetime | None = None) -> bool:
        current = now or _utcnow()
        expiry = self.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= current


@dataclass(frozen=True)
class ActorPrincipal:
    """Principal that records non-approval steps (proposal, candidate, sandbox).

    Deliberately a different type from :class:`ReviewerPrincipal` so the
    controller can statically enforce "the proposer cannot be the approver".

    ``subject`` is canonicalized at construction via
    :func:`_canonical_subject` and rejected if it mixes ASCII with non-ASCII
    letters, mirroring :class:`ReviewerPrincipal`, so the separation-of-duties
    comparison in :meth:`request_approval` compares two principals that are
    each already in canonical form (and defense-in-depth canonicalizes again
    at the comparison site).
    """

    subject: str

    def __post_init__(self) -> None:
        if not isinstance(self.subject, str) or not self.subject.strip():
            raise WrapperArtifactError("actor subject must be a non-empty string")
        canonical = _canonical_subject(self.subject)
        _assert_no_mixed_script(canonical)
        object.__setattr__(self, "subject", canonical)


@dataclass(frozen=True)
class ApprovalRecord:
    """Single-use approval record minted by the controller.

    Fields:

    * ``approval_id``: a CSPRNG-generated opaque token.  Only the controller
      ever mints these, and only into its in-memory journal.
    * ``reviewer``: the authenticated :class:`ReviewerPrincipal` who approved.
    * ``proposal_id``: which proposal this approval unlocks.
    * ``binding_checksum``: SHA-256 over the exact tuple of (proposal_id,
      candidate_artifact_id, candidate_payload_sha256, dataset_manifest,
      sandbox_run_id, sandbox_replay_checksum, config_snapshot_artifact_id,
      rollback_target_artifact_id, rollback_target_config_snapshot_id).
      ``activate`` re-derives this and rejects any drift.
    * ``issued_at``: when the controller minted this record.
    """

    approval_id: str
    reviewer: ReviewerPrincipal
    proposal_id: str
    binding_checksum: str
    issued_at: datetime


@dataclass(frozen=True)
class ActivationEvent:
    """Immutable activation receipt recorded after a successful activation."""

    activation_id: str
    proposal_id: str
    actor_subject: str
    activated_artifact_id: str
    config_snapshot_artifact_id: str
    rollback_target_artifact_id: str
    rollback_target_config_snapshot_id: str
    approval_id: str
    modelhub_probe_status: str
    reason: str
    at: datetime


@dataclass(frozen=True)
class RollbackEvent:
    """Immutable rollback receipt.  Issued offline (no ModelHub probe)."""

    rollback_id: str
    proposal_id: str
    actor_subject: str
    target_artifact_id: str
    target_config_snapshot_id: str
    reason: str
    at: datetime


@dataclass
class _ProposalState:
    """In-memory lifecycle record for one wrapper proposal."""

    proposal_id: str
    state: str = INITIAL_WRAPPER_STATE
    diagnostic: DiagnosticSource | None = None
    candidate: CandidateArtifact | None = None
    risk: RiskAssessment | None = None
    proposer: ActorPrincipal | None = None
    sandbox_result: SandboxReplayResult | None = None
    sandbox_runner: ActorPrincipal | None = None
    config_snapshot_artifact_id: str | None = None
    rollback_target_artifact_id: str | None = None
    rollback_target_config_snapshot_id: str | None = None
    activation: ActivationEvent | None = None
    rollback: RollbackEvent | None = None


def _binding_material(
    proposal_id: str,
    candidate: CandidateArtifact,
    sandbox_result: SandboxReplayResult,
    config_snapshot_artifact_id: str,
    rollback_target_artifact_id: str,
    rollback_target_config_snapshot_id: str,
) -> dict[str, Any]:
    """Canonical material that gets hashed into an ApprovalRecord binding.

    Every field here is content-addressed or typed-checkable, so any drift in
    the candidate artifact, sandbox result, config snapshot, or rollback
    target invalidates the binding checksum and the controller refuses
    activation.
    """
    return {
        "kind": "wrapper.approval-binding/v1",
        "proposal_id": proposal_id,
        "candidate_artifact_id": candidate.artifact_id,
        "candidate_payload_sha256": candidate.payload_sha256,
        "dataset_manifest_checksum": candidate.dataset_manifest.checksum(),
        "sandbox_run_id": sandbox_result.run_id,
        "sandbox_replay_checksum": sandbox_result.checksum(),
        "config_snapshot_artifact_id": config_snapshot_artifact_id,
        "rollback_target_artifact_id": rollback_target_artifact_id,
        "rollback_target_config_snapshot_id": rollback_target_config_snapshot_id,
    }


def compute_binding_checksum(
    proposal_id: str,
    candidate: CandidateArtifact,
    sandbox_result: SandboxReplayResult,
    config_snapshot_artifact_id: str,
    rollback_target_artifact_id: str,
    rollback_target_config_snapshot_id: str,
) -> str:
    return _sha256_hex(
        _binding_material(
            proposal_id,
            candidate,
            sandbox_result,
            config_snapshot_artifact_id,
            rollback_target_artifact_id,
            rollback_target_config_snapshot_id,
        )
    )


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #


class WrapperArtifactController:
    """Drive one wrapper proposal through its eight-state lifecycle.

    The controller is intentionally stateful and in-memory: it owns the
    proposal journal, the single-use approval journal, and the audit trail of
    activation/rollback events.  Pointer mutation is delegated to the wired
    :class:`RevisionPointerStore`; artifact storage to the wired
    :class:`ArtifactRegistry`.  Nothing in this class touches a database,
    ModelHub (beyond the inline evaluator), or the network.
    """

    def __init__(
        self,
        registry: ArtifactRegistry,
        pointers: RevisionPointerStore,
        *,
        clock: datetime | None = None,
        pointer_name: str = "wrapper",
    ) -> None:
        self.registry = registry
        self.pointers = pointers
        self.pointer_name = pointer_name
        self._clock = clock  # if None, _now() returns real utcnow
        self._proposals: dict[str, _ProposalState] = {}
        self._approvals: dict[str, ApprovalRecord] = {}
        self._consumed_approvals: dict[str, str] = {}  # approval_id -> activation_id
        # Artifact ids that have ever been *activated*.  Rollback targets must
        # be drawn from this set, so a caller cannot point rollback at an
        # arbitrary artifact that was never approved.
        self._approved_artifacts: dict[str, str] = {}  # artifact_id -> config_snapshot_id
        self._activation_events: list[ActivationEvent] = []
        self._rollback_events: list[RollbackEvent] = []

    # ------------------------- internal helpers ------------------------- #

    def _now(self) -> datetime:
        if self._clock is not None:
            return self._clock
        return _utcnow()

    def _require_proposal(self, proposal_id: str) -> _ProposalState:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise WrapperArtifactError(f"unknown proposal: {proposal_id!r}")
        return proposal

    def _advance(self, proposal: _ProposalState, target: str) -> None:
        # ``transition`` raises ValueError for any forbidden pair; we promote
        # it to WrapperArtifactError so callers catch a single type.
        try:
            transition(proposal.state, target)
        except ValueError as exc:
            raise WrapperArtifactError(str(exc)) from exc
        proposal.state = target

    @staticmethod
    def _verify_candidate(candidate: CandidateArtifact) -> None:
        if candidate.artifact_id != f"sha256:{candidate.payload_sha256}":
            raise WrapperArtifactError(
                "candidate artifact_id must equal sha256:<payload_sha256>"
            )

    def _verify_artifact_in_registry(self, artifact_id: str) -> ArtifactRecord:
        record = self.registry.get(artifact_id)
        if record is None:
            raise WrapperArtifactError(f"artifact not registered: {artifact_id!r}")
        if record.artifact_id != f"sha256:{record.sha256}":
            raise WrapperArtifactError("registry artifact identity is malformed")
        return record

    # ------------------------- lifecycle steps ------------------------- #

    def create_proposal(
        self,
        *,
        proposal_id: str,
        diagnostic: DiagnosticSource,
        candidate: CandidateArtifact,
        risk: RiskAssessment,
        proposer: ActorPrincipal,
    ) -> str:
        """Open a wrapper proposal in the ``proposal`` state.

        The diagnostic source, candidate artifact, and risk assessment are
        bound at creation; they cannot be substituted later without opening a
        new proposal.  This is the provenance gate: a wrapper cannot elevate
        an artifact whose diagnostic origin is missing.
        """
        proposal_id = _require_nonempty_str(proposal_id, "proposal_id")
        if not isinstance(diagnostic, DiagnosticSource):
            raise WrapperArtifactError("diagnostic must be a DiagnosticSource")
        if not isinstance(candidate, CandidateArtifact):
            raise WrapperArtifactError("candidate must be a CandidateArtifact")
        if not isinstance(risk, RiskAssessment):
            raise WrapperArtifactError("risk must be a RiskAssessment")
        if not isinstance(proposer, ActorPrincipal):
            raise WrapperArtifactError("proposer must be an ActorPrincipal")
        # Provenance: diagnostic id/observer/generated_at all required.
        _require_nonempty_str(diagnostic.diagnostic_id, "diagnostic_id")
        _require_nonempty_str(diagnostic.observer, "observer")
        if diagnostic.generated_at.tzinfo is None:
            raise WrapperArtifactError("diagnostic.generated_at must be timezone-aware")
        if risk.risk_level not in {"low", "medium", "high"}:
            raise WrapperArtifactError("risk.risk_level must be low|medium|high")
        _require_nonempty_str(risk.evaluator, "risk.evaluator")
        self._verify_candidate(candidate)
        self._verify_artifact_in_registry(candidate.artifact_id)
        if proposal_id in self._proposals:
            raise WrapperArtifactError(f"proposal already exists: {proposal_id!r}")

        state = _ProposalState(proposal_id=proposal_id)
        # create_proposal is itself the diagnostics -> proposal transition.
        # We start the in-memory record in ``diagnostics`` so the transition
        # table enforces the entry edge, then advance.
        state.state = "diagnostics"
        self._proposals[proposal_id] = state
        state.diagnostic = diagnostic
        state.candidate = candidate
        state.risk = risk
        state.proposer = proposer
        self._advance(state, "proposal")
        return proposal_id

    def attach_sandbox(
        self,
        *,
        proposal_id: str,
        sandbox_result: SandboxReplayResult,
        sandbox_runner: ActorPrincipal,
    ) -> str:
        """Bind a sandbox replay result and advance to ``sandbox_replay``.

        The sandbox runner is recorded so the approval step can later reject a
        reviewer who is the same principal as the runner (no self-approval).

        The pointer is *not* moved here.  Sandbox isolation is enforced by the
        state machine: ``attach_sandbox`` only transitions the in-memory
        proposal state, leaving the production :class:`RevisionPointerStore`
        untouched.
        """
        proposal = self._require_proposal(proposal_id)
        if not isinstance(sandbox_result, SandboxReplayResult):
            raise WrapperArtifactError("sandbox_result must be a SandboxReplayResult")
        if not isinstance(sandbox_runner, ActorPrincipal):
            raise WrapperArtifactError("sandbox_runner must be an ActorPrincipal")
        _require_nonempty_str(sandbox_result.run_id, "sandbox_result.run_id")
        _require_nonempty_str(sandbox_result.runner_version, "sandbox_result.runner_version")
        if sandbox_result.completed_at.tzinfo is None:
            raise WrapperArtifactError("sandbox_result.completed_at must be timezone-aware")
        if sandbox_result.candidate_artifact_id != proposal.candidate.artifact_id:
            raise WrapperArtifactError(
                "sandbox result is not bound to this proposal's candidate"
            )
        self._advance(proposal, "candidate_build")
        proposal.sandbox_result = sandbox_result
        proposal.sandbox_runner = sandbox_runner
        self._advance(proposal, "sandbox_replay")
        # If the sandbox did not pass we *remain* in sandbox_replay; the review
        # step refuses to advance.  This is the "sandbox isolation" property:
        # a failed sandbox cannot be hidden.
        return proposal.state

    def _confirm_sandbox_passed(self, proposal: _ProposalState) -> SandboxReplayResult:
        sandbox_result = proposal.sandbox_result
        if sandbox_result is None:
            raise WrapperArtifactError("no sandbox replay attached")
        if not sandbox_result.passed:
            raise WrapperArtifactError("sandbox replay did not pass; cannot advance")
        return sandbox_result

    def request_approval(
        self,
        *,
        proposal_id: str,
        reviewer: ReviewerPrincipal,
        config_snapshot: bytes,
        rollback_target_artifact_id: str,
        reason: str,
    ) -> ApprovalRecord:
        """Mint a single-use :class:`ApprovalRecord` for a proposal.

        This is the human gate.  The reviewer must:

        * be a :class:`ReviewerPrincipal` (not the same subject as the
          proposer or the sandbox runner — self-approval is forbidden),
        * not be expired at mint time,
        * name a ``rollback_target_artifact_id`` that is an *already-approved*
          artifact (i.e. one previously activated and tracked by this
          controller), with its config snapshot still in the registry.

        The config snapshot is stored as its own immutable artifact so the
        activation step can later verify the triple binding by recomputing the
        checksum.  The returned :class:`ApprovalRecord` carries the binding
        checksum; callers cannot fabricate one because they cannot compute it
        without the controller's canonical encoder.
        """
        proposal = self._require_proposal(proposal_id)
        if not isinstance(reviewer, ReviewerPrincipal):
            raise WrapperArtifactError("reviewer must be a ReviewerPrincipal")
        _require_nonempty_str(reviewer.subject, "reviewer.subject")
        _require_nonempty_str(reviewer.role, "reviewer.role")
        if reviewer.expires_at.tzinfo is None:
            raise WrapperArtifactError("reviewer.expires_at must be timezone-aware")
        if reviewer.is_expired(now=self._now()):
            raise WrapperArtifactError("reviewer principal has expired")
        _require_nonempty_str(reason, "reason")
        if not isinstance(config_snapshot, (bytes, bytearray)):
            raise WrapperArtifactError("config_snapshot must be bytes")
        rollback_target_artifact_id = _require_nonempty_str(
            rollback_target_artifact_id, "rollback_target_artifact_id"
        )

        # No self-approval: the reviewer must differ from proposer and runner.
        # Both sides are canonicalized at construction, but we re-canonicalize
        # here as defense-in-depth so the separation-of-duties invariant does
        # not depend on every future Principal subtype remembering to
        # canonicalize.  This closes the CISO H1 spoofing vectors (whitespace,
        # case, NFKC-equivalent homoglyphs, and cross-script confusables — the
        # latter already rejected at construction by _assert_no_mixed_script).
        if proposal.proposer is not None and _canonical_subject(reviewer.subject) == _canonical_subject(proposal.proposer.subject):
            raise WrapperArtifactError(
                "reviewer is the same principal as the proposer; self-approval forbidden"
            )
        if proposal.sandbox_runner is not None and _canonical_subject(reviewer.subject) == _canonical_subject(proposal.sandbox_runner.subject):
            raise WrapperArtifactError(
                "reviewer is the same principal as the sandbox runner; self-approval forbidden"
            )

        sandbox_result = self._confirm_sandbox_passed(proposal)

        # Rollback target must be a previously-approved artifact.  This is the
        # core of "rollback to a known-good version": the target cannot be an
        # arbitrary caller-supplied id.
        if rollback_target_artifact_id not in self._approved_artifacts:
            raise WrapperArtifactError(
                "rollback_target_artifact_id is not a previously-approved artifact"
            )
        rollback_target_config_snapshot_id = self._approved_artifacts[rollback_target_artifact_id]
        # The rollback target's config snapshot must still be in the registry,
        # otherwise offline rollback would be impossible.
        if self.registry.get(rollback_target_config_snapshot_id) is None:
            raise WrapperArtifactError(
                "rollback target config snapshot is missing from registry"
            )

        # Store config snapshot as an immutable artifact; its artifact_id is
        # the content-addressed checksum used in the binding.
        config_record = self.registry.put(bytes(config_snapshot), metadata={"role": "config-snapshot"})
        config_snapshot_artifact_id = config_record.artifact_id

        binding_checksum = compute_binding_checksum(
            proposal.proposal_id,
            proposal.candidate,
            sandbox_result,
            config_snapshot_artifact_id,
            rollback_target_artifact_id,
            rollback_target_config_snapshot_id,
        )

        approval = ApprovalRecord(
            approval_id="wap_" + secrets.token_urlsafe(32),
            reviewer=reviewer,
            proposal_id=proposal.proposal_id,
            binding_checksum=binding_checksum,
            issued_at=self._now(),
        )
        self._approvals[approval.approval_id] = approval
        # Only now do we advance to review.  The transition table guarantees
        # request_approval cannot run before sandbox_replay was entered.
        self._advance(proposal, "review")
        # Stash the bound ids on the proposal so activate can re-derive.
        proposal.config_snapshot_artifact_id = config_snapshot_artifact_id
        proposal.rollback_target_artifact_id = rollback_target_artifact_id
        proposal.rollback_target_config_snapshot_id = rollback_target_config_snapshot_id
        return approval

    def activate(
        self,
        *,
        proposal_id: str,
        approval: ApprovalRecord,
        probe_observation: dict[str, Any],
        probe_requirement: ProbeRequirement,
        reason: str,
    ) -> ActivationEvent:
        """Move the pointer to the candidate artifact (``human_activation``).

        Hard requirements, all checked in order:

        1. The proposal is in the ``review`` state.
        2. The ``approval`` is a real :class:`ApprovalRecord` minted by this
           controller for *this* proposal, and not yet consumed.
        3. The approval's reviewer is still authenticated and not expired.
        4. The approval's ``binding_checksum`` matches a freshly recomputed
           checksum over the current proposal state — any drift in candidate,
           sandbox result, config snapshot, or rollback target rejects.
        5. The ModelHub probe (observation + requirement) evaluates to
           ``verified`` *inline*.  Caller cannot supply a pre-baked dict.
        6. The current active pointer matches the rollback target; otherwise
           activating would desync the rollback target.

        Only then is the pointer moved.  The activation event is recorded in
        the controller's audit trail, and the artifact is added to the
        approved-artifact set so it can serve as a future rollback target.
        """
        proposal = self._require_proposal(proposal_id)
        if not isinstance(approval, ApprovalRecord):
            raise WrapperArtifactError("approval must be an ApprovalRecord")
        if not isinstance(probe_requirement, ProbeRequirement):
            raise WrapperArtifactError("probe_requirement must be a ProbeRequirement")
        if not isinstance(probe_observation, dict):
            raise WrapperArtifactError("probe_observation must be a dict")
        _require_nonempty_str(reason, "reason")
        if not isinstance(approval, ApprovalRecord):
            raise WrapperArtifactError("approval must be an ApprovalRecord")
        # Approval must be from this controller's journal and match this proposal.
        # The journal/consumed checks come before the state check so a replay
        # attempt produces a specific audit signal even if the proposal has
        # already advanced past ``review``.
        recorded = self._approvals.get(approval.approval_id)
        if recorded is None:
            raise WrapperArtifactError("approval record is not in the journal")
        if recorded is not approval:
            # Caller passed a forged ApprovalRecord with a known id but
            # different fields.  Identity check rejects it.
            raise WrapperArtifactError("approval record does not match journal entry")
        if recorded.proposal_id != proposal.proposal_id:
            raise WrapperArtifactError("approval record belongs to a different proposal")
        if approval.approval_id in self._consumed_approvals:
            raise WrapperArtifactError("approval record has already been consumed")
        if proposal.state != "review":
            raise WrapperArtifactError(
                f"activate requires review state; proposal is in {proposal.state!r}"
            )
        if approval.reviewer.is_expired(now=self._now()):
            raise WrapperArtifactError("approval reviewer has expired")
        # Re-derive binding checksum from current proposal state.
        sandbox_result = self._confirm_sandbox_passed(proposal)
        recomputed = compute_binding_checksum(
            proposal.proposal_id,
            proposal.candidate,
            sandbox_result,
            proposal.config_snapshot_artifact_id,
            proposal.rollback_target_artifact_id,
            proposal.rollback_target_config_snapshot_id,
        )
        if recomputed != approval.binding_checksum:
            raise WrapperArtifactError(
                "approval binding checksum does not match current proposal state"
            )
        # Inline ModelHub evaluation: caller cannot fake a "verified" status
        # without supplying a legitimate observation/requirement pair.
        probe_report = evaluate_modelhub_readonly_probe(
            probe_observation, probe_requirement
        )
        if probe_report.get("status") != "verified":
            raise WrapperArtifactError(
                f"ModelHub probe did not verify (status={probe_report.get('status')!r}); "
                "wrapper candidate must remain disabled"
            )
        # Current active pointer must equal the rollback target.  This catches
        # a concurrent activation that already moved the pointer.
        current_pointer = self.pointers.pointer(self.pointer_name)
        if current_pointer.active_artifact_id != proposal.rollback_target_artifact_id:
            raise WrapperArtifactError(
                "current active pointer does not match the rollback target"
            )

        # Move the pointer: stage then activate.  Both go through the wired
        # RevisionPointerStore, which records the append-only history.
        self.pointers.stage(
            self.pointer_name,
            proposal.candidate.artifact_id,
            actor=approval.reviewer.subject,
            now=self._now().timestamp(),
        )
        self.pointers.activate(
            self.pointer_name,
            actor=approval.reviewer.subject,
            now=self._now().timestamp(),
        )

        activation = ActivationEvent(
            activation_id="wac_" + secrets.token_urlsafe(24),
            proposal_id=proposal.proposal_id,
            actor_subject=approval.reviewer.subject,
            activated_artifact_id=proposal.candidate.artifact_id,
            config_snapshot_artifact_id=proposal.config_snapshot_artifact_id,
            rollback_target_artifact_id=proposal.rollback_target_artifact_id,
            rollback_target_config_snapshot_id=proposal.rollback_target_config_snapshot_id,
            approval_id=approval.approval_id,
            modelhub_probe_status=probe_report["status"],
            reason=reason,
            at=self._now(),
        )
        self._activation_events.append(activation)
        self._consumed_approvals[approval.approval_id] = activation.activation_id
        # The freshly activated artifact + its config snapshot become a valid
        # future rollback target.
        self._approved_artifacts[activation.activated_artifact_id] = (
            activation.config_snapshot_artifact_id
        )
        proposal.activation = activation
        self._advance(proposal, "human_activation")
        return activation

    def begin_monitoring(self, *, proposal_id: str) -> str:
        """Acknowledge activation and enter steady-state ``monitoring``."""
        proposal = self._require_proposal(proposal_id)
        self._advance(proposal, "monitoring")
        return proposal.state

    def rollback(
        self,
        *,
        proposal_id: str,
        actor: ReviewerPrincipal,
        reason: str,
    ) -> RollbackEvent:
        """Roll the pointer back to the activation's recorded rollback target.

        Deliberately **offline**: no ModelHub probe is required.  The rollback
        target was bound into the approval and stored in the local registry at
        activation time, so this method succeeds even when ModelHub is
        unreachable.  Fail-closed for forward activation, fail-safe for
        rollback to a known-good artifact.
        """
        proposal = self._require_proposal(proposal_id)
        if not isinstance(actor, ReviewerPrincipal):
            raise WrapperArtifactError("actor must be a ReviewerPrincipal")
        if actor.is_expired(now=self._now()):
            raise WrapperArtifactError("rollback actor has expired")
        _require_nonempty_str(reason, "reason")
        # The state machine allows rollback only from human_activation or
        # monitoring; this call raises if we are in some other state.  We
        # check it before the activation-presence check so the structural
        # rule produces a clear audit signal for any pre-activation rollback
        # attempt.
        self._advance(proposal, "rollback")
        if proposal.activation is None:
            # Defensive: state machine already prevents reaching here from a
            # pre-activation state, but we keep the check to fail closed
            # against any future state-machine extension.
            raise WrapperArtifactError("proposal has not been activated; nothing to roll back")
        if proposal.rollback is not None:
            raise WrapperArtifactError("proposal has already been rolled back")
        target_artifact_id = proposal.rollback_target_artifact_id
        target_config_snapshot_id = proposal.rollback_target_config_snapshot_id
        # Defensive: target must still be a known approved artifact.
        if target_artifact_id not in self._approved_artifacts:
            raise WrapperArtifactError("rollback target is no longer in approved set")
        # Restore the active pointer to the rollback target.  Offline by
        # construction: no ModelHub call here.  The pointer store raises
        # ValueError on invariant violations (e.g. unknown artifact); promote
        # to WrapperArtifactError so callers catch a single domain type.
        try:
            self.pointers.rollback(
                self.pointer_name,
                target_artifact_id,
                actor=actor.subject,
                now=self._now().timestamp(),
            )
        except WrapperArtifactError:
            raise
        except ValueError as exc:
            raise WrapperArtifactError(f"pointer rollback failed: {exc}") from exc
        event = RollbackEvent(
            rollback_id="war_" + secrets.token_urlsafe(24),
            proposal_id=proposal.proposal_id,
            actor_subject=actor.subject,
            target_artifact_id=target_artifact_id,
            target_config_snapshot_id=target_config_snapshot_id,
            reason=reason,
            at=self._now(),
        )
        self._rollback_events.append(event)
        proposal.rollback = event
        return event

    # ------------------------- introspection ------------------------- #

    def state(self, proposal_id: str) -> str:
        return self._require_proposal(proposal_id).state

    def activation_events(self) -> tuple[ActivationEvent, ...]:
        return tuple(self._activation_events)

    def rollback_events(self) -> tuple[RollbackEvent, ...]:
        return tuple(self._rollback_events)

    def approved_artifacts(self) -> dict[str, str]:
        """Snapshot of artifact_id -> config_snapshot_id for rollback targets."""
        return dict(self._approved_artifacts)


__all__ = [
    "ActorPrincipal",
    "ActivationEvent",
    "ApprovalRecord",
    "CandidateArtifact",
    "DatasetManifest",
    "DiagnosticSource",
    "RollbackEvent",
    "ReviewerPrincipal",
    "RiskAssessment",
    "SandboxReplayResult",
    "WrapperArtifactController",
    "WrapperArtifactError",
    "compute_binding_checksum",
]
