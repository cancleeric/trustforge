import hashlib

from trustforge.modelhub_readonly_probe import ProbeRequirement, evaluate_modelhub_readonly_probe


PAYLOAD = b"model bytes"
SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
REQUIREMENT = ProbeRequirement(
    tenant_id="tenant-a",
    product="trustforge",
    model_name="calibrator",
    artifact_id="artifact-123",
    artifact_sha256=SHA256,
    provenance_id="prov-123",
)


def _verified_observation():
    return {
        "health_ok": True,
        "capabilities": ["health", "list_models", "get_model_path"],
        "identity": {"tenant_id": "tenant-a", "product": "trustforge"},
        "negative_read_checks": {"other_tenant_blocked": True, "other_artifact_blocked": True},
        "artifact": {"artifact_id": "artifact-123", "sha256": SHA256, "checksum_payload": PAYLOAD},
        "provenance": {"id": "prov-123", "verified": True},
    }


def test_modelhub_readonly_probe_verifies_complete_read_evidence_without_writes():
    report = evaluate_modelhub_readonly_probe(_verified_observation(), REQUIREMENT)

    assert report["status"] == "verified"
    assert report["read_only"] is True
    assert report["write_operations"] == []
    assert {component["status"] for component in report["components"].values()} == {"verified"}


def test_health_200_alone_is_not_production_health_guarantee():
    report = evaluate_modelhub_readonly_probe({"health_ok": True}, REQUIREMENT)

    assert report["status"] == "unverified"
    assert report["components"]["health"]["reason"] == (
        "health_ok_without_scope_artifact_or_negative_access_evidence"
    )


def test_tenant_or_artifact_unauthorized_read_success_disables_probe():
    observation = _verified_observation()
    observation["negative_read_checks"]["other_tenant_blocked"] = False

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "disabled"
    assert report["components"]["read_access"] == {
        "status": "disabled",
        "reason": "unauthorized_read_succeeded",
    }


def test_timeout_unavailable_and_checksum_mismatch_fail_closed():
    timeout = evaluate_modelhub_readonly_probe({"timeout": True}, REQUIREMENT)
    unavailable = evaluate_modelhub_readonly_probe({"unavailable": True}, REQUIREMENT)
    checksum = _verified_observation()
    checksum["artifact"]["sha256"] = "0" * 64

    mismatch = evaluate_modelhub_readonly_probe(checksum, REQUIREMENT)

    assert timeout["status"] == "disabled"
    assert unavailable["status"] == "disabled"
    assert mismatch["status"] == "disabled"
    assert mismatch["components"]["artifact"]["reason"] == "artifact_checksum_mismatch"


def test_missing_proof_stays_unverified_and_state_change_attempt_disables():
    missing = _verified_observation()
    del missing["provenance"]
    mutating = _verified_observation()
    mutating["mutations_attempted"] = ["trigger_retrain"]

    unverified = evaluate_modelhub_readonly_probe(missing, REQUIREMENT)
    disabled = evaluate_modelhub_readonly_probe(mutating, REQUIREMENT)

    assert unverified["status"] == "unverified"
    assert unverified["components"]["provenance"]["reason"] == "provenance_missing"
    assert disabled["status"] == "disabled"
    assert disabled["components"]["mutation_guard"]["reason"] == "read_only_probe_attempted_state_change"
