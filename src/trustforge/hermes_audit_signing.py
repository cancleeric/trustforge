"""Ed25519 attestations that make Hermes evidence bundles third-party verifiable.

`write_evidence_bundle()` already produces a self-consistent canonical-JSON
SHA-256 digest, but that digest is only as trustworthy as the operator who
computed it -- anyone with write access to the evidence directory can edit
`evidence.json` and recompute a matching `SHA256SUMS`. This module adds an
`EvidenceAttestationV1` signed with Ed25519 so a third party holding only the
public verification key can detect tampering after the fact.

Verification follows the domain-separated Ed25519 pattern used by
`verified_receipt_release_gate._verify_ceo` (domain-prefixed canonical JSON,
hex-encoded signature); canonical-JSON signing bytes follow the
`evidence_action_intent` convention of validating an exact unsigned key set
before signing. Key material is loaded with `secure_keyring`
(`read_private_keyring`/`read_public_keyring`); nonce replay protection is
provided by `AuthenticatedLedger`, whose `append()` already rejects a reused
`event["nonce"]` ledger-wide (not merely per audit_id).
"""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .authenticated_ledger import AuthenticatedLedger, LedgerError, NonceAlreadyConsumed
from .hermes_audit_contracts import (
    AuditBundle,
    AuditContractError,
    AuditTarget,
    SAFE_RELEASE_RE,
    _AUDIT_ID_RE,
    _INSTANCE_ID_RE,
    _REGION_RE,
    _parse_utc,
    _require_digest,
    _require_exact_keys,
    _require_safe_identifier,
    _require_status,
    canonical_json,
    has_secret_like_value,
)

EVIDENCE_BUNDLE_SCHEMA = "trustforge.hermes-audit-evidence-attestation/v1"
EVIDENCE_BUNDLE_SIGNING_DOMAIN = b"trustforge.hermes-audit-evidence-attestation.v1\x00"

# This is an evidentiary attestation, not an authorization gate: a generous
# age window is intentional (unlike the 15-minute CEO authorization window in
# verified_receipt_release_gate.py), but it still must be bounded so a
# stolen/leaked signed attestation cannot be replayed indefinitely.
MAX_FUTURE_SKEW = timedelta(seconds=30)
MAX_ATTESTATION_AGE = timedelta(hours=24)

_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}\Z")

# --- Approval-record attestations (Phase 3 / plan scope (a)) ---------------
#
# Four independent Ed25519 roles (CEO, CPO, CISO, operator), each with its own
# domain-separated signing domain -- so a private key that legitimately signs
# one role's approval can never be replayed as a signature for a different
# role. This is an authorization gate (not merely evidentiary), so unlike
# MAX_ATTESTATION_AGE above its validity window is bounded much more tightly,
# but still deliberately longer than evidence_action_intent.py's 15-minute
# release-authorization window: a human "CEO change window" for scheduling a
# bounded, read-only production audit is not the same operation as authorizing
# a release, and runbook practice allows more lead time between the four
# people signing and the operator actually invoking the CLI.
APPROVAL_RECORD_SCHEMA = "trustforge.hermes-audit-approval/v1"
APPROVAL_CEO_SIGNING_DOMAIN = b"trustforge.hermes-audit-approval-ceo.v1\x00"
APPROVAL_CPO_SIGNING_DOMAIN = b"trustforge.hermes-audit-approval-cpo.v1\x00"
APPROVAL_CISO_SIGNING_DOMAIN = b"trustforge.hermes-audit-approval-ciso.v1\x00"
APPROVAL_OPERATOR_SIGNING_DOMAIN = b"trustforge.hermes-audit-approval-operator.v1\x00"
APPROVAL_NONCE_LEDGER_KEY_ID = "approval-verification-keyring"
MAX_APPROVAL_FUTURE_SKEW = timedelta(seconds=30)
MAX_APPROVAL_WINDOW = timedelta(hours=6)

_APPROVAL_ROLES = frozenset({"ceo", "cpo", "ciso", "operator"})
_APPROVAL_SIGNING_DOMAINS = {
    "ceo": APPROVAL_CEO_SIGNING_DOMAIN,
    "cpo": APPROVAL_CPO_SIGNING_DOMAIN,
    "ciso": APPROVAL_CISO_SIGNING_DOMAIN,
    "operator": APPROVAL_OPERATOR_SIGNING_DOMAIN,
}
# Unlike _SAFE_IDENTIFIER_RE (hermes_audit_contracts), output_dir must also
# accept a leading "/" (an absolute path) or leading "." -- CLI callers pass
# both relative ("out/audits/hermes") and resolved absolute paths.
_SAFE_PATH_RE = re.compile(r"[A-Za-z0-9/.][A-Za-z0-9._:@/+\-]{0,1023}\Z")


def _require_safe_path(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 1024
        or _SAFE_PATH_RE.fullmatch(value) is None
        or has_secret_like_value(value)
    ):
        raise AuditContractError(f"{name} must be a secret-free bounded path")
    return value


@dataclass(frozen=True, slots=True)
class EvidenceAttestationV1:
    schema: str
    audit_id: str
    canonical_payload_sha256: str
    overall_status: str
    target_region: str
    target_instance_id: str
    captured_at: str
    actor: str
    key_id: str
    issued_at: str
    nonce: str
    signature: str

    def __post_init__(self) -> None:
        if self.schema != EVIDENCE_BUNDLE_SCHEMA:
            raise AuditContractError("evidence attestation schema is unsupported")
        if not isinstance(self.audit_id, str) or _AUDIT_ID_RE.fullmatch(self.audit_id) is None:
            raise AuditContractError("evidence attestation audit id is invalid")
        _require_digest(self.canonical_payload_sha256, "canonical_payload_sha256")
        _require_status(self.overall_status, "overall_status")
        if not isinstance(self.target_region, str) or _REGION_RE.fullmatch(self.target_region) is None:
            raise AuditContractError("evidence attestation target region is invalid")
        if (
            not isinstance(self.target_instance_id, str)
            or _INSTANCE_ID_RE.fullmatch(self.target_instance_id) is None
        ):
            raise AuditContractError("evidence attestation target instance id is invalid")
        _parse_utc(self.captured_at, "captured_at")
        _require_safe_identifier(self.actor, "actor")
        _require_safe_identifier(self.key_id, "key_id")
        _require_safe_identifier(self.nonce, "nonce")
        _parse_utc(self.issued_at, "issued_at")
        if not isinstance(self.signature, str) or _SIGNATURE_RE.fullmatch(self.signature) is None:
            raise AuditContractError("evidence attestation signature is invalid")

    def unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("signature")
        return value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_UNSIGNED_FIELDS = frozenset(EvidenceAttestationV1.__dataclass_fields__) - {"signature"}


def build_unsigned_attestation(
    *,
    bundle: AuditBundle,
    actor: str,
    key_id: str,
    issued_at: str,
    nonce: str,
) -> dict[str, Any]:
    """Derive the unsigned attestation payload from an ``AuditBundle``."""
    if not isinstance(bundle, AuditBundle):
        raise AuditContractError("evidence attestation requires an audit bundle")
    return {
        "schema": EVIDENCE_BUNDLE_SCHEMA,
        "audit_id": bundle.audit_id,
        "canonical_payload_sha256": bundle.canonical_payload_sha256,
        "overall_status": bundle.overall_status.value,
        "target_region": bundle.target.region,
        "target_instance_id": bundle.target.instance_id,
        "captured_at": bundle.captured_at,
        "actor": actor,
        "key_id": key_id,
        "issued_at": issued_at,
        "nonce": nonce,
    }


def attestation_signing_bytes(unsigned: Mapping[str, Any]) -> bytes:
    """Return the domain-prefixed canonical-JSON bytes an Ed25519 key signs."""
    _require_exact_keys(unsigned, set(_UNSIGNED_FIELDS), "unsigned evidence attestation")
    return EVIDENCE_BUNDLE_SIGNING_DOMAIN + canonical_json(dict(unsigned))


def derive_nonce_ledger_keyring(key_id: str, private_key: bytes) -> dict[str, bytes]:
    """Derive a stable local HMAC keyring for the nonce ledger from the same
    Ed25519 signing key, so operators need only manage one keyring file.

    Single domain-separated SHA-256 KDF; if the nonce ledger ever needs to
    rotate independently of the signing key, split this into its own
    keyring file/flag instead of deriving it here.
    """
    derived = hashlib.sha256(
        private_key + b"trustforge.hermes-audit-nonce-ledger.v1\x00"
    ).digest()
    return {key_id: derived}


def sign_evidence_bundle(
    bundle: AuditBundle,
    *,
    private_key: bytes,
    key_id: str,
    actor: str,
    nonce_store: AuthenticatedLedger,
) -> EvidenceAttestationV1:
    """Mint a fresh, nonce-protected attestation for ``bundle``.

    The nonce is consumed in ``nonce_store`` before signing; a
    ``NonceAlreadyConsumed`` from a colliding nonce propagates unchanged so
    the caller can mint a new nonce and retry.
    """
    issued_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    nonce = secrets.token_hex(16)
    unsigned = build_unsigned_attestation(
        bundle=bundle, actor=actor, key_id=key_id, issued_at=issued_at, nonce=nonce,
    )
    nonce_store.append(
        {"schema": EVIDENCE_BUNDLE_SCHEMA, "audit_id": bundle.audit_id, "nonce": nonce}
    )
    try:
        private = Ed25519PrivateKey.from_private_bytes(private_key)
        signature = private.sign(attestation_signing_bytes(unsigned)).hex()
    except (ValueError, TypeError) as exc:
        raise AuditContractError("evidence attestation private key is invalid") from exc
    return EvidenceAttestationV1(**unsigned, signature=signature)


def _parse_iso_utc(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise AuditContractError(f"{name} must be ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AuditContractError(f"{name} lacks timezone")
    return parsed.astimezone(timezone.utc)


def verify_evidence_bundle_attestation(
    attestation: EvidenceAttestationV1,
    bundle: AuditBundle,
    *,
    verification_keys: Mapping[str, bytes],
    now: datetime | None = None,
) -> None:
    """Fail-closed verification: any mismatch or invalid signature raises."""
    if not isinstance(attestation, EvidenceAttestationV1) or not isinstance(bundle, AuditBundle):
        raise AuditContractError("evidence attestation verification inputs are invalid")
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise AuditContractError("evidence attestation verification clock lacks timezone")
    current = current.astimezone(timezone.utc)
    if (
        attestation.audit_id != bundle.audit_id
        or attestation.canonical_payload_sha256 != bundle.canonical_payload_sha256
        or attestation.overall_status != bundle.overall_status.value
        or attestation.target_region != bundle.target.region
        or attestation.target_instance_id != bundle.target.instance_id
        or attestation.captured_at != bundle.captured_at
    ):
        raise AuditContractError("evidence attestation does not match audit bundle")
    key = verification_keys.get(attestation.key_id) if isinstance(verification_keys, Mapping) else None
    if key is None:
        raise AuditContractError("evidence attestation key id is unknown")
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(
            bytes.fromhex(attestation.signature),
            attestation_signing_bytes(attestation.unsigned()),
        )
    except (InvalidSignature, ValueError) as exc:
        raise AuditContractError("evidence attestation signature is invalid") from exc
    issued = _parse_iso_utc(attestation.issued_at, "issued_at")
    if issued > current + MAX_FUTURE_SKEW or current - issued > MAX_ATTESTATION_AGE:
        raise AuditContractError("evidence attestation issued_at is outside the allowed window")


@dataclass(frozen=True, slots=True)
class ApprovalAttestationV1:
    """One role's signed, replay-protected authorization for a single Hermes
    production audit invocation. Four of these (ceo/cpo/ciso/operator) are
    validated together by `validate_approval_bundle`; one alone authorizes
    nothing.
    """

    schema: str
    role: str
    region: str
    instance_id: str
    expected_release: str | None
    output_dir: str
    static_ssm_command_sha256: str
    actor: str
    issued_at: str
    expires_at: str
    nonce: str
    key_id: str
    signature: str

    def __post_init__(self) -> None:
        if self.schema != APPROVAL_RECORD_SCHEMA:
            raise AuditContractError("approval attestation schema is unsupported")
        if self.role not in _APPROVAL_ROLES:
            raise AuditContractError("approval attestation role is invalid")
        if not isinstance(self.region, str) or _REGION_RE.fullmatch(self.region) is None:
            raise AuditContractError("approval attestation region is invalid")
        if (
            not isinstance(self.instance_id, str)
            or _INSTANCE_ID_RE.fullmatch(self.instance_id) is None
        ):
            raise AuditContractError("approval attestation instance id is invalid")
        if self.expected_release is not None and (
            not isinstance(self.expected_release, str)
            or SAFE_RELEASE_RE.fullmatch(self.expected_release) is None
        ):
            raise AuditContractError("approval attestation expected release is invalid")
        _require_safe_path(self.output_dir, "output_dir")
        _require_digest(self.static_ssm_command_sha256, "static_ssm_command_sha256")
        _require_safe_identifier(self.actor, "actor")
        _parse_utc(self.issued_at, "issued_at")
        _parse_utc(self.expires_at, "expires_at")
        _require_safe_identifier(self.nonce, "nonce")
        _require_safe_identifier(self.key_id, "key_id")
        if not isinstance(self.signature, str) or _SIGNATURE_RE.fullmatch(self.signature) is None:
            raise AuditContractError("approval attestation signature is invalid")

    def unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("signature")
        return value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_UNSIGNED_APPROVAL_FIELDS = frozenset(ApprovalAttestationV1.__dataclass_fields__) - {"signature"}


def build_unsigned_approval_v1(
    *,
    role: str,
    region: str,
    instance_id: str,
    expected_release: str | None,
    output_dir: str,
    static_ssm_command_sha256: str,
    actor: str,
    issued_at: str,
    expires_at: str,
    nonce: str,
    key_id: str,
) -> dict[str, Any]:
    """Build one role's canonical unsigned approval-attestation payload."""
    if role not in _APPROVAL_ROLES:
        raise AuditContractError("approval attestation role is invalid")
    return {
        "schema": APPROVAL_RECORD_SCHEMA,
        "role": role,
        "region": region,
        "instance_id": instance_id,
        "expected_release": expected_release,
        "output_dir": output_dir,
        "static_ssm_command_sha256": static_ssm_command_sha256,
        "actor": actor,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
        "key_id": key_id,
    }


def approval_signing_bytes(unsigned: Mapping[str, Any]) -> bytes:
    """Return the role-domain-prefixed canonical-JSON bytes an Ed25519 key signs.

    The domain is selected by ``unsigned["role"]`` so the same private key can
    never have a signature over one role's payload reinterpreted as a
    signature for a different role.
    """
    _require_exact_keys(unsigned, set(_UNSIGNED_APPROVAL_FIELDS), "unsigned approval attestation")
    domain = _APPROVAL_SIGNING_DOMAINS.get(unsigned.get("role"))
    if domain is None:
        raise AuditContractError("approval attestation role is invalid")
    return domain + canonical_json(dict(unsigned))


def load_approval_attestation(payload: Mapping[str, Any]) -> ApprovalAttestationV1:
    """Load only the exact wire object; unknown, missing, or mistyped fields
    are rejected (this is the fix for "anyone can hand-write a JSON file and
    have it accepted" -- the historical CLI gap this phase closes).
    """
    if not isinstance(payload, Mapping):
        raise AuditContractError("approval attestation is not an object")
    value = dict(payload)
    if set(value) != set(ApprovalAttestationV1.__dataclass_fields__):
        raise AuditContractError("approval attestation is not the exact expected schema")
    try:
        return ApprovalAttestationV1(**value)
    except TypeError as exc:
        raise AuditContractError("approval attestation types are invalid") from exc


def sign_approval_attestation(
    *,
    role: str,
    region: str,
    instance_id: str,
    expected_release: str | None,
    output_dir: str,
    static_ssm_command_sha256: str,
    actor: str,
    issued_at: str,
    expires_at: str,
    nonce: str,
    key_id: str,
    private_key: bytes,
) -> ApprovalAttestationV1:
    """Mint one role's signed approval attestation.

    Unlike `sign_evidence_bundle`, this does not consume a nonce: the four
    roles sign independently and offline, ahead of any particular audit
    invocation, so nonce consumption happens exactly once -- inside
    `validate_approval_bundle`, at the moment an operator actually presents
    the four-file bundle to authorize one real audit run.
    """
    unsigned = build_unsigned_approval_v1(
        role=role, region=region, instance_id=instance_id,
        expected_release=expected_release, output_dir=output_dir,
        static_ssm_command_sha256=static_ssm_command_sha256, actor=actor,
        issued_at=issued_at, expires_at=expires_at, nonce=nonce, key_id=key_id,
    )
    try:
        private = Ed25519PrivateKey.from_private_bytes(private_key)
        signature = private.sign(approval_signing_bytes(unsigned)).hex()
    except (ValueError, TypeError) as exc:
        raise AuditContractError("approval attestation private key is invalid") from exc
    return ApprovalAttestationV1(**unsigned, signature=signature)


def derive_approval_nonce_ledger_keyring(verification_keys: Mapping[str, bytes]) -> dict[str, bytes]:
    """Derive a stable local HMAC keyring for the approval-bundle nonce ledger
    from the full public verification keyring.

    Unlike `derive_nonce_ledger_keyring` (which derives from one signer's
    private key), this derives only from public material: verifying an
    approval bundle -- and thus consuming its four nonces -- must never
    require possessing any of the four roles' private keys, only their
    public verification keys (which the CLI already needs to check
    signatures).
    """
    if not isinstance(verification_keys, Mapping) or not verification_keys:
        raise AuditContractError("approval nonce ledger requires a non-empty verification keyring")
    ordered = canonical_json({key_id: key.hex() for key_id, key in verification_keys.items()})
    derived = hashlib.sha256(
        ordered + b"trustforge.hermes-audit-approval-nonce-ledger.v1\x00"
    ).digest()
    return {APPROVAL_NONCE_LEDGER_KEY_ID: derived}


def _consume_approval_nonces(
    nonce_store: AuthenticatedLedger,
    attestations: Mapping[str, "ApprovalAttestationV1"],
) -> None:
    """Consume all four nonces, or none: if any is already used, reject the
    whole batch without consuming the others (a "local" nonce reuse -- e.g.
    only the operator's file being replayed -- must not silently authorize a
    new audit on the strength of three fresh signatures).
    """
    nonces = [item.nonce for item in attestations.values()]
    if len(set(nonces)) != len(nonces):
        raise AuditContractError("approval bundle nonces must be distinct across roles")
    try:
        existing = {
            record["event"].get("nonce")
            for record in nonce_store.read()
            if isinstance(record.get("event"), Mapping)
        }
    except LedgerError as exc:
        raise AuditContractError("approval nonce ledger cannot be read") from exc
    if any(nonce in existing for nonce in nonces):
        raise AuditContractError("an approval attestation nonce was already consumed")
    for role, attestation in attestations.items():
        try:
            nonce_store.append(
                {"schema": APPROVAL_RECORD_SCHEMA, "role": role, "nonce": attestation.nonce}
            )
        except NonceAlreadyConsumed as exc:
            raise AuditContractError("an approval attestation nonce was already consumed") from exc
        except LedgerError as exc:
            raise AuditContractError("approval nonce ledger append failed") from exc


def validate_approval_bundle(
    ceo: ApprovalAttestationV1,
    cpo: ApprovalAttestationV1,
    ciso: ApprovalAttestationV1,
    operator: ApprovalAttestationV1,
    *,
    target: AuditTarget,
    static_ssm_command_sha256: str,
    expected_release: str | None,
    output_dir: str,
    now: datetime,
    verification_keys: Mapping[str, bytes],
    nonce_store: AuthenticatedLedger,
) -> None:
    """Fail-closed verification of a four-role approval bundle.

    Every one of the four roles' signature, target/digest/release/output-dir
    binding, actor/key distinctness, validity window, and nonce is checked;
    any mismatch anywhere raises `AuditContractError` and no nonce is
    consumed.
    """
    if not isinstance(target, AuditTarget):
        raise AuditContractError("approval bundle target is invalid")
    _require_digest(static_ssm_command_sha256, "static_ssm_command_sha256")
    if expected_release is not None and (
        not isinstance(expected_release, str) or SAFE_RELEASE_RE.fullmatch(expected_release) is None
    ):
        raise AuditContractError("approval bundle expected release has invalid grammar")
    _require_safe_path(output_dir, "output_dir")
    if now.tzinfo is None or now.utcoffset() is None:
        raise AuditContractError("approval bundle verification clock lacks timezone")
    current = now.astimezone(timezone.utc)

    attestations = {"ceo": ceo, "cpo": cpo, "ciso": ciso, "operator": operator}
    if any(not isinstance(item, ApprovalAttestationV1) for item in attestations.values()):
        raise AuditContractError("approval bundle requires four signed attestations")
    for role, attestation in attestations.items():
        if attestation.role != role:
            raise AuditContractError(f"{role} approval attestation has the wrong role")

    bindings = {
        (
            item.region, item.instance_id, item.expected_release,
            item.output_dir, item.static_ssm_command_sha256,
        )
        for item in attestations.values()
    }
    expected_binding = (
        target.region, target.instance_id, expected_release,
        output_dir, static_ssm_command_sha256,
    )
    if bindings != {expected_binding}:
        raise AuditContractError(
            "approval bundle does not bind the expected target, release, or command digest"
        )

    actors = [item.actor for item in attestations.values()]
    key_ids = [item.key_id for item in attestations.values()]
    if len(set(actors)) != len(actors):
        raise AuditContractError("approval bundle actors must be distinct across roles (no self-approval)")
    if len(set(key_ids)) != len(key_ids):
        raise AuditContractError("approval bundle key ids must be distinct across roles (no self-approval)")

    resolved_keys: dict[str, bytes] = {}
    for role, attestation in attestations.items():
        key = (
            verification_keys.get(attestation.key_id)
            if isinstance(verification_keys, Mapping)
            else None
        )
        if key is None:
            raise AuditContractError(f"{role} approval attestation key id is unknown")
        resolved_keys[role] = key

    # Distinct key_id *labels* (checked above) are not enough: the keyring
    # could register the same physical Ed25519 public key under four
    # different labels, letting one private key produce all four "distinct"
    # signatures. Require the four resolved public-key byte strings to also
    # be pairwise distinct, or this degrades to a single-approver bypass.
    if len(set(resolved_keys.values())) != len(resolved_keys):
        raise AuditContractError(
            "approval bundle key ids must resolve to distinct public keys (no self-approval)"
        )

    for role, attestation in attestations.items():
        key = resolved_keys[role]
        try:
            Ed25519PublicKey.from_public_bytes(key).verify(
                bytes.fromhex(attestation.signature),
                approval_signing_bytes(attestation.unsigned()),
            )
        except (InvalidSignature, ValueError) as exc:
            raise AuditContractError(f"{role} approval attestation signature is invalid") from exc
        issued = _parse_iso_utc(attestation.issued_at, f"{role} issued_at")
        expires = _parse_iso_utc(attestation.expires_at, f"{role} expires_at")
        if (
            issued > current + MAX_APPROVAL_FUTURE_SKEW
            or expires <= issued
            or expires - issued > MAX_APPROVAL_WINDOW
            or expires <= current
        ):
            raise AuditContractError(f"{role} approval attestation window is invalid or expired")

    _consume_approval_nonces(nonce_store, attestations)


__all__ = [
    "EVIDENCE_BUNDLE_SCHEMA",
    "EVIDENCE_BUNDLE_SIGNING_DOMAIN",
    "MAX_ATTESTATION_AGE",
    "MAX_FUTURE_SKEW",
    "EvidenceAttestationV1",
    "attestation_signing_bytes",
    "build_unsigned_attestation",
    "derive_nonce_ledger_keyring",
    "sign_evidence_bundle",
    "verify_evidence_bundle_attestation",
    "APPROVAL_RECORD_SCHEMA",
    "APPROVAL_CEO_SIGNING_DOMAIN",
    "APPROVAL_CPO_SIGNING_DOMAIN",
    "APPROVAL_CISO_SIGNING_DOMAIN",
    "APPROVAL_OPERATOR_SIGNING_DOMAIN",
    "APPROVAL_NONCE_LEDGER_KEY_ID",
    "MAX_APPROVAL_FUTURE_SKEW",
    "MAX_APPROVAL_WINDOW",
    "ApprovalAttestationV1",
    "approval_signing_bytes",
    "build_unsigned_approval_v1",
    "derive_approval_nonce_ledger_keyring",
    "load_approval_attestation",
    "sign_approval_attestation",
    "validate_approval_bundle",
]
