"""Read-only ModelHub verification contract.

The probe result is intentionally evidence-shaped.  It does not call ModelHub
or expose credentials; callers feed it observations gathered through read-only
checks and receive a fail-closed readiness decision.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

ProbeStatus = Literal["verified", "unverified", "disabled"]


@dataclass(frozen=True)
class ProbeRequirement:
    """Expected scope and artifact identity for a read-only ModelHub probe."""

    tenant_id: str
    product: str
    model_name: str
    artifact_id: str
    artifact_sha256: str
    provenance_id: str


def evaluate_modelhub_readonly_probe(
    observation: dict[str, Any], requirement: ProbeRequirement
) -> dict[str, Any]:
    """Return a fail-closed read-only readiness report for ModelHub.

    The evaluator accepts already-collected observations so tests and review can
    prove the rules without uploading artifacts, mutating registry state, or
    requiring new credentials.
    """

    components = {
        "health": _health_component(observation),
        "capability": _capability_component(observation),
        "identity": _identity_component(observation, requirement),
        "read_access": _read_access_component(observation),
        "artifact": _artifact_component(observation, requirement),
        "provenance": _provenance_component(observation, requirement),
    }
    mutation_component = _mutation_component(observation)
    if mutation_component["status"] == "disabled":
        components["mutation_guard"] = mutation_component

    if _health_only(observation):
        components["health"] = _component(
            "unverified",
            "health_ok_without_scope_artifact_or_negative_access_evidence",
        )

    status: ProbeStatus = "verified"
    if any(component["status"] == "disabled" for component in components.values()):
        status = "disabled"
    elif any(component["status"] != "verified" for component in components.values()):
        status = "unverified"

    return {
        "kind": "modelhub_readonly_probe.v1",
        "status": status,
        "read_only": True,
        "write_operations": [],
        "components": components,
    }


def _component(status: ProbeStatus, reason: str) -> dict[str, str]:
    return {"status": status, "reason": reason}


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _health_component(observation: dict[str, Any]) -> dict[str, str]:
    if observation.get("timeout") is True:
        return _component("disabled", "modelhub_timeout")
    if observation.get("unavailable") is True:
        return _component("disabled", "modelhub_unavailable")
    if observation.get("health_ok") is True:
        return _component("verified", "health_endpoint_reachable")
    return _component("unverified", "health_not_verified")


def _capability_component(observation: dict[str, Any]) -> dict[str, str]:
    capabilities = observation.get("capabilities")
    required = {"health", "list_models", "get_model_path"}
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        return _component("unverified", "capabilities_missing")
    missing = sorted(required.difference(capabilities))
    if missing:
        return _component("unverified", "capability_missing:" + ",".join(missing))
    forbidden = {"upload_artifact", "trigger_retrain", "mutate_registry", "rotate_secret"}
    if forbidden.intersection(capabilities):
        return _component("disabled", "state_changing_capability_in_readonly_probe")
    return _component("verified", "readonly_capabilities_verified")


def _identity_component(observation: dict[str, Any], requirement: ProbeRequirement) -> dict[str, str]:
    identity = observation.get("identity")
    if not isinstance(identity, dict):
        return _component("unverified", "identity_missing")
    if identity.get("tenant_id") != requirement.tenant_id:
        return _component("disabled", "tenant_scope_mismatch")
    if identity.get("product") != requirement.product:
        return _component("disabled", "product_scope_mismatch")
    return _component("verified", "tenant_and_product_scope_verified")


def _read_access_component(observation: dict[str, Any]) -> dict[str, str]:
    checks = observation.get("negative_read_checks")
    if not isinstance(checks, dict):
        return _component("unverified", "negative_read_checks_missing")
    required = ("other_tenant_blocked", "other_artifact_blocked")
    if all(checks.get(key) is True for key in required):
        return _component("verified", "unauthorized_tenant_and_artifact_reads_blocked")
    if any(checks.get(key) is False for key in required):
        return _component("disabled", "unauthorized_read_succeeded")
    return _component("unverified", "negative_read_check_incomplete")


def _artifact_component(observation: dict[str, Any], requirement: ProbeRequirement) -> dict[str, str]:
    artifact = observation.get("artifact")
    if not isinstance(artifact, dict):
        return _component("unverified", "artifact_identity_missing")
    if artifact.get("artifact_id") != requirement.artifact_id:
        return _component("disabled", "artifact_identity_mismatch")
    observed_sha = artifact.get("sha256")
    if observed_sha != requirement.artifact_sha256:
        return _component("disabled", "artifact_checksum_mismatch")
    checksum_payload = artifact.get("checksum_payload")
    if checksum_payload is not None:
        if not isinstance(checksum_payload, (bytes, bytearray)):
            return _component("disabled", "artifact_checksum_payload_invalid")
        calculated = hashlib.sha256(bytes(checksum_payload)).hexdigest()
        if calculated != requirement.artifact_sha256:
            return _component("disabled", "artifact_checksum_recalculation_mismatch")
    return _component("verified", "artifact_identity_and_checksum_verified")


def _provenance_component(observation: dict[str, Any], requirement: ProbeRequirement) -> dict[str, str]:
    provenance = observation.get("provenance")
    if not isinstance(provenance, dict):
        return _component("unverified", "provenance_missing")
    if provenance.get("id") != requirement.provenance_id:
        return _component("disabled", "provenance_identity_mismatch")
    if provenance.get("verified") is not True:
        return _component("unverified", "provenance_not_verified")
    return _component("verified", "provenance_verified")


def _mutation_component(observation: dict[str, Any]) -> dict[str, str]:
    attempted = observation.get("mutations_attempted", [])
    if attempted:
        return _component("disabled", "read_only_probe_attempted_state_change")
    return _component("verified", "no_state_changing_operation_attempted")


def _health_only(observation: dict[str, Any]) -> bool:
    if observation.get("health_ok") is not True:
        return False
    evidence_keys = {"capabilities", "identity", "negative_read_checks", "artifact", "provenance"}
    return not any(key in observation for key in evidence_keys)
