from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from trustforge.intrinsic_official_contract import (
    PUBLIC_OFFICIAL_STATE_FIELDS,
    SENSITIVE_AUTHORITY_FIELDS,
    IntrinsicOfficialState,
    intrinsic_official_state_schema,
    validate_intrinsic_official_state,
)


def test_public_typed_contract_contains_no_authority_or_sensitive_material() -> None:
    assert PUBLIC_OFFICIAL_STATE_FIELDS.isdisjoint(SENSITIVE_AUTHORITY_FIELDS)
    value = IntrinsicOfficialState(
        state="blocked",
        capability_id=None,
        verified_at="2026-07-30T00:00:00Z",
        expires_at=None,
        release_id=None,
        reason="authority_unavailable",
    ).public_dict()
    assert set(value) == PUBLIC_OFFICIAL_STATE_FIELDS
    assert not any("key" in field or "receipt" in field for field in value)


def test_canonical_schema_accepts_blocked_state_and_matches_openapi() -> None:
    value = IntrinsicOfficialState(
        state="blocked",
        capability_id=None,
        verified_at="2026-07-30T00:00:00Z",
        expires_at=None,
        release_id=None,
        reason="authority_unavailable",
    ).public_dict()
    schema = intrinsic_official_state_schema()
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)

    root = Path(__file__).resolve().parents[1]
    openapi = yaml.safe_load((root / "docs/api/openapi.yaml").read_text())
    assert openapi["components"]["schemas"]["IntrinsicOfficialState"] == schema


@pytest.mark.parametrize(
    "forbidden",
    [
        "signature",
        "private_key",
        "raw_receipt",
        "trust_root",
        "ledger_path",
        "policy_digest",
        "dataset_digest",
        "observation_root_digest",
        "calibration_claim",
        "unknown_field",
    ],
)
def test_canonical_schema_rejects_authority_and_unknown_fields(forbidden) -> None:
    value = IntrinsicOfficialState(
        state="blocked",
        capability_id=None,
        verified_at="2026-07-30T00:00:00Z",
        expires_at=None,
        release_id=None,
        reason="authority_unavailable",
    ).public_dict()
    value[forbidden] = {"secret": "SECRET"}
    with pytest.raises(ValidationError):
        validate_intrinsic_official_state(value)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("state", "passed"),
        ("verified_at", "not-a-time"),
        ("verified_at", {"nested": "SECRET"}),
        ("capability_id", []),
        ("release_id", "x" * 257),
        ("reason", ""),
        ("reason", "free-form secret text"),
    ],
)
def test_runtime_dataclass_rejects_malformed_values(field, invalid) -> None:
    values = {
        "state": "blocked",
        "capability_id": None,
        "verified_at": "2026-07-30T00:00:00Z",
        "expires_at": None,
        "release_id": None,
        "reason": "authority_unavailable",
    }
    values[field] = invalid
    with pytest.raises(ValidationError):
        IntrinsicOfficialState(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capability_id", "raw-receipt-secret"),
        ("capability_id", "private_key"),
        ("release_id", "trust-root-secret"),
        ("release_id", "calibration.dataset"),
        ("release_id", "contains spaces"),
    ],
)
def test_runtime_contract_rejects_sensitive_or_nonopaque_identifiers(field, value) -> None:
    values = {
        "state": "blocked",
        "capability_id": None,
        "verified_at": "2026-07-30T00:00:00Z",
        "expires_at": None,
        "release_id": None,
        "reason": "authority_unavailable",
    }
    values[field] = value
    with pytest.raises((ValidationError, ValueError)):
        IntrinsicOfficialState(**values)


def test_public_dict_revalidates_even_if_runtime_state_is_tampered() -> None:
    state = IntrinsicOfficialState(
        state="blocked",
        capability_id=None,
        verified_at="2026-07-30T00:00:00Z",
        expires_at=None,
        release_id=None,
        reason="authority_unavailable",
    )
    object.__setattr__(state, "reason", {"raw_receipt": "SECRET"})
    with pytest.raises(ValidationError):
        state.public_dict()
