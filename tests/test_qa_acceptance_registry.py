from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "validate_qa_acceptance.py"
SPEC = importlib.util.spec_from_file_location("validate_qa_acceptance", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

AcceptanceValidationError = MODULE.AcceptanceValidationError
expanded_case_ids = MODULE.expanded_case_ids
validate_acceptance = MODULE.validate_acceptance


def _registry() -> dict:
    return json.loads((ROOT / "qa" / "requirements.json").read_text(encoding="utf-8"))


def _valid_summary(artifact_root: Path) -> dict:
    registry = _registry()
    release_id = "v1.2.3-qa"
    artifact_path = f"out/acceptance/{release_id}/reports/report.md"
    payload = b"# TrustForge\nverified report\n"
    output_path = artifact_root / artifact_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    artifact = {
        "path": artifact_path,
        "sha256": __import__("hashlib").sha256(payload).hexdigest(),
        "media_type": "text/markdown",
        "size_bytes": len(payload),
        "magic": "# TrustForge",
    }
    cases = []
    for case_id, requirement in expanded_case_ids(registry).items():
        cases.append(
            {
                "schema_version": "1.0",
                "case_id": case_id,
                "requirement_id": requirement["id"],
                "release_id": release_id,
                "hard": requirement["hard"],
                "status": "pass",
                "started_at": "2026-07-30T01:00:00Z",
                "finished_at": "2026-07-30T01:00:01Z",
                "duration_ms": 1000,
                "evidence": [copy.deepcopy(artifact)],
            }
        )
    return {
        "schema_version": "1.0",
        "release_id": release_id,
        "disposition": "deployed_not_accepted",
        "manifest": {
            "schema_version": "1.0",
            "release_id": release_id,
            "git_sha": "b" * 40,
            "frontend_version": "1.2.3",
            "backend_version": "1.2.3",
            "artifacts": [artifact],
        },
        "cases": cases,
    }


def _validate(
    summary: dict,
    artifact_root: Path,
    registry: dict | None = None,
) -> None:
    validate_acceptance(
        registry or _registry(),
        summary,
        ROOT / "qa" / "schemas",
        artifact_root,
    )


def test_registry_expands_required_competition_matrices() -> None:
    cases = expanded_case_ids(_registry())
    assert len([case for case in cases if case.startswith("CA-01[")]) == 15
    assert len([case for case in cases if case.startswith("CA-02[")]) == 25
    assert len([case for case in cases if case.startswith("CA-03[")]) == 30
    assert "UI-01[viewport=boundary-900x900]" in cases
    assert "UI-01[viewport=boundary-901x900]" in cases


def test_complete_release_bound_summary_records_planned_gates(tmp_path: Path) -> None:
    _validate(_valid_summary(tmp_path), tmp_path)


@pytest.mark.parametrize("status", ["fail", "skipped", "not_run"])
def test_failed_deployment_case_requires_failed_disposition(
    status: str, tmp_path: Path
) -> None:
    summary = _valid_summary(tmp_path)
    summary["cases"][0]["status"] = status
    with pytest.raises(AcceptanceValidationError, match="disposition must be deployment_failed"):
        _validate(summary, tmp_path)
    summary["disposition"] = "deployment_failed"
    _validate(summary, tmp_path)


def test_failed_competition_case_requires_deployed_not_accepted(tmp_path: Path) -> None:
    summary = _valid_summary(tmp_path)
    competition_case = next(
        case for case in summary["cases"] if case["requirement_id"] == "CA-01"
    )
    competition_case["status"] = "fail"
    summary["disposition"] = "production_accepted"
    with pytest.raises(
        AcceptanceValidationError,
        match="disposition must be deployed_not_accepted",
    ):
        _validate(summary, tmp_path)
    summary["disposition"] = "deployed_not_accepted"
    _validate(summary, tmp_path)


def test_missing_case_fails_closed(tmp_path: Path) -> None:
    summary = _valid_summary(tmp_path)
    summary["cases"].pop()
    with pytest.raises(AcceptanceValidationError, match="missing required cases"):
        _validate(summary, tmp_path)


def test_duplicate_and_unknown_requirement_ids_are_rejected() -> None:
    registry = _registry()
    registry["requirements"].append(copy.deepcopy(registry["requirements"][0]))
    with pytest.raises(AcceptanceValidationError, match="duplicate or invalid"):
        expanded_case_ids(registry)

    registry = _registry()
    registry["requirements"][0]["matrix"] = ["not_a_dimension"]
    with pytest.raises(AcceptanceValidationError, match="unknown dimensions"):
        expanded_case_ids(registry)


def test_hard_requirement_without_automation_is_rejected() -> None:
    registry = _registry()
    registry["requirements"][0]["automation"] = []
    with pytest.raises(AcceptanceValidationError, match="lacks automation"):
        expanded_case_ids(registry)


def test_implemented_automation_path_must_exist() -> None:
    registry = _registry()
    registry["requirements"][0]["automation"] = ["scripts/does-not-exist.py"]
    with pytest.raises(AcceptanceValidationError, match="implemented automation missing"):
        expanded_case_ids(registry, ROOT)


def test_release_binding_and_artifact_boundary_are_enforced(tmp_path: Path) -> None:
    summary = _valid_summary(tmp_path)
    summary["manifest"]["release_id"] = "another-release"
    with pytest.raises(AcceptanceValidationError, match="does not match"):
        _validate(summary, tmp_path)

    summary = _valid_summary(tmp_path)
    summary["manifest"]["artifacts"][0]["path"] = "../report.md"
    with pytest.raises(AcceptanceValidationError, match="must be below"):
        _validate(summary, tmp_path)


def test_symlinked_artifact_fails_closed(tmp_path: Path) -> None:
    summary = _valid_summary(tmp_path)
    artifact = summary["manifest"]["artifacts"][0]
    path = tmp_path / artifact["path"]
    outside = tmp_path / "outside.md"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)

    with pytest.raises(AcceptanceValidationError, match="contains symlink"):
        _validate(summary, tmp_path)


def test_evidence_must_exist_in_manifest(tmp_path: Path) -> None:
    summary = _valid_summary(tmp_path)
    summary["cases"][0]["evidence"][0]["path"] = (
        f"out/acceptance/{summary['release_id']}/reports/other.md"
    )
    with pytest.raises(AcceptanceValidationError, match="absent from manifest"):
        _validate(summary, tmp_path)


def test_case_requires_evidence_and_metadata_matches_manifest(tmp_path: Path) -> None:
    summary = _valid_summary(tmp_path)
    summary["cases"][0]["evidence"] = []
    with pytest.raises(AcceptanceValidationError, match="schema error"):
        _validate(summary, tmp_path)

    summary = _valid_summary(tmp_path)
    summary["cases"][0]["evidence"][0]["size_bytes"] += 1
    with pytest.raises(AcceptanceValidationError, match="metadata mismatch"):
        _validate(summary, tmp_path)


def test_manifest_hash_and_magic_must_match_real_artifact(tmp_path: Path) -> None:
    summary = _valid_summary(tmp_path)
    summary["manifest"]["artifacts"][0]["sha256"] = "0" * 64
    with pytest.raises(AcceptanceValidationError, match="sha256 mismatch"):
        _validate(summary, tmp_path)

    summary = _valid_summary(tmp_path)
    summary["manifest"]["artifacts"][0]["magic"] = "%PDF"
    with pytest.raises(AcceptanceValidationError, match="magic mismatch"):
        _validate(summary, tmp_path)


def test_secret_bearing_fields_are_rejected(tmp_path: Path) -> None:
    summary = _valid_summary(tmp_path)
    summary["cases"][0]["metrics"] = {"authorization_header": "redacted"}
    with pytest.raises(AcceptanceValidationError, match="secret-bearing field"):
        _validate(summary, tmp_path)


def test_invalid_schema_version_is_rejected(tmp_path: Path) -> None:
    summary = _valid_summary(tmp_path)
    summary["schema_version"] = "2.0"
    with pytest.raises(AcceptanceValidationError, match="schema error"):
        _validate(summary, tmp_path)


def test_duplicate_case_result_is_rejected(tmp_path: Path) -> None:
    summary = _valid_summary(tmp_path)
    summary["cases"].append(copy.deepcopy(summary["cases"][0]))
    with pytest.raises(AcceptanceValidationError, match="duplicate result"):
        _validate(summary, tmp_path)
