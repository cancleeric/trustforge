import hashlib

import pytest

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


# --- Capability component: every fail-closed branch (#503 acceptance) ----------


def test_capability_not_a_list_is_unverified():
    observation = _verified_observation()
    observation["capabilities"] = "health"

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "unverified"
    assert report["components"]["capability"]["reason"] == "capabilities_missing"


def test_capability_missing_required_is_unverified_and_names_it():
    observation = _verified_observation()
    observation["capabilities"] = ["health", "list_models"]  # get_model_path missing

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "unverified"
    assert report["components"]["capability"]["reason"] == "capability_missing:get_model_path"


@pytest.mark.parametrize("forbidden", ["upload_artifact", "trigger_retrain", "mutate_registry", "rotate_secret"])
def test_state_changing_capability_disables_readonly_probe(forbidden):
    observation = _verified_observation()
    observation["capabilities"] = ["health", "list_models", "get_model_path", forbidden]

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "disabled"
    assert report["components"]["capability"]["reason"] == "state_changing_capability_in_readonly_probe"


# --- Identity / tenant scope component (#503 acceptance) ----------------------


def test_product_scope_mismatch_disables_probe():
    observation = _verified_observation()
    observation["identity"]["product"] = "other-product"

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "disabled"
    assert report["components"]["identity"]["reason"] == "product_scope_mismatch"


def test_identity_not_a_dict_is_unverified():
    observation = _verified_observation()
    observation["identity"] = "tenant-a"

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "unverified"
    assert report["components"]["identity"]["reason"] == "identity_missing"


# --- Read access / negative checks component (#503 acceptance) ----------------


def test_negative_read_checks_not_a_dict_is_unverified():
    observation = _verified_observation()
    observation["negative_read_checks"] = ["blocked"]

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "unverified"
    assert report["components"]["read_access"]["reason"] == "negative_read_checks_missing"


def test_negative_read_checks_partial_false_disables_probe():
    observation = _verified_observation()
    observation["negative_read_checks"] = {
        "other_tenant_blocked": True,
        "other_artifact_blocked": False,
    }

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "disabled"
    assert report["components"]["read_access"]["reason"] == "unauthorized_read_succeeded"


def test_negative_read_checks_neither_confirmed_is_unverified():
    observation = _verified_observation()
    observation["negative_read_checks"] = {}  # no True, no False

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "unverified"
    assert report["components"]["read_access"]["reason"] == "negative_read_check_incomplete"


# --- Artifact identity / provenance component (#503 acceptance) ---------------


def test_artifact_not_a_dict_is_unverified():
    observation = _verified_observation()
    observation["artifact"] = "artifact-123"

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "unverified"
    assert report["components"]["artifact"]["reason"] == "artifact_identity_missing"


def test_artifact_id_mismatch_disables_probe():
    observation = _verified_observation()
    observation["artifact"]["artifact_id"] = "artifact-other"

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "disabled"
    assert report["components"]["artifact"]["reason"] == "artifact_identity_mismatch"


def test_artifact_checksum_payload_not_bytes_disables_probe():
    observation = _verified_observation()
    observation["artifact"]["checksum_payload"] = "model bytes"  # str, not bytes

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "disabled"
    assert report["components"]["artifact"]["reason"] == "artifact_checksum_payload_invalid"


def test_artifact_checksum_recalculation_mismatch_disables_probe():
    observation = _verified_observation()
    observation["artifact"]["checksum_payload"] = b"different bytes than sha256"

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "disabled"
    assert report["components"]["artifact"]["reason"] == "artifact_checksum_recalculation_mismatch"


def test_provenance_not_a_dict_is_unverified():
    observation = _verified_observation()
    observation["provenance"] = "prov-123"

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "unverified"
    assert report["components"]["provenance"]["reason"] == "provenance_missing"


def test_provenance_identity_mismatch_disables_probe():
    observation = _verified_observation()
    observation["provenance"]["id"] = "prov-other"

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "disabled"
    assert report["components"]["provenance"]["reason"] == "provenance_identity_mismatch"


def test_provenance_present_but_not_verified_stays_unverified():
    observation = _verified_observation()
    observation["provenance"]["verified"] = False

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "unverified"
    assert report["components"]["provenance"]["reason"] == "provenance_not_verified"


# --- Aggregate status precedence (#503 acceptance) ----------------------------


def test_disabled_takes_precedence_over_unverified_in_aggregate():
    """One disabled component must drag the aggregate to disabled, not unverified."""
    observation = _verified_observation()
    observation["negative_read_checks"]["other_artifact_blocked"] = False  # disabled
    del observation["provenance"]  # unverified

    report = evaluate_modelhub_readonly_probe(observation, REQUIREMENT)

    assert report["status"] == "disabled"


def test_empty_observation_is_unverified_not_disabled():
    """No evidence at all is unverified (cannot prove anything), not disabled."""
    report = evaluate_modelhub_readonly_probe({}, REQUIREMENT)

    assert report["status"] == "unverified"
    assert report["read_only"] is True
    assert report["write_operations"] == []
