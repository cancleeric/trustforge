from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs/contracts/commercial-onchain-evidence-contract-v1.json"
FIXTURE_PATH = ROOT / "tests/fixtures/commercial/onchain_risk_evidence.json"
RFC3339_FORMAT_CHECKER = FormatChecker()


@RFC3339_FORMAT_CHECKER.checks("date-time", raises=ValueError)
def _is_calendar_valid_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    datetime.fromisoformat(normalized)
    return True


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@pytest.fixture(scope="module")
def evidence_contract_validator() -> Draft202012Validator:
    schema = json.loads(CONTRACT_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=RFC3339_FORMAT_CHECKER)


def test_commercial_onchain_fixture_evidence_matches_contract(
    evidence_contract_validator: Draft202012Validator,
) -> None:
    fixtures = json.loads(FIXTURE_PATH.read_text())

    assert {fixture["evidence"]["source_state"] for fixture in fixtures} == {
        "ready",
        "credential-gated",
        "archive-required",
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

    for field in (
        "content_hash",
        "raw_payload_reference",
        "license_or_terms",
        "asset_scope",
    ):
        invalid = dict(evidence)
        invalid.pop(field)
        with pytest.raises(ValidationError):
            evidence_contract_validator.validate(invalid)


def test_commercial_onchain_contract_rejects_incomplete_https_source_url(
    evidence_contract_validator: Draft202012Validator,
) -> None:
    evidence = json.loads(FIXTURE_PATH.read_text())[0]["evidence"]
    invalid = dict(evidence)
    invalid["source_url"] = "https://"

    with pytest.raises(ValidationError):
        evidence_contract_validator.validate(invalid)


@pytest.mark.parametrize(
    "source_state",
    (
        "credential-gated",
        "archive-required",
        "blocked",
    ),
)
def test_commercial_onchain_contract_requires_state_reason_for_non_ready_sources(
    evidence_contract_validator: Draft202012Validator,
    source_state: str,
) -> None:
    evidence = json.loads(FIXTURE_PATH.read_text())[0]["evidence"]
    invalid = dict(evidence)
    invalid["source_state"] = source_state
    invalid.pop("state_reason", None)

    with pytest.raises(ValidationError):
        evidence_contract_validator.validate(invalid)


def test_commercial_onchain_contract_allows_ready_source_without_state_reason(
    evidence_contract_validator: Draft202012Validator,
) -> None:
    evidence = json.loads(FIXTURE_PATH.read_text())[0]["evidence"]
    valid = dict(evidence)
    valid["source_state"] = "ready"
    valid.pop("state_reason", None)

    evidence_contract_validator.validate(valid)


def test_commercial_onchain_contract_rejects_invalid_source_state(
    evidence_contract_validator: Draft202012Validator,
) -> None:
    evidence = json.loads(FIXTURE_PATH.read_text())[0]["evidence"]
    invalid = {**evidence, "source_state": "live-but-unreviewed"}

    with pytest.raises(ValidationError):
        evidence_contract_validator.validate(invalid)


@pytest.mark.parametrize("timestamp_field", ("published_at", "retrieved_at"))
@pytest.mark.parametrize(
    "invalid_timestamp",
    (
        "not-a-date-time",
        "2026-13-99T99:99:99Z",
        "2026-02-30T00:00:00Z",
    ),
)
def test_commercial_onchain_contract_rejects_invalid_lineage_timestamps(
    evidence_contract_validator: Draft202012Validator,
    timestamp_field: str,
    invalid_timestamp: str,
) -> None:
    evidence = json.loads(FIXTURE_PATH.read_text())[0]["evidence"]
    invalid = {**evidence, timestamp_field: invalid_timestamp}

    with pytest.raises(ValidationError):
        evidence_contract_validator.validate(invalid)
