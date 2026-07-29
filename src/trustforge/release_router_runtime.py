"""Least-privilege construction of the long-lived release router."""

from __future__ import annotations

import json
import pwd
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from trustforge.deployment_control import DeploymentControlLedger
from trustforge.release_router import (
    ReleaseABRouter,
    ReleaseEndpoint,
    RoutedResponse,
    RoutingPolicy,
)
from trustforge.safe_fs import read_regular_file
from trustforge.signed_event_ledger import SECURITY_LEDGER_ROOT, SignedEventLedger

RUNTIME_CONFIG_PATH = Path("/etc/trustforge/release-router-runtime.json")
RUNTIME_KEYS_PATH = Path("/etc/trustforge/release-router-runtime-keys.json")
COORDINATION_LOCK_PATH = Path("/run/trustforge-release-control/coordination.lock")
OUTCOME_EVENT_KINDS = frozenset(
    {
        "candidate_reservation",
        "candidate_result",
        "candidate_cost_reconciliation",
        "router_emergency_stop",
    }
)


class RouterRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RouterRuntimeKeyMaterial:
    control_event_public: Mapping[str, bytes]
    router_outcome_public: Mapping[str, bytes]
    router_outcome_private: Mapping[str, bytes]
    routing: Mapping[str, bytes]
    endpoint_manifest_public: Mapping[str, bytes]
    authorization_public: Mapping[str, bytes]
    completion_public: Mapping[str, bytes]
    canary_cost_budget_public: Mapping[str, bytes]


def _protected(path: Path) -> dict:
    raw, info = read_regular_file(path, maximum_bytes=128 * 1024)
    if info.st_uid != 0 or info.st_mode & 0o077:
        raise RouterRuntimeError("runtime input ownership or mode is unsafe")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RouterRuntimeError("runtime input is invalid") from exc
    if not isinstance(value, dict) or not value:
        raise RouterRuntimeError("runtime input must be an object")
    return value


def _provision_receipt(path: Path) -> dict:
    raw, info = read_regular_file(path, maximum_bytes=4096)
    if info.st_uid != 0 or info.st_mode & 0o777 != 0o644 or info.st_nlink != 1:
        raise RouterRuntimeError("ledger provisioning receipt metadata is unsafe")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RouterRuntimeError("ledger provisioning receipt is invalid") from exc
    if not isinstance(value, dict) or not value:
        raise RouterRuntimeError("ledger provisioning receipt must be an object")
    if (
        set(value)
        != {
            "schema",
            "control_public",
            "outcome_public",
            "outcome_event_kinds",
        }
        or value.get("schema") != "trustforge.release-ledger-provision-receipt/v2"
        or value.get("outcome_event_kinds") != sorted(OUTCOME_EVENT_KINDS)
        or any(
            not isinstance(value.get(role), dict) or not value[role]
            for role in ("control_public", "outcome_public")
        )
    ):
        raise RouterRuntimeError("release ledgers require audited permission migration")
    return value


def _keys(payload: dict, role: str) -> dict[str, bytes]:
    value = payload.get(role)
    if not isinstance(value, dict) or not value:
        raise RouterRuntimeError(f"runtime {role} keys are absent")
    try:
        decoded = {key: bytes.fromhex(item) for key, item in value.items()}
    except (TypeError, ValueError) as exc:
        raise RouterRuntimeError(f"runtime {role} keys are invalid") from exc
    if any(not key or len(item) != 32 for key, item in decoded.items()):
        raise RouterRuntimeError(f"runtime {role} keys must be 32 bytes")
    return decoded


def parse_runtime_key_material(payload: dict) -> RouterRuntimeKeyMaterial:
    roles = {
        "control_event_public",
        "router_outcome_public",
        "router_outcome_private",
        "routing",
        "endpoint_manifest_public",
        "authorization_public",
        "completion_public",
        "canary_cost_budget_public",
    }
    if set(payload) != roles:
        raise RouterRuntimeError("runtime key roles are over-privileged or incomplete")
    decoded = {
        role: MappingProxyType(_keys(payload, role))
        for role in sorted(roles)
    }
    return RouterRuntimeKeyMaterial(**decoded)


def load_runtime_key_material(
    path: Path = RUNTIME_KEYS_PATH,
) -> RouterRuntimeKeyMaterial:
    """Expose exact typed runtime keys without admitting private cost material."""
    return parse_runtime_key_material(_protected(path))


def build_runtime_router(
    response_validator: Callable[[str, RoutedResponse], None] | None = None,
) -> ReleaseABRouter:
    """Load only ledger append, routing, and manifest verification material."""
    runtime_config = _protected(RUNTIME_CONFIG_PATH)
    material = load_runtime_key_material()
    _provision_receipt(SECURITY_LEDGER_ROOT / "provision-receipt.json")
    control_public = material.control_event_public
    outcome_public = material.router_outcome_public
    outcome_private = material.router_outcome_private
    routing_keys = material.routing
    public_keys = material.endpoint_manifest_public
    authorization_public = material.authorization_public
    completion_public = material.completion_public
    operator_uid = pwd.getpwnam("trustforge-operator").pw_uid
    router_uid = pwd.getpwnam("trustforge-router").pw_uid
    ownership = {
        "root_owner_uid": 0,
        "root_group": "trustforge-release",
        "root_mode": 0o750,
        "directory_group": "trustforge-release",
        "directory_mode": 0o750,
        "file_mode": 0o640,
    }
    if len(outcome_private) != 1:
        raise RouterRuntimeError("runtime requires exactly one outcome signing key")
    outcome_key_id, outcome_secret = next(iter(outcome_private.items()))
    control_permissions = {
        "release-control": frozenset(
            {
                "deployment_initialized",
                "operator_stop",
                "activation_prepared",
                "activation_completed",
                "activation_failed",
            }
        )
    }
    outcome_permissions = {"release-router-outcome": OUTCOME_EVENT_KINDS}
    ledger = SignedEventLedger(
        directory=SECURITY_LEDGER_ROOT / "control",
        verification_keys=control_public,
        event_permissions=control_permissions,
        domain_keys={"release-control": frozenset(control_public)},
        ledger_role="release-control",
        coordination_root=SECURITY_LEDGER_ROOT,
        coordination_lock_path=COORDINATION_LOCK_PATH,
        coordination_lock_mode=0o660,
        coordination_lock_owner_uid=0,
        coordination_lock_group="trustforge-release",
        directory_owner_uid=operator_uid,
        **ownership,
    )
    outcome_ledger = SignedEventLedger(
        directory=SECURITY_LEDGER_ROOT / "router-outcomes",
        verification_keys=outcome_public,
        event_permissions=outcome_permissions,
        domain_keys={"release-router-outcome": frozenset(outcome_public)},
        signing_key_id=outcome_key_id,
        signing_private_key=outcome_secret,
        signing_domain="release-router-outcome",
        ledger_role="release-router-outcomes",
        coordination_root=SECURITY_LEDGER_ROOT,
        coordination_lock_path=COORDINATION_LOCK_PATH,
        coordination_lock_mode=0o660,
        coordination_lock_owner_uid=0,
        coordination_lock_group="trustforge-release",
        directory_owner_uid=router_uid,
        **ownership,
    )
    records = ledger.read()
    if not records or records[0]["event"].get("kind") != "deployment_initialized":
        raise RouterRuntimeError("deployment ledger is not initialized")
    initialized = records[0]["event"]
    active = ReleaseEndpoint(**initialized["active"])
    candidate = ReleaseEndpoint(**initialized["candidate"])
    policy = RoutingPolicy(**initialized["policy"])
    expected_runtime = {
        "schema": "trustforge.release-router-runtime/v1",
        "a_artifact_digest": active.release_digest,
        "b_artifact_digest": candidate.release_digest,
        "endpoint_manifest_key_ids": sorted(public_keys),
        "control_ledger_id": records[0]["ledger_id"],
        "deployment_initialized_event_hash": records[0]["event_hash"],
        "routing_policy": initialized["policy"],
        "outcome_signing_key_id": outcome_key_id,
    }
    if runtime_config != expected_runtime:
        raise RouterRuntimeError(
            "runtime identity does not match authenticated deployment initialization"
        )
    if policy.routing_key_id not in routing_keys:
        raise RouterRuntimeError("runtime routing signer does not match policy")
    control = DeploymentControlLedger(
        ledger,
        outcome_ledger=outcome_ledger,
        authorization_keys=authorization_public,
        completion_keys=completion_public,
        target=initialized["target"],
        target_confirmation=initialized["target_confirmation"],
        active=active,
        candidate=candidate,
        policy=policy,
        evidence_bundle_digest=initialized["evidence_bundle_digest"],
        stop_after_errors=int(initialized["stop_after_errors"]),
        cost_budget_keys=material.canary_cost_budget_public,
        require_distributed_lock=True,
    )
    if policy.routing_key_id not in routing_keys:
        raise RouterRuntimeError("active routing key is absent")
    return ReleaseABRouter(
        control,
        routing_keys,
        pinned_a_fallback=active,
        manifest_keyring=public_keys,
        cost_budget_keyring=material.canary_cost_budget_public,
        response_validator=response_validator,
    )
