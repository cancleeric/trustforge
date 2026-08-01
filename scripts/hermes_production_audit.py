#!/usr/bin/env python3
"""Run or dry-run the bounded, read-only Hermes production audit."""
from __future__ import annotations

import argparse
import json
import pwd
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from trustforge.authenticated_ledger import AuthenticatedLedger
from trustforge.hermes_audit import (
    STATIC_SSM_COMMAND_DIGEST,
    create_aws_clients,
    dry_run_plan,
    run_audit,
    write_evidence_bundle,
)
from trustforge.hermes_audit_contracts import (
    AuditBundle,
    AuditContractError,
    AuditLimits,
    AuditStatus,
    AuditTarget,
    exit_code_for,
)
from trustforge.hermes_audit_signing import (
    APPROVAL_NONCE_LEDGER_KEY_ID,
    ApprovalAttestationV1,
    EvidenceAttestationV1,
    derive_approval_nonce_ledger_keyring,
    derive_nonce_ledger_keyring,
    load_approval_attestation,
    sign_evidence_bundle,
    validate_approval_bundle,
)
from trustforge.secure_keyring import read_private_keyring, read_public_keyring


def _read_approval_file(path: Path, role: str) -> ApprovalAttestationV1:
    """Bounded-read one role's approval JSON file and parse it strictly.

    Mirrors the symlink/size guard the old single-file
    ``_validate_approval_record`` used to apply, then delegates all schema
    and business validation to ``load_approval_attestation`` (exact-key
    dataclass construction) so this script itself does not re-implement any
    approval-record parsing rule.
    """
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
            raise AuditContractError(f"{role} approval file must be a bounded regular file")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except AuditContractError:
        raise
    except Exception as exc:
        raise AuditContractError(f"{role} approval file cannot be read") from exc
    try:
        return load_approval_attestation(payload)
    except AuditContractError as exc:
        raise AuditContractError(f"{role} approval file is invalid: {exc}") from exc


def _validate_approvals(args: argparse.Namespace, target: AuditTarget) -> None:
    """Load, verify and nonce-consume the four independent approval files.

    Reuses Phase 1's ``AuthenticatedLedger``/nonce-ledger pattern (via
    ``derive_approval_nonce_ledger_keyring``) and delegates every signature,
    binding, distinctness, window and replay check to
    ``hermes_audit_signing.validate_approval_bundle``; this script only
    wires arguments to that call.
    """
    paths = {
        "ceo": args.ceo_approval,
        "cpo": args.cpo_approval,
        "ciso": args.ciso_approval,
        "operator": args.operator_approval,
    }
    missing = [role for role, path in paths.items() if path is None]
    if missing or args.approval_verification_keyring is None:
        raise AuditContractError(
            "non-dry audit requires --ceo-approval, --cpo-approval, --ciso-approval, "
            "--operator-approval and --approval-verification-keyring"
        )
    attestations = {role: _read_approval_file(path, role) for role, path in paths.items()}
    verification_keys = read_public_keyring(args.approval_verification_keyring)
    nonce_store = AuthenticatedLedger(
        keyring=derive_approval_nonce_ledger_keyring(verification_keys),
        active_key_id=APPROVAL_NONCE_LEDGER_KEY_ID,
        test_directory_override=args.approval_nonce_ledger_dir,
    )
    validate_approval_bundle(
        attestations["ceo"],
        attestations["cpo"],
        attestations["ciso"],
        attestations["operator"],
        target=target,
        static_ssm_command_sha256=STATIC_SSM_COMMAND_DIGEST,
        expected_release=args.expected_release,
        output_dir=str(args.output_dir),
        now=datetime.now(timezone.utc),
        verification_keys=verification_keys,
        nonce_store=nonce_store,
    )


def _load_signer(
    keyring_path: Path, nonce_ledger_dir: Path
) -> Callable[[AuditBundle], EvidenceAttestationV1]:
    """Load the evidence-signing private key and bind it to a nonce ledger.

    ``read_private_keyring`` already rejects a symlinked or loosely
    permissioned keyring file (``secure_keyring.read_protected_json``'s
    ``os.O_NOFOLLOW`` + ownership/mode checks); this helper does not bypass
    that path.
    """
    key_id, private_key, _ = read_private_keyring(keyring_path)
    ledger = AuthenticatedLedger(
        keyring=derive_nonce_ledger_keyring(key_id, private_key),
        active_key_id=key_id,
        test_directory_override=nonce_ledger_dir,
    )
    actor = pwd.getpwuid(os.geteuid()).pw_name

    def signer(bundle: AuditBundle) -> EvidenceAttestationV1:
        return sign_evidence_bundle(
            bundle,
            private_key=private_key,
            key_id=key_id,
            actor=actor,
            nonce_store=ledger,
        )

    return signer


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Hermes production audit")
    parser.add_argument("--region", required=True, help="Explicit AWS region")
    parser.add_argument("--instance-id", required=True, help="Explicit SSM-managed EC2 instance ID")
    parser.add_argument("--output-dir", required=True, type=Path, help="Ignored directory under repository out/")
    parser.add_argument("--expected-release", help="Optional expected deployed release identity")
    parser.add_argument("--ceo-approval", type=Path, help="CEO-signed approval attestation JSON file (required for non-dry audits)")
    parser.add_argument("--cpo-approval", type=Path, help="CPO-signed approval attestation JSON file (required for non-dry audits)")
    parser.add_argument("--ciso-approval", type=Path, help="CISO-signed approval attestation JSON file (required for non-dry audits)")
    parser.add_argument("--operator-approval", type=Path, help="Operator-signed approval attestation JSON file (required for non-dry audits)")
    parser.add_argument("--approval-verification-keyring", type=Path, help="Public keyring used to verify all four approval attestations")
    parser.add_argument("--approval-nonce-ledger-dir", type=Path, default=Path("out/audit-approval-nonce-ledger"), help="Local directory for the approval-attestation nonce ledger")
    parser.add_argument("--signing-keyring", type=Path, help="Ed25519 private keyring used to attest the evidence bundle for non-dry audits")
    parser.add_argument("--nonce-ledger-dir", type=Path, default=Path("out/audit-nonce-ledger"), help="Local directory for the evidence-attestation nonce ledger")
    parser.add_argument("--dry-run", action="store_true", help="Print static plan without constructing AWS clients")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    limits = AuditLimits.defaults()
    try:
        target = AuditTarget(args.region, args.instance_id)
        if args.dry_run:
            print(json.dumps(dry_run_plan(target, args.output_dir, limits, args.expected_release), sort_keys=True))
            return 0
        _validate_approvals(args, target)
        if args.signing_keyring is None:
            raise AuditContractError("non-dry audit requires --signing-keyring")
        signer = _load_signer(args.signing_keyring, args.nonce_ledger_dir)
        bundle = run_audit(
            target,
            create_aws_clients(target.region, limits),
            limits=limits,
            expected_release=args.expected_release,
        )
        output = write_evidence_bundle(bundle, args.output_dir, signer=signer)
        print(json.dumps({
            "status": bundle.overall_status.value,
            "audit_id": bundle.audit_id,
            "canonical_payload_sha256": bundle.canonical_payload_sha256,
            "output_dir": str(output),
        }, sort_keys=True))
        return exit_code_for(bundle.overall_status)
    except AuditContractError:
        print(json.dumps({"status": AuditStatus.INTERNAL_FAILURE.value, "error_class": "AuditContractError"}), file=sys.stderr)
        return exit_code_for(AuditStatus.INTERNAL_FAILURE)
    except Exception as exc:
        print(json.dumps({"status": AuditStatus.INTERNAL_FAILURE.value, "error_class": type(exc).__name__}), file=sys.stderr)
        return exit_code_for(AuditStatus.INTERNAL_FAILURE)


if __name__ == "__main__":
    raise SystemExit(main())
