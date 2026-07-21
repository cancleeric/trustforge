"""Compile raw skill artifact dicts into frozen TypedPolicy dataclasses.

The compiler validates fields against the typed schema, rejects unknown
families, and returns an immutable policy object that consumers can use
with attribute access instead of raw dict parsing.

compile_policy() is the public entry point:
    raw artifact dict → TypedPolicy (frozen dataclass)
"""
from __future__ import annotations

import dataclasses
from typing import Any

from .guards import SecurityError, check_artifact
from .schema import FAMILY_SCHEMA


def compile_policy(artifact: dict[str, Any]) -> Any:
    """Compile a raw artifact dict into a frozen TypedPolicy instance.

    Args:
        artifact: Raw dict from a skill artifact file.  Must contain at minimum
                  a "family" key matching one of FAMILY_SCHEMA, and optionally
                  a "policy" sub-dict with typed field overrides.

    Returns:
        A frozen dataclass instance corresponding to the family's schema.

    Raises:
        SecurityError: If guards reject the artifact (forbidden family, injection, etc.)
        ValueError: If the family is unknown or field types are invalid.
    """
    # Run all security guards first (fail-closed).
    check_artifact(artifact)

    family = artifact.get("family")
    if family not in FAMILY_SCHEMA:
        raise ValueError(
            f"unsupported policy family: {family!r}; "
            f"allowed: {sorted(FAMILY_SCHEMA)}"
        )

    schema_cls = FAMILY_SCHEMA[family]
    policy_data = artifact.get("policy", {})

    if not isinstance(policy_data, dict):
        raise ValueError(
            f"'policy' field must be a dict for family '{family}', "
            f"got {type(policy_data).__name__}"
        )

    # Extract only fields defined in the schema — reject silently to avoid
    # leaking internal field names in error messages.  Unknown keys in policy
    # are simply ignored (not an error), allowing forward-compatible artifacts.
    valid_fields = {f.name for f in dataclasses.fields(schema_cls)}
    filtered: dict[str, Any] = {}

    for key, value in policy_data.items():
        if key not in valid_fields:
            continue  # ignore forward-compatible unknown fields
        # Validate type for basic safety (int/float/bool/str/list).
        expected_field = next(f for f in dataclasses.fields(schema_cls) if f.name == key)
        filtered[key] = _coerce_field(key, value, expected_field, family)

    return schema_cls(**filtered)


def _coerce_field(
    key: str, value: Any, field_info: dataclasses.Field, family: str
) -> Any:
    """Validate and coerce a single field value against its declared type.

    Raises ValueError on type mismatch.
    """
    # Resolve the expected type from the field's annotation string or type.
    expected_type = field_info.type
    if isinstance(expected_type, str):
        # Handle forward-reference annotations like 'list[str]'.
        type_map = {"int": int, "float": float, "bool": bool, "str": str}
        if expected_type in type_map:
            expected_type = type_map[expected_type]
        elif expected_type.startswith("list"):
            expected_type = list
        else:
            # Can't validate complex types statically — accept as-is.
            return value
    elif hasattr(expected_type, "__origin__"):
        # Generic like list[str] → just check it's a list.
        expected_type = expected_type.__origin__

    if expected_type is float and isinstance(value, (int, float)):
        return float(value)
    if expected_type is int and isinstance(value, int) and not isinstance(value, bool):
        return value
    if expected_type is bool and isinstance(value, bool):
        return value
    if expected_type is str and isinstance(value, str):
        return value
    if expected_type is list and isinstance(value, list):
        return value

    if not isinstance(value, expected_type):
        raise ValueError(
            f"policy field '{key}' for family '{family}' expects "
            f"{expected_type.__name__}, got {type(value).__name__}: {value!r}"
        )
    return value
