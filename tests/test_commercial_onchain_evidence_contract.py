from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs/contracts/commercial-onchain-evidence-contract-v1.json"
FIXTURE_PATH = ROOT / "tests/fixtures/commercial/onchain_risk_evidence.json"


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@pytest.fixture(scope="module")
def evidence_contract_validator() -> Draft202012Validator:
    schema = json.loads(CONTRACT_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_commercial_onchain_fixture_evidence_matches_contract(
    evidence_contract_validator: Draft202012Validator,
) -> None:
    fixtures = json.loads(FIXTURE_PATH.read_text())

    assert {fixture["evidence"]["source_state"] for fixture in fixtures} == {
        "ready",
        "credential-gated",
        "blocked",
    }
    for fixture in fixtures:
        evidence_contract_validator.validate(fixture["evidence"])


def test_commercial_onchain_content_hashes_are_deterministic() -> None:
    fixtures = json.loads(FIXTURE_PATH.read_text())

    for fixture in fixtures:
        assert fixture["evidence"]["content_hash"] == _canonical_hash(fixture["payload"])


def test_commercial_onchain_contract_rejects_missing_lineage_fields(
    evidence_contract_validator: Draft202012Validator,
) -> None:
    evidence = json.loads(FIXTURE_PATH.read_text())[0]["evidence"]

    for field in ("content_hash", "raw_payload_reference", "license_or_terms"):
        invalid = dict(evidence)
        invalid.pop(field)
        with pytest.raises(ValidationError):
            evidence_contract_validator.validate(invalid)


def test_commercial_onchain_contract_rejects_invalid_source_state(
    evidence_contract_validator: Draft202012Validator,
) -> None:
    evidence = json.loads(FIXTURE_PATH.read_text())[0]["evidence"]
    invalid = {**evidence, "source_state": "live-but-unreviewed"}

    with pytest.raises(ValidationError):
        evidence_contract_validator.validate(invalid)
