"""Least-privilege construction of the long-lived release router."""
from __future__ import annotations

import json
import os
from pathlib import Path

from trustforge.authenticated_ledger import AuthenticatedLedger
from trustforge.deployment_control import DeploymentControlLedger
from trustforge.release_router import ReleaseABRouter, ReleaseEndpoint, RoutingPolicy
from trustforge.safe_fs import read_regular_file

RUNTIME_CONFIG_PATH = Path("/etc/trustforge/release-router-runtime.json")
RUNTIME_KEYS_PATH = Path("/etc/trustforge/release-router-runtime-keys.json")


class RouterRuntimeError(RuntimeError):
    pass


def _protected(path: Path) -> dict:
    raw, info = read_regular_file(path, maximum_bytes=128 * 1024)
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RouterRuntimeError("runtime input ownership or mode is unsafe")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RouterRuntimeError("runtime input is invalid") from exc
    if not isinstance(value, dict):
        raise RouterRuntimeError("runtime input must be an object")
    return value


def _keys(payload: dict, role: str) -> dict[str, bytes]:
    value = payload.get(role)
    if not isinstance(value, dict):
        raise RouterRuntimeError(f"runtime {role} keys are absent")
    try:
        decoded = {key: bytes.fromhex(item) for key, item in value.items()}
    except (TypeError, ValueError) as exc:
        raise RouterRuntimeError(f"runtime {role} keys are invalid") from exc
    if any(len(item) != 32 for item in decoded.values()):
        raise RouterRuntimeError(f"runtime {role} keys must be 32 bytes")
    return decoded


def build_runtime_router() -> ReleaseABRouter:
    """Load only ledger append, routing, and manifest verification material."""
    config = _protected(RUNTIME_CONFIG_PATH)
    key_file = _protected(RUNTIME_KEYS_PATH)
    if set(key_file) != {"ledger", "routing", "endpoint_manifest_public"}:
        raise RouterRuntimeError("runtime key roles are over-privileged or incomplete")
    ledger_keys = _keys(key_file, "ledger")
    routing_keys = _keys(key_file, "routing")
    public_keys = _keys(key_file, "endpoint_manifest_public")
    ledger = AuthenticatedLedger(
        keyring=ledger_keys,
        active_key_id=config["active_ledger_key_id"],
    )
    records = ledger.read()
    if not records or records[0]["event"].get("kind") != "deployment_initialized":
        raise RouterRuntimeError("deployment ledger is not initialized")
    initialized = records[0]["event"]
    active = ReleaseEndpoint(**initialized["active"])
    candidate = ReleaseEndpoint(**initialized["candidate"])
    policy = RoutingPolicy(**initialized["policy"])
    control = DeploymentControlLedger(
        ledger,
        authorization_keys={},
        completion_keys={},
        target=initialized["target"],
        target_confirmation=initialized["target_confirmation"],
        active=active,
        candidate=candidate,
        policy=policy,
        evidence_bundle_digest=initialized["evidence_bundle_digest"],
        stop_after_errors=int(initialized["stop_after_errors"]),
        require_distributed_lock=True,
    )
    if policy.routing_key_id not in routing_keys:
        raise RouterRuntimeError("active routing key is absent")
    return ReleaseABRouter(
        control,
        routing_keys,
        pinned_a_fallback=active,
        manifest_keyring=public_keys,
    )
