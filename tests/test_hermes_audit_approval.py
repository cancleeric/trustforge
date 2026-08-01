"""Unit tests for the four-role approval-record anti-forgery gate (Hermes
audit Phase 3 / docs/plans/PLAN-HERMES-PR1197-REMEDIATION-2026-07-31.md
section 3).

These tests exercise ``hermes_audit_signing.validate_approval_bundle`` and
its supporting helpers directly (the same layer
``scripts/hermes_production_audit.py`` now delegates to); CLI-level
integration (missing an ``--xxx-approval`` flag) is covered separately in
``tests/test_hermes_audit.py``.
"""
from __future__ import annotations

import dataclasses
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

from trustforge.authenticated_ledger import AuthenticatedLedger
from trustforge.hermes_audit_contracts import AuditContractError, AuditTarget
from trustforge.hermes_audit_signing import (
    APPROVAL_NONCE_LEDGER_KEY_ID,
    ApprovalAttestationV1,
    derive_approval_nonce_ledger_keyring,
    load_approval_attestation,
    sign_approval_attestation,
    validate_approval_bundle,
)

REGION = "ap-southeast-2"
INSTANCE = "i-0" + "0" * 16
TARGET = AuditTarget(REGION, INSTANCE)
DIGEST = "sha256:" + "ab" * 32
OUTPUT_DIR = "out/audits/hermes/approval-required"
EXPECTED_RELEASE = "v0.27.37"
ROLES = ("ceo", "cpo", "ciso", "operator")


def _keypair(key_id: str) -> tuple[str, bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public_bytes = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return key_id, private_bytes, public_bytes


def _role_keys() -> dict[str, tuple[str, bytes, bytes]]:
    """One distinct Ed25519 keypair per role, as four independent parties
    would hold in production -- never reused across roles."""
    return {role: _keypair(f"{role}-key") for role in ROLES}


def _verification_keys(role_keys: dict[str, tuple[str, bytes, bytes]]) -> dict[str, bytes]:
    return {key_id: public for key_id, _private, public in role_keys.values()}


def _shared_role_keys() -> dict[str, tuple[str, bytes, bytes]]:
    """All four roles keep their own distinct key_id *label* (and their own
    domain-separated signature, since `role` is baked into the signed bytes
    via `approval_signing_bytes`) but the SAME physical Ed25519 keypair is
    registered under all four labels -- reproducing "whoever provisions the
    verification keyring accidentally/maliciously registers one physical
    public key under four different key_id labels" (Bug 2, 2026-07-31
    adversarial codex review, CEO-confirmed by reading the code).
    """
    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public_bytes = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {role: (f"{role}-key", private_bytes, public_bytes) for role in ROLES}


def _sign_role(
    role: str,
    role_keys: dict[str, tuple[str, bytes, bytes]],
    *,
    region: str = REGION,
    instance_id: str = INSTANCE,
    expected_release: str | None = EXPECTED_RELEASE,
    output_dir: str = OUTPUT_DIR,
    static_ssm_command_sha256: str = DIGEST,
    actor: str | None = None,
    issued_at: str = "2026-07-31T00:00:00Z",
    expires_at: str = "2026-07-31T02:00:00Z",
    nonce: str | None = None,
    key_id: str | None = None,
) -> ApprovalAttestationV1:
    role_key_id, private_bytes, _public = role_keys[role]
    return sign_approval_attestation(
        role=role,
        region=region,
        instance_id=instance_id,
        expected_release=expected_release,
        output_dir=output_dir,
        static_ssm_command_sha256=static_ssm_command_sha256,
        actor=actor if actor is not None else f"{role}-actor",
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce if nonce is not None else f"nonce-{role}-{issued_at}",
        key_id=key_id if key_id is not None else role_key_id,
        private_key=private_bytes,
    )


def _valid_bundle(
    role_keys: dict[str, tuple[str, bytes, bytes]], *, nonce_prefix: str = "w1", **overrides
) -> dict[str, ApprovalAttestationV1]:
    return {
        role: _sign_role(role, role_keys, nonce=f"{nonce_prefix}-{role}", **overrides)
        for role in ROLES
    }


def _ledger(tmp_path: Path, verification_keys: dict[str, bytes]) -> AuthenticatedLedger:
    return AuthenticatedLedger(
        keyring=derive_approval_nonce_ledger_keyring(verification_keys),
        active_key_id=APPROVAL_NONCE_LEDGER_KEY_ID,
        test_directory_override=tmp_path / "approval-nonce-ledger",
    )


NOW = datetime(2026, 7, 31, 0, 30, tzinfo=timezone.utc)


def _validate(bundle: dict[str, ApprovalAttestationV1], nonce_store: AuthenticatedLedger, **overrides) -> None:
    kwargs = dict(
        target=TARGET,
        static_ssm_command_sha256=DIGEST,
        expected_release=EXPECTED_RELEASE,
        output_dir=OUTPUT_DIR,
        now=NOW,
    )
    kwargs.update(overrides)
    validate_approval_bundle(
        bundle["ceo"], bundle["cpo"], bundle["ciso"], bundle["operator"],
        nonce_store=nonce_store,
        verification_keys=kwargs.pop("verification_keys"),
        **kwargs,
    )


# 1. Positive: a valid, independently four-signed bundle validates cleanly.
def test_valid_four_signer_bundle_validates(tmp_path: Path) -> None:
    role_keys = _role_keys()
    bundle = _valid_bundle(role_keys)
    ledger = _ledger(tmp_path, _verification_keys(role_keys))
    _validate(bundle, ledger, verification_keys=_verification_keys(role_keys))
    # the four nonces are now recorded (used by the replay tests below).
    consumed = {record["event"].get("nonce") for record in ledger.read()}
    assert consumed == {f"w1-{role}" for role in ROLES}


# 2. A forged / tampered signature (flipped bit) is rejected.
def test_forged_signature_is_rejected(tmp_path: Path) -> None:
    role_keys = _role_keys()
    bundle = _valid_bundle(role_keys)
    raw = bytearray(bytes.fromhex(bundle["ciso"].signature))
    raw[0] ^= 0x01
    bundle["ciso"] = dataclasses.replace(bundle["ciso"], signature=raw.hex())
    ledger = _ledger(tmp_path, _verification_keys(role_keys))
    with pytest.raises(AuditContractError, match="ciso approval attestation signature is invalid"):
        _validate(bundle, ledger, verification_keys=_verification_keys(role_keys))


# 3. A hand-written / unsigned approval JSON (schema-valid but never actually
#    signed by the claimed key) is rejected -- i.e. the signature is checked
#    against the *claimed* key_id's real public key, not merely well-formed.
def test_unsigned_json_claiming_a_real_key_id_is_rejected(tmp_path: Path) -> None:
    role_keys = _role_keys()
    bundle = _valid_bundle(role_keys)
    other_key_id, other_private, _other_public = _keypair("attacker-key")
    forged = _sign_role("cpo", {"cpo": (other_key_id, other_private, b"")}, nonce="w1-cpo")
    # claim the real cpo key_id even though it was signed by a different key.
    bundle["cpo"] = dataclasses.replace(forged, key_id=role_keys["cpo"][0])
    ledger = _ledger(tmp_path, _verification_keys(role_keys))
    with pytest.raises(AuditContractError, match="cpo approval attestation signature is invalid"):
        _validate(bundle, ledger, verification_keys=_verification_keys(role_keys))


# 4. Sharing the same key_id across two roles (one signer impersonating two
#    roles) is rejected even if both signatures independently verify.
def test_shared_key_id_across_roles_is_rejected(tmp_path: Path) -> None:
    role_keys = _role_keys()
    shared_key_id, shared_private, shared_public = role_keys["ceo"]
    bundle = _valid_bundle(role_keys)
    bundle["cpo"] = sign_approval_attestation(
        role="cpo", region=REGION, instance_id=INSTANCE, expected_release=EXPECTED_RELEASE,
        output_dir=OUTPUT_DIR, static_ssm_command_sha256=DIGEST, actor="cpo-actor",
        issued_at="2026-07-31T00:00:00Z", expires_at="2026-07-31T02:00:00Z",
        nonce="w1-cpo", key_id=shared_key_id, private_key=shared_private,
    )
    verification_keys = _verification_keys(role_keys)
    verification_keys[shared_key_id] = shared_public
    ledger = _ledger(tmp_path, verification_keys)
    with pytest.raises(AuditContractError, match="key ids must be distinct"):
        _validate(bundle, ledger, verification_keys=verification_keys)


# 4b. Registering the SAME physical Ed25519 public key under four distinct
#     key_id *labels* (with four textually-distinct actors and four
#     individually-valid, role-domain-separated signatures) must still be
#     rejected: label distinctness alone does not prove four independently
#     controlled signers -- one private key holder could produce all four
#     signatures. This is the fail-closed gap Bug 2 exploited.
def test_shared_physical_key_registered_under_four_distinct_key_id_labels_is_rejected(
    tmp_path: Path,
) -> None:
    role_keys = _shared_role_keys()
    bundle = _valid_bundle(role_keys)
    verification_keys = _verification_keys(role_keys)
    assert len(set(verification_keys.values())) == 1  # sanity: truly one physical key
    ledger = _ledger(tmp_path, verification_keys)
    with pytest.raises(AuditContractError, match="distinct public keys"):
        _validate(bundle, ledger, verification_keys=verification_keys)


# 5. Sharing the same actor across two roles (self-approval) is rejected
#    even with two distinct, individually-valid keys.
def test_shared_actor_across_roles_is_rejected(tmp_path: Path) -> None:
    role_keys = _role_keys()
    bundle = _valid_bundle(role_keys, actor="same-human")
    ledger = _ledger(tmp_path, _verification_keys(role_keys))
    with pytest.raises(AuditContractError, match="actors must be distinct"):
        _validate(bundle, ledger, verification_keys=_verification_keys(role_keys))


# 6. A region/instance_id mismatch across the four attestations is rejected.
def test_region_instance_mismatch_across_signers_is_rejected(tmp_path: Path) -> None:
    role_keys = _role_keys()
    bundle = _valid_bundle(role_keys)
    bundle["operator"] = _sign_role(
        "operator", role_keys, region="us-east-1", instance_id="i-0aaaaaaaaaaaaaaaa", nonce="w1-operator",
    )
    ledger = _ledger(tmp_path, _verification_keys(role_keys))
    with pytest.raises(AuditContractError, match="does not bind the expected target"):
        _validate(bundle, ledger, verification_keys=_verification_keys(role_keys))


# 7. A stale/wrong static_ssm_command_sha256 (any one signer bound to a
#    different command digest than the CLI is about to run) is rejected.
def test_wrong_static_ssm_command_digest_is_rejected(tmp_path: Path) -> None:
    role_keys = _role_keys()
    stale_digest = "sha256:" + "cd" * 32
    bundle = _valid_bundle(role_keys)
    bundle["ciso"] = _sign_role("ciso", role_keys, static_ssm_command_sha256=stale_digest, nonce="w1-ciso")
    ledger = _ledger(tmp_path, _verification_keys(role_keys))
    with pytest.raises(AuditContractError, match="does not bind the expected target"):
        _validate(bundle, ledger, verification_keys=_verification_keys(role_keys))


# 8a. A bundle that is internally consistent but bound to a different
#     expected_release than the CLI's own --expected-release argument is
#     rejected (approvals cannot be silently replayed onto a different
#     release).
def test_expected_release_mismatch_vs_cli_args_is_rejected(tmp_path: Path) -> None:
    role_keys = _role_keys()
    bundle = _valid_bundle(role_keys, expected_release="v0.27.36")
    ledger = _ledger(tmp_path, _verification_keys(role_keys))
    with pytest.raises(AuditContractError, match="does not bind the expected target"):
        _validate(bundle, ledger, verification_keys=_verification_keys(role_keys))


# 8b. Likewise for output_dir.
def test_output_dir_mismatch_vs_cli_args_is_rejected(tmp_path: Path) -> None:
    role_keys = _role_keys()
    bundle = _valid_bundle(role_keys, output_dir="out/audits/hermes/some-other-run")
    ledger = _ledger(tmp_path, _verification_keys(role_keys))
    with pytest.raises(AuditContractError, match="does not bind the expected target"):
        _validate(bundle, ledger, verification_keys=_verification_keys(role_keys))


# 9. Replaying the exact same four-file bundle a second time (same run's
#    nonces already consumed) is rejected.
def test_nonce_reuse_within_the_same_run_is_rejected(tmp_path: Path) -> None:
    role_keys = _role_keys()
    bundle = _valid_bundle(role_keys)
    ledger = _ledger(tmp_path, _verification_keys(role_keys))
    _validate(bundle, ledger, verification_keys=_verification_keys(role_keys))
    with pytest.raises(AuditContractError, match="nonce was already consumed"):
        _validate(bundle, ledger, verification_keys=_verification_keys(role_keys))


# 10. Partial reuse: mixing one already-consumed signer's file (from an
#     earlier successful window) with three freshly-signed ones must reject
#     the *whole* batch, and -- critically -- must not silently consume the
#     three fresh nonces either (verified by then successfully replaying
#     them in a genuinely fresh, all-new bundle).
def test_partial_nonce_reuse_across_windows_rejects_the_whole_batch(tmp_path: Path) -> None:
    role_keys = _role_keys()
    verification_keys = _verification_keys(role_keys)
    ledger = _ledger(tmp_path, verification_keys)

    window1 = _valid_bundle(role_keys, nonce_prefix="w1")
    _validate(window1, ledger, verification_keys=verification_keys)

    window2_mixed = {
        "ceo": window1["ceo"],  # reused, already-consumed nonce
        "cpo": _sign_role("cpo", role_keys, nonce="w2-cpo"),
        "ciso": _sign_role("ciso", role_keys, nonce="w2-ciso"),
        "operator": _sign_role("operator", role_keys, nonce="w2-operator"),
    }
    with pytest.raises(AuditContractError, match="nonce was already consumed"):
        _validate(window2_mixed, ledger, verification_keys=verification_keys)

    # the three fresh window-2 nonces must still be unconsumed: a bundle
    # reusing them (with a brand new ceo nonce) must succeed.
    window3 = {
        "ceo": _sign_role("ceo", role_keys, nonce="w3-ceo"),
        "cpo": window2_mixed["cpo"],
        "ciso": window2_mixed["ciso"],
        "operator": window2_mixed["operator"],
    }
    _validate(window3, ledger, verification_keys=verification_keys)


# 11. An expired attestation (expires_at in the past relative to the
#     verification clock) is rejected.
def test_expired_attestation_is_rejected(tmp_path: Path) -> None:
    role_keys = _role_keys()
    bundle = _valid_bundle(
        role_keys, issued_at="2026-07-30T00:00:00Z", expires_at="2026-07-30T02:00:00Z",
    )
    ledger = _ledger(tmp_path, _verification_keys(role_keys))
    with pytest.raises(AuditContractError, match="window is invalid or expired"):
        _validate(bundle, ledger, verification_keys=_verification_keys(role_keys))


# 12. An attestation issued further in the future than the allowed clock
#     skew is rejected.
def test_future_dated_issued_at_is_rejected(tmp_path: Path) -> None:
    role_keys = _role_keys()
    bundle = _valid_bundle(
        role_keys, issued_at="2026-07-31T05:00:00Z", expires_at="2026-07-31T06:00:00Z",
    )
    ledger = _ledger(tmp_path, _verification_keys(role_keys))
    with pytest.raises(AuditContractError, match="window is invalid or expired"):
        _validate(bundle, ledger, verification_keys=_verification_keys(role_keys))


# 13. Missing any one of the four required attestations (i.e. a caller that
#     did not supply all four signed files) is rejected before any signature
#     is even checked.
def test_missing_one_of_the_four_attestations_is_rejected(tmp_path: Path) -> None:
    role_keys = _role_keys()
    bundle = _valid_bundle(role_keys)
    ledger = _ledger(tmp_path, _verification_keys(role_keys))
    incomplete = dict(bundle)
    incomplete["operator"] = None
    with pytest.raises(AuditContractError, match="requires four signed attestations"):
        _validate(incomplete, ledger, verification_keys=_verification_keys(role_keys))


# 14. Schema drift -- a hand-edited JSON file with a missing field, an extra
#     field, or a wrong-typed field -- is rejected by load_approval_attestation
#     itself, before it can ever reach validate_approval_bundle.
def test_schema_drift_is_rejected_by_load_approval_attestation() -> None:
    role_keys = _role_keys()
    signed = _sign_role("ceo", role_keys, nonce="w1-ceo")
    base = signed.to_dict()

    missing = dict(base)
    del missing["nonce"]
    with pytest.raises(AuditContractError, match="not the exact expected schema"):
        load_approval_attestation(missing)

    extra = dict(base)
    extra["unexpected_field"] = "x"
    with pytest.raises(AuditContractError, match="not the exact expected schema"):
        load_approval_attestation(extra)

    wrong_type = dict(base)
    wrong_type["issued_at"] = 12345
    with pytest.raises(AuditContractError):
        load_approval_attestation(wrong_type)

    # sanity: the untouched, genuinely signed payload still loads and
    # round-trips to an equal attestation.
    assert load_approval_attestation(base) == signed
