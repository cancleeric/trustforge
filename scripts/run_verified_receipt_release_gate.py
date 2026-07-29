#!/usr/bin/env python3
"""Production composition for the verified receipt -> canary start gate."""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import pwd
import tempfile
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trustforge.asset_intrinsic_promotion_receipt import (
    EVENT_KIND,
    FAILURE_EVENT_KIND,
    SIGNER_DOMAIN,
)
from trustforge.deployment_control import DeploymentAuthorization, DeploymentControlLedger
from trustforge.release_manifest import ReleaseManifest
from trustforge.release_router import ReleaseEndpoint, RoutingPolicy
from trustforge.secure_keyring import (
    read_private_keyring,
    read_protected_json,
    read_public_keyring,
)
from trustforge.signed_event_ledger import SignedEventLedger
from trustforge.verified_receipt_release_gate import (
    AUDIT_INTENT_KIND,
    AUDIT_OUTCOME_KIND,
    VerifiedReceiptCEOAuthorization,
    VerifiedReceiptReleaseGate,
)

AUDIT_DOMAIN = "verified-receipt-release-gate"
CONTROL_DOMAIN = "release-control"
OUTCOME_DOMAIN = "release-router-outcome"


def _required(value: Path | None, option: str) -> Path:
    if value is None:
        raise SystemExit(f"{option} is required for this command")
    return value


def _construct(contract, value: Any, label: str):
    if not isinstance(value, dict) or set(value) != {
        item.name for item in fields(contract)
    }:
        raise SystemExit(f"{label} contract is invalid")
    try:
        return contract(**value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{label} contract is invalid") from exc


def _digest(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            not __import__("stat").S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise SystemExit("release manifest changed during read")
    finally:
        os.close(descriptor)
    return "sha256:" + digest.hexdigest()


def _identity(args):
    test = args.test_owner_current_user
    if test and Path(tempfile.gettempdir()).resolve() not in (
        args.ledger_root.resolve(),
        *args.ledger_root.resolve().parents,
    ):
        raise SystemExit("test ownership mode requires temporary root")
    group = grp.getgrgid(os.getegid()).gr_name if test else "trustforge-release"
    return {
        "root_uid": os.geteuid() if test else 0,
        "group": group,
        "operator_uid": os.geteuid() if test else pwd.getpwnam("trustforge-operator").pw_uid,
        "receipt_uid": os.geteuid() if test else pwd.getpwnam("trustforge-receipt").pw_uid,
        "router_uid": os.geteuid() if test else pwd.getpwnam("trustforge-router").pw_uid,
    }


def _ledger(
    args,
    *,
    directory: str,
    public: dict[str, bytes],
    domain: str,
    kinds: frozenset[str],
    role: str,
    owner_uid: int,
    signer: tuple[str, bytes] | None,
) -> SignedEventLedger:
    identity = _identity(args)
    test = args.test_owner_current_user
    key_id, private = signer or (None, None)
    return SignedEventLedger(
        directory=args.ledger_root / directory,
        verification_keys=public,
        event_permissions={domain: kinds},
        domain_keys={domain: frozenset(public)},
        signing_key_id=key_id,
        signing_private_key=private,
        signing_domain=domain if signer else None,
        ledger_role=role,
        coordination_root=args.ledger_root,
        coordination_lock_path=args.ledger_root / "coordination.lock",
        coordination_lock_mode=0o600 if test else 0o660,
        coordination_lock_owner_uid=identity["root_uid"],
        coordination_lock_group=identity["group"],
        root_owner_uid=identity["root_uid"],
        root_group=identity["group"],
        root_mode=0o750,
        directory_owner_uid=owner_uid,
        directory_group=identity["group"],
        directory_mode=0o700 if test else 0o750,
        file_mode=0o600 if test else 0o640,
    )


def _compose(args) -> tuple[VerifiedReceiptReleaseGate, dict[str, Any]]:
    config = read_protected_json(args.config, private=False)
    expected = {
        "target",
        "target_confirmation",
        "active",
        "candidate",
        "routing_policy",
        "evidence_bundle_digest",
        "stop_after_errors",
        "require_distributed_lock",
        "expected_git_sha",
        "expected_policy_digest",
        "expected_dataset_digest",
        "release_manifest_digest",
    }
    if not isinstance(config, dict) or set(config) != expected:
        raise SystemExit("release gate config contract is invalid")
    manifest_raw = read_protected_json(args.release_manifest, private=False)
    manifest = _construct(ReleaseManifest, manifest_raw, "release manifest")
    if (
        _digest(args.release_manifest) != config["release_manifest_digest"]
        or manifest.git_sha != config["expected_git_sha"]
        or manifest.artifact_digest != config["candidate"]["release_digest"]
    ):
        raise SystemExit("immutable release manifest binding is invalid")
    receipt_public = read_public_keyring(args.receipt_public_keyring)
    ceo_public = read_public_keyring(args.ceo_public_keyring)
    completion_public = read_public_keyring(args.completion_public_keyring)
    operator_public = read_public_keyring(args.operator_public_keyring)
    outcome_public = read_public_keyring(args.outcome_public_keyring)
    if args.command == "run":
        control_key_id, control_private, control_public = read_private_keyring(
            _required(args.control_keyring, "--control-keyring")
        )
    else:
        control_key_id = control_private = None
        control_public = read_public_keyring(
            _required(args.control_public_keyring, "--control-public-keyring")
        )
    if args.command in {"run", "reconcile"}:
        audit_key_id, audit_private, audit_public = read_private_keyring(
            _required(args.audit_keyring, "--audit-keyring")
        )
    else:
        audit_key_id = audit_private = None
        audit_public = read_public_keyring(
            _required(args.audit_public_keyring, "--audit-public-keyring")
        )
    identity = _identity(args)
    control_ledger = _ledger(
        args,
        directory="control",
        public=control_public,
        domain=CONTROL_DOMAIN,
        kinds=frozenset(
            {
                "deployment_initialized",
                "operator_stop",
                "activation_prepared",
                "activation_completed",
                "activation_failed",
            }
        ),
        role="release-control",
        owner_uid=identity["operator_uid"],
        signer=(
            (control_key_id, control_private)
            if control_key_id is not None and control_private is not None
            else None
        ),
    )
    outcome_ledger = _ledger(
        args,
        directory="router-outcomes",
        public=outcome_public,
        domain=OUTCOME_DOMAIN,
        kinds=frozenset(
            {"candidate_reservation", "candidate_result", "router_emergency_stop"}
        ),
        role="release-router-outcomes",
        owner_uid=identity["router_uid"],
        signer=None,
    )
    receipt_ledger = _ledger(
        args,
        directory="intrinsic-promotion-receipts",
        public=receipt_public,
        domain=SIGNER_DOMAIN,
        kinds=frozenset({EVENT_KIND, FAILURE_EVENT_KIND}),
        role="intrinsic-promotion-receipts",
        owner_uid=identity["receipt_uid"],
        signer=None,
    )
    audit_ledger = _ledger(
        args,
        directory="verified-release-gate-audit",
        public=audit_public,
        domain=AUDIT_DOMAIN,
        kinds=frozenset({AUDIT_INTENT_KIND, AUDIT_OUTCOME_KIND}),
        role="verified-receipt-release-gate",
        owner_uid=identity["operator_uid"],
        signer=(
            (audit_key_id, audit_private)
            if audit_key_id is not None and audit_private is not None
            else None
        ),
    )
    active = _construct(ReleaseEndpoint, config["active"], "active endpoint")
    candidate = _construct(ReleaseEndpoint, config["candidate"], "candidate endpoint")
    policy = _construct(RoutingPolicy, config["routing_policy"], "routing policy")
    control = DeploymentControlLedger(
        control_ledger,
        outcome_ledger=outcome_ledger,
        authorization_keys=operator_public,
        completion_keys=completion_public,
        target=config["target"],
        target_confirmation=config["target_confirmation"],
        active=active,
        candidate=candidate,
        policy=policy,
        evidence_bundle_digest=config["evidence_bundle_digest"],
        stop_after_errors=config["stop_after_errors"],
        require_distributed_lock=config["require_distributed_lock"],
        clock=lambda: datetime.now(timezone.utc),
    )
    gate = VerifiedReceiptReleaseGate(
        receipt_ledger=receipt_ledger,
        audit_ledger=audit_ledger,
        deployment_control=control,
        ceo_keys=ceo_public,
        expected_git_sha=config["expected_git_sha"],
        expected_policy_digest=config["expected_policy_digest"],
        expected_dataset_digest=config["expected_dataset_digest"],
        expected_release_manifest_digest=config["release_manifest_digest"],
    )
    return gate, config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--receipt-public-keyring", type=Path, required=True)
    parser.add_argument("--ceo-public-keyring", type=Path, required=True)
    parser.add_argument("--completion-public-keyring", type=Path, required=True)
    parser.add_argument("--operator-public-keyring", type=Path, required=True)
    parser.add_argument("--outcome-public-keyring", type=Path, required=True)
    parser.add_argument("--control-keyring", type=Path)
    parser.add_argument("--control-public-keyring", type=Path)
    parser.add_argument("--audit-keyring", type=Path)
    parser.add_argument("--audit-public-keyring", type=Path)
    parser.add_argument("--test-owner-current-user", action="store_true", help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--operator-authorization", type=Path, required=True)
    run.add_argument("--ceo-authorization", type=Path, required=True)
    subparsers.add_parser("reconcile")
    subparsers.add_parser("verify-audit")
    args = parser.parse_args()
    gate, _ = _compose(args)
    if args.command == "run":
        operator = _construct(
            DeploymentAuthorization,
            read_protected_json(args.operator_authorization, private=False),
            "operator authorization",
        )
        ceo = _construct(
            VerifiedReceiptCEOAuthorization,
            read_protected_json(args.ceo_authorization, private=False),
            "CEO authorization",
        )
        result = gate.start_canary(
            ceo_authorization=ceo,
            operator_authorization=operator,
            now=datetime.now(timezone.utc),
        )
        print(json.dumps({"transaction_id": result["event"]["transaction_id"], "status": "prepared"}, sort_keys=True))
    elif args.command == "reconcile":
        print(json.dumps({"appended": len(gate.reconcile_audit())}, sort_keys=True))
    else:
        print(json.dumps({"records": len(gate.verify_audit())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
