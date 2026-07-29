"""Pure v4 evidence-action and transaction-intent contract.

This module describes signed inputs.  It does not verify production trust
anchors, consume nonces, derive current release state, publish evidence, or
confer release authority.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping

from trustforge.agent.shadow_contracts import canonical_json

EVIDENCE_ACTION_SCHEMA = "trustforge.release-evidence-action/v4"
EVIDENCE_ACTION_VERSION = "trustforge.release-evidence-authorization/v4"
EVIDENCE_ACTION = "derive-and-publish-release-ingress-evidence"
CEO_SIGNING_DOMAIN = b"trustforge.release-evidence-ceo-authorization.v4\x00"
OPERATOR_SIGNING_DOMAIN = b"trustforge.release-evidence-operator-authorization.v4\x00"
TRANSACTION_INTENT_DOMAIN = b"trustforge.release-evidence-transaction-intent.v1\x00"
MAX_AUTHORIZATION_LIFETIME = timedelta(minutes=15)
MAX_FUTURE_SKEW = timedelta(seconds=30)

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_EVENT_HASH = re.compile(r"[0-9a-f]{64}\Z")
_LEDGER_ID = re.compile(r"[0-9a-f]{32}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40,64}\Z")
_SIGNATURE = re.compile(r"[0-9a-f]{128}\Z")


class EvidenceActionContractError(ValueError):
    """A v4 evidence-action input violates the pure shared contract."""


@dataclass(frozen=True, slots=True)
class EvidenceActionScopeV4:
    target: str
    candidate: str
    active_release_digest: str
    candidate_release_digest: str
    release_manifest_digest: str
    promotion_pass_event_hash: str
    git_sha: str
    dataset_digest: str
    policy_digest: str
    ramp_digest: str
    pit_cutoff: str
    evidence_bundle_digest: str
    routing_key_id: str
    control_ledger_id: str
    expected_control_head: str
    expected_sequence: int
    transcript_v2_digest: str
    provenance_digest: str
    evidence_key_id: str


@dataclass(frozen=True, slots=True)
class EvidenceActionEnvelopeV4:
    schema: str
    version: str
    role: str
    action: str
    target: str
    candidate: str
    active_release_digest: str
    candidate_release_digest: str
    release_manifest_digest: str
    promotion_pass_event_hash: str
    git_sha: str
    dataset_digest: str
    policy_digest: str
    ramp_digest: str
    pit_cutoff: str
    evidence_bundle_digest: str
    routing_key_id: str
    control_ledger_id: str
    expected_control_head: str
    expected_sequence: int
    transcript_v2_digest: str
    provenance_digest: str
    evidence_key_id: str
    actor: str
    issued_at: str
    expires_at: str
    nonce: str
    key_id: str
    signature: str

    def unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("signature")
        return value

    def scope(self) -> EvidenceActionScopeV4:
        names = EvidenceActionScopeV4.__dataclass_fields__
        return EvidenceActionScopeV4(**{name: getattr(self, name) for name in names})


@dataclass(frozen=True, slots=True)
class EvidenceActionIntentDescription:
    """Deterministic inert binding for later OS-trusted integration."""

    intent_digest: str
    scope: EvidenceActionScopeV4
    ceo_envelope: EvidenceActionEnvelopeV4
    operator_envelope: EvidenceActionEnvelopeV4


def _utc(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise EvidenceActionContractError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceActionContractError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceActionContractError(f"{label} timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_scope_format(scope: EvidenceActionScopeV4) -> None:
    """Validate syntax only; this does not establish current release state."""

    if (
        not all(
            _nonempty(getattr(scope, name))
            for name in (
                "target",
                "candidate",
                "routing_key_id",
                "evidence_key_id",
            )
        )
        or any(
            not isinstance(getattr(scope, name), str)
            or _SHA256.fullmatch(getattr(scope, name)) is None
            for name in (
                "active_release_digest",
                "candidate_release_digest",
                "release_manifest_digest",
                "dataset_digest",
                "policy_digest",
                "ramp_digest",
                "evidence_bundle_digest",
                "transcript_v2_digest",
                "provenance_digest",
            )
        )
        or not isinstance(scope.promotion_pass_event_hash, str)
        or _EVENT_HASH.fullmatch(scope.promotion_pass_event_hash) is None
        or not isinstance(scope.expected_control_head, str)
        or _EVENT_HASH.fullmatch(scope.expected_control_head) is None
        or not isinstance(scope.control_ledger_id, str)
        or _LEDGER_ID.fullmatch(scope.control_ledger_id) is None
        or not isinstance(scope.git_sha, str)
        or _GIT_SHA.fullmatch(scope.git_sha) is None
        or not isinstance(scope.expected_sequence, int)
        or isinstance(scope.expected_sequence, bool)
        or scope.expected_sequence < 1
    ):
        raise EvidenceActionContractError("evidence-action scope format is invalid")
    _utc(scope.pit_cutoff, label="PIT cutoff")


def load_evidence_action_envelope_v4(
    payload: Mapping[str, Any],
) -> EvidenceActionEnvelopeV4:
    """Load only the exact v4 wire object; compatibility is forbidden."""

    if not isinstance(payload, Mapping):
        raise EvidenceActionContractError("evidence-action envelope is not an object")
    value = dict(payload)
    expected = set(EvidenceActionEnvelopeV4.__dataclass_fields__)
    if set(value) != expected:
        raise EvidenceActionContractError("evidence-action envelope is not exact v4")
    try:
        envelope = EvidenceActionEnvelopeV4(**value)
    except TypeError as exc:
        raise EvidenceActionContractError(
            "evidence-action envelope types are invalid"
        ) from exc
    validate_scope_format(envelope.scope())
    if not all(
        isinstance(getattr(envelope, name), str)
        for name in (
            "schema",
            "version",
            "role",
            "action",
            "actor",
            "issued_at",
            "expires_at",
            "nonce",
            "key_id",
            "signature",
        )
    ):
        raise EvidenceActionContractError("evidence-action envelope types are invalid")
    if (
        envelope.schema != EVIDENCE_ACTION_SCHEMA
        or envelope.version != EVIDENCE_ACTION_VERSION
        or envelope.role not in {"ceo", "operator"}
        or envelope.action != EVIDENCE_ACTION
        or _SIGNATURE.fullmatch(envelope.signature) is None
    ):
        raise EvidenceActionContractError(
            "evidence-action envelope domain binding is invalid"
        )
    return envelope


def build_unsigned_evidence_action_v4(
    *,
    role: Literal["ceo", "operator"],
    scope: EvidenceActionScopeV4,
    actor: str,
    issued_at: str,
    expires_at: str,
    nonce: str,
    key_id: str,
) -> dict[str, Any]:
    """Build the only canonical unsigned v4 producer representation."""

    validate_scope_format(scope)
    if role not in {"ceo", "operator"}:
        raise EvidenceActionContractError("evidence-action role is invalid")
    if not all(_nonempty(item) for item in (actor, nonce, key_id)):
        raise EvidenceActionContractError("evidence-action identity is invalid")
    issued = _utc(issued_at, label="issued_at")
    expires = _utc(expires_at, label="expires_at")
    if expires <= issued or expires - issued > MAX_AUTHORIZATION_LIFETIME:
        raise EvidenceActionContractError("evidence-action lifetime is invalid")
    return {
        "schema": EVIDENCE_ACTION_SCHEMA,
        "version": EVIDENCE_ACTION_VERSION,
        "role": role,
        "action": EVIDENCE_ACTION,
        **asdict(scope),
        "actor": actor,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
        "key_id": key_id,
    }


def evidence_action_signing_bytes(
    unsigned: Mapping[str, Any],
    *,
    role: Literal["ceo", "operator"],
) -> bytes:
    """Return deterministic role-separated signing bytes for an exact object."""

    expected = set(EvidenceActionEnvelopeV4.__dataclass_fields__) - {"signature"}
    value = dict(unsigned)
    if set(value) != expected or value.get("role") != role:
        raise EvidenceActionContractError(
            "unsigned evidence-action object is not exact for role"
        )
    try:
        placeholder = load_evidence_action_envelope_v4(
            {**value, "signature": "0" * 128}
        )
    except EvidenceActionContractError:
        raise
    if (
        placeholder.schema != EVIDENCE_ACTION_SCHEMA
        or placeholder.version != EVIDENCE_ACTION_VERSION
        or placeholder.action != EVIDENCE_ACTION
    ):
        raise EvidenceActionContractError(
            "unsigned evidence-action domain binding is invalid"
        )
    domain = {
        "ceo": CEO_SIGNING_DOMAIN,
        "operator": OPERATOR_SIGNING_DOMAIN,
    }[role]
    return domain + canonical_json(value)


def describe_evidence_action_intent(
    *,
    ceo_payload: Mapping[str, Any],
    operator_payload: Mapping[str, Any],
    observed_at: datetime,
) -> EvidenceActionIntentDescription:
    """Check pair structure and return an inert deterministic description."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise EvidenceActionContractError("observation clock lacks timezone")
    now = observed_at.astimezone(timezone.utc)
    ceo = load_evidence_action_envelope_v4(ceo_payload)
    operator = load_evidence_action_envelope_v4(operator_payload)
    if (
        ceo.schema != EVIDENCE_ACTION_SCHEMA
        or operator.schema != EVIDENCE_ACTION_SCHEMA
        or ceo.version != EVIDENCE_ACTION_VERSION
        or operator.version != EVIDENCE_ACTION_VERSION
        or ceo.role != "ceo"
        or operator.role != "operator"
        or ceo.action != EVIDENCE_ACTION
        or operator.action != EVIDENCE_ACTION
        or ceo.scope() != operator.scope()
        or ceo.actor == operator.actor
        or ceo.key_id == operator.key_id
        or ceo.nonce == operator.nonce
        or not all(
            _nonempty(item)
            for item in (
                ceo.actor,
                operator.actor,
                ceo.key_id,
                operator.key_id,
                ceo.nonce,
                operator.nonce,
            )
        )
        or not isinstance(ceo.signature, str)
        or _SIGNATURE.fullmatch(ceo.signature) is None
        or not isinstance(operator.signature, str)
        or _SIGNATURE.fullmatch(operator.signature) is None
    ):
        raise EvidenceActionContractError("evidence-action pair structure is invalid")
    for label, envelope in (("CEO", ceo), ("operator", operator)):
        issued = _utc(envelope.issued_at, label=f"{label} issued_at")
        expires = _utc(envelope.expires_at, label=f"{label} expires_at")
        if (
            issued > now + MAX_FUTURE_SKEW
            or expires <= now
            or expires <= issued
            or expires - issued > MAX_AUTHORIZATION_LIFETIME
        ):
            raise EvidenceActionContractError(
                f"{label} evidence-action time window is invalid"
            )
    material = {
        "schema": "trustforge.release-evidence-transaction-intent/v1",
        "scope": asdict(ceo.scope()),
        "ceo_envelope": asdict(ceo),
        "operator_envelope": asdict(operator),
    }
    digest = (
        "sha256:"
        + hashlib.sha256(
            TRANSACTION_INTENT_DOMAIN + canonical_json(material)
        ).hexdigest()
    )
    return EvidenceActionIntentDescription(
        intent_digest=digest,
        scope=ceo.scope(),
        ceo_envelope=ceo,
        operator_envelope=operator,
    )


__all__ = [
    "CEO_SIGNING_DOMAIN",
    "EVIDENCE_ACTION",
    "EVIDENCE_ACTION_SCHEMA",
    "EVIDENCE_ACTION_VERSION",
    "MAX_AUTHORIZATION_LIFETIME",
    "OPERATOR_SIGNING_DOMAIN",
    "TRANSACTION_INTENT_DOMAIN",
    "EvidenceActionContractError",
    "EvidenceActionEnvelopeV4",
    "EvidenceActionIntentDescription",
    "EvidenceActionScopeV4",
    "build_unsigned_evidence_action_v4",
    "describe_evidence_action_intent",
    "evidence_action_signing_bytes",
    "load_evidence_action_envelope_v4",
    "validate_scope_format",
]
