#!/usr/bin/env python3
"""Operator interface for #733 control evidence; never deploys or cuts traffic."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from trustforge.agent.shadow_contracts import ShadowReleaseIdentity
from trustforge.authenticated_ledger import AuthenticatedLedger
from trustforge.deployment_control import (
    ActivationCompletionReceipt,
    DeploymentAuthorization,
    DeploymentControlError,
    DeploymentControlLedger,
)
from trustforge.deployment_evidence import (
    EvidenceError,
    evidence_bundle_digest,
    snapshot_artifact,
    verify_gate_receipts,
    verify_shadow_health_export,
)
from trustforge.release_router import ReleaseEndpoint, RoutingPolicy
from trustforge.safe_fs import read_regular_file

CONFIG_PATH = Path("/etc/trustforge/deployment-control.json")
KEYRING_PATH = Path("/etc/trustforge/deployment-keyrings.json")
LEDGER_PATH = Path("/var/lib/trustforge/deployment-control")


def _protected_json(path: Path, maximum_bytes: int = 1_000_000) -> dict:
    raw, info = read_regular_file(path, maximum_bytes=maximum_bytes)
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise DeploymentControlError("protected input ownership or permissions are unsafe")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise DeploymentControlError("protected input must be a JSON object")
    return value


def _keyring(payload: dict, name: str) -> dict[str, bytes]:
    values = payload.get(name)
    if not isinstance(values, dict):
        raise DeploymentControlError(f"{name} keyring is unavailable")
    try:
        decoded = {key: bytes.fromhex(value) for key, value in values.items()}
    except (TypeError, ValueError) as exc:
        raise DeploymentControlError(f"{name} keyring is invalid") from exc
    if any(len(value) < 32 for value in decoded.values()):
        raise DeploymentControlError(f"{name} keyring contains a short key")
    return decoded


def _build_control(
    *,
    require_preflight: bool,
    verify_retained_a: bool,
    verify_retained_b: bool,
) -> tuple[DeploymentControlLedger, dict[str, bytes], dict[str, bytes]]:
    config = _protected_json(CONFIG_PATH)
    keys = _protected_json(KEYRING_PATH, 128 * 1024)
    ledger_keys = _keyring(keys, "ledger")
    auth_keys = _keyring(keys, "authorization")
    completion_keys = _keyring(keys, "completion")
    gate_keys = _keyring(keys, "gates")
    routing_keys = _keyring(keys, "routing")
    manifest_keys = _keyring(keys, "endpoint_manifests")
    ledger = AuthenticatedLedger(
        keyring=ledger_keys,
        active_key_id=config["active_ledger_key_id"],
    )
    if require_preflight:
        identity = ShadowReleaseIdentity(**config["shadow_identity"])
        active_snapshot = snapshot_artifact(
            config["active_artifact_path"], identity.active_artifact_digest
        )
        candidate_snapshot = snapshot_artifact(
            config["candidate_artifact_path"], identity.candidate_artifact_digest
        )
        now = datetime.now(timezone.utc)
        shadow = verify_shadow_health_export(
            config["shadow_health_path"],
            config["shadow_store_path"],
            expected_identity=identity,
            now=now,
        )
        gates = verify_gate_receipts(
            config["gate_receipt_paths"],
            active_artifact_digest=identity.active_artifact_digest,
            candidate_artifact_digest=identity.candidate_artifact_digest,
            keyring=gate_keys,
            now=now,
        )
        evidence_digest = evidence_bundle_digest(
            active=active_snapshot,
            candidate=candidate_snapshot,
            shadow=shadow,
            gates=gates,
        )
        active = ReleaseEndpoint(**config["active_endpoint"])
        candidate = ReleaseEndpoint(**config["candidate_endpoint"])
        if (
            active.release_digest != active_snapshot.digest
            or candidate.release_digest != candidate_snapshot.digest
        ):
            raise DeploymentControlError(
                "endpoint identity does not match verified artifact"
            )
        policy = RoutingPolicy(**config["routing_policy"])
        target = config["target"]
        target_confirmation = config["target_confirmation"]
        stop_after_errors = int(config["stop_after_errors"])
    else:
        records = ledger.read()
        if not records or records[0]["event"].get("kind") != "deployment_initialized":
            raise DeploymentControlError("deployment ledger is not initialized")
        initialized = records[0]["event"]
        active = ReleaseEndpoint(**initialized["active"])
        candidate = ReleaseEndpoint(**initialized["candidate"])
        policy = RoutingPolicy(**initialized["policy"])
        evidence_digest = initialized["evidence_bundle_digest"]
        target = initialized["target"]
        target_confirmation = initialized["target_confirmation"]
        stop_after_errors = int(initialized["stop_after_errors"])
        if verify_retained_a:
            snapshot_artifact(
                config["active_artifact_path"], active.release_digest
            )
        if verify_retained_b:
            snapshot_artifact(
                config["candidate_artifact_path"], candidate.release_digest
            )
    if policy.routing_key_id not in routing_keys:
        raise DeploymentControlError("routing key id is unavailable")
    control = DeploymentControlLedger(
        ledger,
        authorization_keys=auth_keys,
        completion_keys=completion_keys,
        target=target,
        target_confirmation=target_confirmation,
        active=active,
        candidate=candidate,
        policy=policy,
        evidence_bundle_digest=evidence_digest,
        stop_after_errors=stop_after_errors,
    )
    return control, routing_keys, manifest_keys


def _redacted_status(control: DeploymentControlLedger) -> dict:
    state = control.routing_snapshot()
    return {
        "status_version": "trustforge.deployment-status/v1",
        "target": control.target,
        "ledger_id": state.ledger_id,
        "ledger_head": state.ledger_head,
        "phase": state.phase,
        "desired_phase": state.desired_phase,
        "activation_status": state.activation_status,
        "active_artifact_digest": state.active.release_digest,
        "candidate_artifact_digest": state.candidate.release_digest,
        "routing_policy_digest": state.policy.policy_digest,
        "routing_key_id": state.policy.routing_key_id,
        "candidate_requests": state.candidate_requests,
        "consecutive_errors": state.consecutive_errors,
        "automatic_promotion": False,
        "production_cutover_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("initialize")
    sub.add_parser("status")
    for action in ("start", "stop", "promote", "rollback-a"):
        item = sub.add_parser(action)
        item.add_argument("--authorization", type=Path, required=True)
    complete = sub.add_parser("complete")
    complete.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        require_preflight = args.command in {"initialize", "start", "promote"}
        verify_retained_a = args.command in {"stop", "rollback-a", "complete"}
        verify_retained_b = args.command == "complete"
        control, _routing_keys, _manifest_keys = _build_control(
            require_preflight=require_preflight,
            verify_retained_a=verify_retained_a,
            verify_retained_b=verify_retained_b,
        )
        now = datetime.now(timezone.utc)
        if args.command == "initialize":
            control.initialize()
        elif args.command == "status":
            pass
        elif args.command == "complete":
            receipt = ActivationCompletionReceipt(**_protected_json(args.receipt, 32_768))
            control.complete(receipt, now=now)
        else:
            authorization = DeploymentAuthorization(
                **_protected_json(args.authorization, 32_768)
            )
            control.prepare(args.command, authorization, now=now)
        print(json.dumps(_redacted_status(control), sort_keys=True, indent=2))
        return 0
    except (
        DeploymentControlError,
        EvidenceError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": type(exc).__name__,
                    "details_redacted": True,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
