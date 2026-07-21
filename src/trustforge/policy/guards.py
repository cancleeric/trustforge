"""Fail-closed security guards for outer-skill policy artifacts.

All checks raise SecurityError on violation — never partial-apply, never
fall-through on unknown.  Every rejection produces a structured audit dict
(accessible via SecurityError.audit) for execution logging.

Guard categories:
    1. FORBIDDEN_FAMILIES — archived/core families that cannot be executed
    2. FORBIDDEN_KEYS — core controls that outer skills may never override
    3. INJECTION_PATTERNS — code/template injection detection
    4. ALLOWED_ACTION_TYPES — unknown action types are rejected
"""
from __future__ import annotations

import json
import re
import time
from typing import Any


class SecurityError(Exception):
    """Raised on any fail-closed guard rejection.

    Attributes:
        audit: Structured dict suitable for execution log recording.
    """

    def __init__(self, message: str, *, audit: dict[str, Any] | None = None):
        super().__init__(message)
        self.audit: dict[str, Any] = audit or {
            "guard": "policy",
            "reason": message,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


# Families that are explicitly archived or must never be in the runtime path.
FORBIDDEN_FAMILIES: frozenset[str] = frozenset({
    "deploy", "core", "security", "cost",
})

# Top-level keys that outer skills may never set — these are core deterministic
# controls owned by the Trust Layer and pipeline invariants.
FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "trust_weights", "core", "time_boundary",
    "evidence_binding", "security", "cost", "deploy",
})

# Patterns that indicate arbitrary code execution or template injection.
INJECTION_PATTERNS: re.Pattern[str] = re.compile(
    r"(exec\s*\(|eval\s*\(|__import__\s*\(|\{\{|\$\{)"
)

# Exhaustive allowlist of action types — anything else is unknown → reject.
ALLOWED_ACTION_TYPES: frozenset[str] = frozenset({
    "set_param", "override_default", "toggle_feature",
    "adjust_limit", "reorder_list",
})


def check_family(family: str | None) -> None:
    """Reject forbidden/archived families."""
    if family in FORBIDDEN_FAMILIES:
        raise SecurityError(
            f"family '{family}' is archived/forbidden and cannot be executed",
            audit={
                "guard": "forbidden_family",
                "family": family,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )


def check_forbidden_keys(value: dict[str, Any]) -> None:
    """Reject artifacts that attempt to override core controls."""
    violations = set(value) & FORBIDDEN_KEYS
    if violations:
        raise SecurityError(
            f"outer skills may not override core controls: {sorted(violations)}",
            audit={
                "guard": "forbidden_keys",
                "keys": sorted(violations),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )


def check_injection(value: dict[str, Any]) -> None:
    """Reject artifacts containing code/template injection patterns."""
    raw = json.dumps(value, ensure_ascii=False)
    match = INJECTION_PATTERNS.search(raw)
    if match:
        raise SecurityError(
            f"potential code/template injection detected: {match.group()!r}",
            audit={
                "guard": "injection",
                "pattern": match.group(),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )


def check_actions(value: dict[str, Any]) -> None:
    """Reject artifacts with unknown action types (fail-closed)."""
    actions = value.get("actions")
    if not actions:
        return
    if not isinstance(actions, list):
        raise SecurityError(
            "actions field must be a list",
            audit={
                "guard": "invalid_actions",
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
    for action in actions:
        if not isinstance(action, dict):
            raise SecurityError(
                "each action must be a dict",
                audit={
                    "guard": "invalid_action_entry",
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
        action_type = action.get("type")
        if action_type not in ALLOWED_ACTION_TYPES:
            raise SecurityError(
                f"unknown action type: {action_type!r}",
                audit={
                    "guard": "unknown_action",
                    "action_type": action_type,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )


def check_artifact(value: dict[str, Any]) -> None:
    """Run all guards on a raw artifact dict. Raises SecurityError on first violation.

    This is the single entry point for guard validation — callers should use this
    rather than individual check_* functions to ensure complete coverage.
    """
    if not isinstance(value, dict):
        raise SecurityError(
            "artifact must be a dict",
            audit={
                "guard": "invalid_type",
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
    check_family(value.get("family"))
    check_forbidden_keys(value)
    check_injection(value)
    check_actions(value)
