"""Unit tests for Ed25519 evidence-bundle attestations (Hermes audit signing)."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

import trustforge.hermes_audit_signing as signing
from trustforge.authenticated_ledger import AuthenticatedLedger, NonceAlreadyConsumed
from trustforge.hermes_audit import write_evidence_bundle
from trustforge.hermes_audit_contracts import (
    AggregateSummary,
    AuditBundle,
    AuditContractError,
    AuditLimits,
    AuditStatus,
    AuditTarget,
    ControlPlane,
    ControlState,
    DataPlane,
    EffectiveControl,
    ReleaseComparison,
    ReleaseComparisonStatus,
    TableAudit,
    UnitEvidence,
    SYSTEMD_UNIT_ALLOWLIST,
    TABLE_TYPE_ALLOWLIST,
    sha256_digest,
)
from trustforge.hermes_audit_signing import (
    EVIDENCE_BUNDLE_SCHEMA,
    EvidenceAttestationV1,
    derive_nonce_ledger_keyring,
    sign_evidence_bundle,
    verify_evidence_bundle_attestation,
)

REGION = "ap-southeast-2"
INSTANCE = "i-0" + "0" * 16


def _digest(label: str) -> str:
    return sha256_digest({"label": label})


def _control_plane() -> ControlPlane:
    units = tuple(
        UnitEvidence(
            unit=unit,
            status=AuditStatus.COMPLETE,
            enabled=ControlState.DISABLED,
            active=ControlState.DISABLED,
            result="success",
            unit_sha256=_digest(f"unit-{index}"),
            drop_in_sha256=_digest(f"dropin-{index}"),
        )
        for index, unit in enumerate(SYSTEMD_UNIT_ALLOWLIST)
    )
    return ControlPlane(
        units=units,
        effective_control=EffectiveControl(
            runtime_guard=ControlState.DISABLED,
            dynamodb_config=ControlState.UNKNOWN,
            unit_env=ControlState.UNKNOWN,
            production_default=ControlState.ENABLED,
            effective=ControlState.DISABLED,
        ),
        redacted_config={"region": REGION},
        release_comparison=(
            ReleaseComparison(
                field="manifest-digest",
                expected="release-20260730",
                deployed="release-20260730",
                status=ReleaseComparisonStatus.MATCH,
            ),
        ),
    )


def _data_plane() -> DataPlane:
    audits = tuple(
        TableAudit(
            table_type=table_type,
            status=AuditStatus.COMPLETE,
            requests_used=1,
            items_used=0,
            bytes_used=0,
            truncated=False,
        )
        for table_type in TABLE_TYPE_ALLOWLIST
    )
    summary = AggregateSummary(status=AuditStatus.COMPLETE, count=0, truncated=False)
    return DataPlane(table_audits=audits, scheduler_summary=summary, cache_summary=summary, cost_summary=summary)


def _bundle(
    status: AuditStatus,
    *,
    audit_id: str,
    target: AuditTarget | None = None,
    captured_at: str = "2026-07-31T00:00:00Z",
) -> AuditBundle:
    if status is AuditStatus.COMPLETE:
        blockers: tuple[str, ...] = ()
    elif status is AuditStatus.INTEGRITY_FAILURE:
        blockers = ("integrity-redaction-failure",)
    else:
        blockers = ("insufficient-evidence",)
    return AuditBundle.build(
        audit_id=audit_id,
        captured_at=captured_at,
        target=target if target is not None else AuditTarget(REGION, INSTANCE),
        invoker_identity_hash=_digest("invoker"),
        limits=AuditLimits.defaults(),
        overall_status=status,
        blockers=blockers,
        control_plane=_control_plane(),
        data_plane=_data_plane(),
    )


def _keypair(key_id: str = "signer-1") -> tuple[str, bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public_bytes = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return key_id, private_bytes, public_bytes


def _ledger(tmp_path: Path, key_id: str, private_bytes: bytes, *, name: str = "nonce-ledger") -> AuthenticatedLedger:
    return AuthenticatedLedger(
        keyring=derive_nonce_ledger_keyring(key_id, private_bytes),
        active_key_id=key_id,
        test_directory_override=tmp_path / name,
    )


# 1. Legitimate signature verifies.
def test_valid_signature_verifies(tmp_path: Path) -> None:
    key_id, private_bytes, public_bytes = _keypair()
    ledger = _ledger(tmp_path, key_id, private_bytes)
    bundle = _bundle(AuditStatus.COMPLETE, audit_id="audit-validsign001")
    attestation = sign_evidence_bundle(
        bundle, private_key=private_bytes, key_id=key_id, actor="operator-1", nonce_store=ledger,
    )
    verify_evidence_bundle_attestation(attestation, bundle, verification_keys={key_id: public_bytes})


# 2. All five overall_status values each produce a verifiable ATTESTATION.json
#    via write_evidence_bundle; 3. SHA256SUMS lists ATTESTATION.json.
def test_write_evidence_bundle_signs_every_overall_status(tmp_path: Path) -> None:
    key_id, private_bytes, public_bytes = _keypair()
    ledger = _ledger(tmp_path, key_id, private_bytes)

    def signer(bundle: AuditBundle) -> EvidenceAttestationV1:
        return sign_evidence_bundle(
            bundle, private_key=private_bytes, key_id=key_id, actor="operator-1", nonce_store=ledger,
        )

    statuses = (
        AuditStatus.COMPLETE,
        AuditStatus.BLOCKED,
        AuditStatus.PARTIAL,
        AuditStatus.INSUFFICIENT_EVIDENCE,
        AuditStatus.INTEGRITY_FAILURE,
    )
    # write_evidence_bundle only accepts destinations under the repo's
    # ignored out/ directory (_validated_output_dir); tmp_path cannot be used.
    root = Path(__file__).resolve().parents[1] / "out" / "audits" / "hermes-signing-test" / "all-statuses"
    try:
        for status in statuses:
            bundle = _bundle(status, audit_id=f"audit-allstatus-{status.value.replace('_', '-')}")
            destination = write_evidence_bundle(bundle, root, signer=signer)

            attestation_path = destination / "ATTESTATION.json"
            assert attestation_path.exists()
            attestation = EvidenceAttestationV1(**json.loads(attestation_path.read_text(encoding="utf-8")))
            assert attestation.overall_status == status.value
            verify_evidence_bundle_attestation(attestation, bundle, verification_keys={key_id: public_bytes})

            checksums = (destination / "SHA256SUMS").read_text(encoding="utf-8")
            assert "ATTESTATION.json" in checksums
            digest = hashlib.sha256(attestation_path.read_bytes()).hexdigest()
            assert f"{digest}  ATTESTATION.json" in checksums
    finally:
        shutil.rmtree(root, ignore_errors=True)


# 4. Tampering evidence.json after signing (i.e. attesting one bundle, then
#    checking the attestation against a different, self-consistent bundle) is
#    detected because the pinned canonical_payload_sha256 no longer matches.
def test_tampering_evidence_after_signing_is_detected(tmp_path: Path) -> None:
    key_id, private_bytes, public_bytes = _keypair()
    ledger = _ledger(tmp_path, key_id, private_bytes)
    genuine = _bundle(AuditStatus.COMPLETE, audit_id="audit-tamperone001")
    attestation = sign_evidence_bundle(
        genuine, private_key=private_bytes, key_id=key_id, actor="operator-1", nonce_store=ledger,
    )
    tampered = _bundle(
        AuditStatus.COMPLETE, audit_id="audit-tamperone001", captured_at="2026-08-01T00:00:00Z",
    )
    assert tampered.canonical_payload_sha256 != genuine.canonical_payload_sha256
    with pytest.raises(AuditContractError):
        verify_evidence_bundle_attestation(attestation, tampered, verification_keys={key_id: public_bytes})


# 5. Wrong / unknown key_id fails verification.
def test_unknown_key_id_is_rejected(tmp_path: Path) -> None:
    key_id, private_bytes, _public_bytes = _keypair()
    ledger = _ledger(tmp_path, key_id, private_bytes)
    bundle = _bundle(AuditStatus.COMPLETE, audit_id="audit-unknownkey01")
    attestation = sign_evidence_bundle(
        bundle, private_key=private_bytes, key_id=key_id, actor="operator-1", nonce_store=ledger,
    )
    other_key_id, _other_private, other_public = _keypair("someone-else")
    with pytest.raises(AuditContractError):
        verify_evidence_bundle_attestation(attestation, bundle, verification_keys={other_key_id: other_public})


# 6. A single flipped signature bit is rejected.
def test_flipped_signature_bit_is_rejected(tmp_path: Path) -> None:
    key_id, private_bytes, public_bytes = _keypair()
    ledger = _ledger(tmp_path, key_id, private_bytes)
    bundle = _bundle(AuditStatus.COMPLETE, audit_id="audit-flipbit0001")
    attestation = sign_evidence_bundle(
        bundle, private_key=private_bytes, key_id=key_id, actor="operator-1", nonce_store=ledger,
    )
    raw = bytearray(bytes.fromhex(attestation.signature))
    raw[0] ^= 0x01
    flipped = dataclasses.replace(attestation, signature=raw.hex())
    with pytest.raises(AuditContractError):
        verify_evidence_bundle_attestation(flipped, bundle, verification_keys={key_id: public_bytes})


# 7. An attestation minted for one target cannot be replayed against a bundle
#    with a different target (region/instance reused across audits).
def test_attestation_bound_to_wrong_target_is_rejected(tmp_path: Path) -> None:
    key_id, private_bytes, public_bytes = _keypair()
    ledger = _ledger(tmp_path, key_id, private_bytes)
    bundle_a = _bundle(
        AuditStatus.COMPLETE, audit_id="audit-targetmatch1", target=AuditTarget(REGION, INSTANCE),
    )
    attestation = sign_evidence_bundle(
        bundle_a, private_key=private_bytes, key_id=key_id, actor="operator-1", nonce_store=ledger,
    )
    bundle_b = _bundle(
        AuditStatus.COMPLETE,
        audit_id="audit-targetmatch1",
        target=AuditTarget("us-east-1", "i-0aaaaaaaaaaaaaaaa"),
    )
    with pytest.raises(AuditContractError):
        verify_evidence_bundle_attestation(attestation, bundle_b, verification_keys={key_id: public_bytes})


class _FixedNonceSource:
    """Stand-in for the stdlib ``secrets`` module exposing only ``token_hex``,
    swapped into ``hermes_audit_signing``'s own module-level name so the
    *real* ``secrets`` module (and therefore ``authenticated_ledger``'s
    independent use of it to mint ``ledger_id``) is left untouched.
    """

    def __init__(self, value: str) -> None:
        self._value = value

    def token_hex(self, _n: int) -> str:
        return self._value


# 8. Reusing the same nonce twice on the same ledger (same audit) is rejected.
def test_reused_nonce_is_rejected_for_the_same_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key_id, private_bytes, _public_bytes = _keypair()
    ledger = _ledger(tmp_path, key_id, private_bytes)
    bundle = _bundle(AuditStatus.COMPLETE, audit_id="audit-noncereuse1")
    monkeypatch.setattr(signing, "secrets", _FixedNonceSource("fixed-nonce-same-audit"))
    sign_evidence_bundle(bundle, private_key=private_bytes, key_id=key_id, actor="operator-1", nonce_store=ledger)
    with pytest.raises(NonceAlreadyConsumed):
        sign_evidence_bundle(bundle, private_key=private_bytes, key_id=key_id, actor="operator-1", nonce_store=ledger)


# 9. Nonce uniqueness is ledger-global, not per audit_id: two different audits
#    sharing the same nonce on the same ledger are still rejected.
def test_reused_nonce_is_rejected_across_different_audit_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_id, private_bytes, _public_bytes = _keypair()
    ledger = _ledger(tmp_path, key_id, private_bytes)
    bundle_a = _bundle(AuditStatus.COMPLETE, audit_id="audit-noncecross1")
    bundle_b = _bundle(AuditStatus.COMPLETE, audit_id="audit-noncecross2")
    monkeypatch.setattr(signing, "secrets", _FixedNonceSource("fixed-nonce-cross-audit"))
    sign_evidence_bundle(bundle_a, private_key=private_bytes, key_id=key_id, actor="operator-1", nonce_store=ledger)
    with pytest.raises(NonceAlreadyConsumed):
        sign_evidence_bundle(bundle_b, private_key=private_bytes, key_id=key_id, actor="operator-1", nonce_store=ledger)


# 10. ATTESTATION.json with missing/extra fields or wrong types is rejected at
#     construction time (before any signature check is even reached).
def test_attestation_schema_drift_is_rejected() -> None:
    key_id = "schema-drift-key"
    base = {
        "schema": EVIDENCE_BUNDLE_SCHEMA,
        "audit_id": "audit-schemadrift1",
        "canonical_payload_sha256": sha256_digest({"x": 1}),
        "overall_status": "complete",
        "target_region": REGION,
        "target_instance_id": INSTANCE,
        "captured_at": "2026-07-31T00:00:00Z",
        "actor": "operator-1",
        "key_id": key_id,
        "issued_at": "2026-07-31T00:00:00Z",
        "nonce": "abc123",
        "signature": "00" * 64,
    }

    missing = dict(base)
    del missing["nonce"]
    with pytest.raises(TypeError):
        EvidenceAttestationV1(**missing)

    extra = dict(base)
    extra["unexpected_field"] = "x"
    with pytest.raises(TypeError):
        EvidenceAttestationV1(**extra)

    wrong_type = dict(base)
    wrong_type["issued_at"] = 12345
    with pytest.raises(AuditContractError):
        EvidenceAttestationV1(**wrong_type)

    wrong_status = dict(base)
    wrong_status["overall_status"] = "not-a-real-status"
    with pytest.raises(AuditContractError):
        EvidenceAttestationV1(**wrong_status)


# 11. issued_at outside the allowed window (future skew or stale) is rejected.
def test_expired_or_future_issued_at_is_rejected(tmp_path: Path) -> None:
    key_id, private_bytes, public_bytes = _keypair()
    ledger = _ledger(tmp_path, key_id, private_bytes)
    bundle = _bundle(AuditStatus.COMPLETE, audit_id="audit-agewindow01")
    attestation = sign_evidence_bundle(
        bundle, private_key=private_bytes, key_id=key_id, actor="operator-1", nonce_store=ledger,
    )
    issued = datetime.fromisoformat(attestation.issued_at.replace("Z", "+00:00"))

    with pytest.raises(AuditContractError):
        verify_evidence_bundle_attestation(
            attestation, bundle, verification_keys={key_id: public_bytes},
            now=issued - timedelta(hours=1),
        )
    with pytest.raises(AuditContractError):
        verify_evidence_bundle_attestation(
            attestation, bundle, verification_keys={key_id: public_bytes},
            now=issued + timedelta(hours=48),
        )
    # sanity: right at issuance, verification still succeeds.
    verify_evidence_bundle_attestation(
        attestation, bundle, verification_keys={key_id: public_bytes}, now=issued,
    )
