"""Public, non-authoritative transport contract for future official UI state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

INTRINSIC_OFFICIAL_STATE_SCHEMA_VERSION = (
    "trustforge.intrinsic-official-state/v1"
)
MAX_IDENTIFIER_LENGTH = 128
OFFICIAL_STATE_REASON_CODES = (
    "authority_unavailable",
    "shadow_only",
    "promotion_blocked",
    "verified",
    "expired",
    "invalid_authority",
)
_SENSITIVE_VALUE_TOKENS = (
    "secret",
    "receipt",
    "signature",
    "private",
    "trustroot",
    "trust_root",
    "calibration",
    "policy",
    "dataset",
    "observation",
)


def intrinsic_official_state_schema() -> dict[str, object]:
    """Return the canonical public transport schema."""
    nullable_timestamp = {
        "anyOf": [
            {
                "type": "string",
                "format": "date-time",
                "maxLength": 64,
                "pattern": r"(?:Z|[+-]\d{2}:\d{2})$",
            },
            {"type": "null"},
        ]
    }
    nullable_identifier = {
        "anyOf": [
            {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_IDENTIFIER_LENGTH,
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
            },
            {"type": "null"},
        ]
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": (
            "https://trustforge.local/contracts/"
            "intrinsic-official-state/v1"
        ),
        "title": "TrustForge IntrinsicOfficialState",
        "type": "object",
        "required": [
            "schema_version",
            "state",
            "capability_id",
            "verified_at",
            "expires_at",
            "release_id",
            "reason",
        ],
        "properties": {
            "schema_version": {
                "const": INTRINSIC_OFFICIAL_STATE_SCHEMA_VERSION
            },
            "state": {
                "enum": ["shadow", "blocked", "official", "error"]
            },
            "capability_id": nullable_identifier,
            "verified_at": {
                "type": "string",
                "format": "date-time",
                "maxLength": 64,
                "pattern": r"(?:Z|[+-]\d{2}:\d{2})$",
            },
            "expires_at": nullable_timestamp,
            "release_id": nullable_identifier,
            "reason": {"enum": list(OFFICIAL_STATE_REASON_CODES)},
        },
        "additionalProperties": False,
    }


def validate_intrinsic_official_state(value: object) -> None:
    """Reject malformed or authority-bearing public state at runtime."""
    # Kept lazy so lightweight release-router imports that only need schema
    # constants do not acquire the JSON Schema runtime dependency.
    from jsonschema import Draft202012Validator, FormatChecker

    Draft202012Validator(
        intrinsic_official_state_schema(),
        format_checker=FormatChecker(),
    ).validate(value)
    if isinstance(value, dict):
        for field in ("capability_id", "release_id"):
            item = value.get(field)
            if isinstance(item, str):
                normalized = item.casefold().replace("-", "_")
                if any(token in normalized for token in _SENSITIVE_VALUE_TOKENS):
                    raise ValueError(f"{field} contains a sensitive token")


@dataclass(frozen=True, slots=True)
class IntrinsicOfficialState:
    """Safe public projection shape; UIA does not issue this capability."""

    state: Literal["shadow", "blocked", "official", "error"]
    capability_id: str | None
    verified_at: str
    expires_at: str | None
    release_id: str | None
    reason: str
    schema_version: str = INTRINSIC_OFFICIAL_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_intrinsic_official_state(asdict(self))

    def public_dict(self) -> dict[str, object]:
        value = asdict(self)
        validate_intrinsic_official_state(value)
        return value


PUBLIC_OFFICIAL_STATE_FIELDS = frozenset(IntrinsicOfficialState.__dataclass_fields__)
SENSITIVE_AUTHORITY_FIELDS = frozenset(
    {
        "signature",
        "private_key",
        "public_key",
        "raw_receipt",
        "promotion_receipt",
        "calibration_claim",
        "trust_root",
        "ledger_path",
        "keyring",
        "policy_digest",
        "dataset_digest",
        "observation_root_digest",
    }
)

if PUBLIC_OFFICIAL_STATE_FIELDS & SENSITIVE_AUTHORITY_FIELDS:
    raise RuntimeError("public official-state contract exposes authority material")


__all__ = [
    "IntrinsicOfficialState",
    "INTRINSIC_OFFICIAL_STATE_SCHEMA_VERSION",
    "OFFICIAL_STATE_REASON_CODES",
    "PUBLIC_OFFICIAL_STATE_FIELDS",
    "SENSITIVE_AUTHORITY_FIELDS",
    "intrinsic_official_state_schema",
    "validate_intrinsic_official_state",
]
