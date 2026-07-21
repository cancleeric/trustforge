"""Security tests: fail-closed guard verification.

Covers:
- Injection attempts (exec, eval, __import__, template interpolation)
- Forbidden key overrides (trust_weights, core, time_boundary, etc.)
- Unknown action types
- Archived/forbidden family attempts
- Path traversal in values (defense-in-depth)
- Normal text is NOT falsely flagged (no false positives)
"""
from __future__ import annotations

import pytest

from trustforge.policy.compiler import compile_policy
from trustforge.policy.guards import (
    ALLOWED_ACTION_TYPES,
    FORBIDDEN_FAMILIES,
    FORBIDDEN_KEYS,
    SecurityError,
    check_actions,
    check_artifact,
    check_family,
    check_forbidden_keys,
    check_injection,
)
from trustforge.skills import validate_artifact


class TestInjectionGuard:
    """INJECTION_PATTERNS catches code/template injection."""

    @pytest.mark.parametrize("payload", [
        "exec('import os')",
        "eval(user_input)",
        "__import__('subprocess')",
        "{{ config.SECRET_KEY }}",
        "${process.env.AWS_SECRET}",
        "exec (malicious_code)",
    ])
    def test_injection_detected(self, payload: str):
        artifact = {
            "family": "source",
            "name": "evil",
            "rules": [payload],
        }
        with pytest.raises(SecurityError, match="injection"):
            check_injection(artifact)

    @pytest.mark.parametrize("safe_text", [
        "execute the analysis pipeline",
        "evaluate the trust score",
        "import data from CSV",
        "the executive summary should include...",
        "evaluation metric: F1 score",
        "use imported modules carefully",
        "timeout after 30 seconds",
        "fallback to local cache",
    ])
    def test_normal_text_not_flagged(self, safe_text: str):
        """Normal English/Chinese text must NOT trigger injection guard."""
        artifact = {
            "family": "source",
            "name": "safe",
            "rules": [safe_text],
        }
        # Should not raise.
        check_injection(artifact)

    def test_nested_injection(self):
        """Injection hidden in nested structure."""
        artifact = {
            "family": "source",
            "name": "nested",
            "rules": ["ok"],
            "metadata": {"description": "harmless", "hook": "eval(x)"},
        }
        with pytest.raises(SecurityError, match="injection"):
            check_injection(artifact)


class TestForbiddenKeysGuard:
    """FORBIDDEN_KEYS prevents core control override."""

    @pytest.mark.parametrize("key", sorted(FORBIDDEN_KEYS))
    def test_each_forbidden_key_rejected(self, key: str):
        artifact = {
            "family": "source",
            "name": "bad",
            "rules": ["r"],
            key: {"override": True},
        }
        with pytest.raises(SecurityError, match="core controls"):
            check_forbidden_keys(artifact)

    def test_multiple_forbidden_keys(self):
        artifact = {
            "family": "source",
            "name": "bad",
            "rules": ["r"],
            "trust_weights": {},
            "security": {},
        }
        with pytest.raises(SecurityError, match="core controls"):
            check_forbidden_keys(artifact)

    def test_allowed_keys_pass(self):
        artifact = {
            "family": "source",
            "name": "ok",
            "rules": ["r"],
            "policy": {"timeout_sec": 10},
            "description": "fine",
        }
        # Should not raise.
        check_forbidden_keys(artifact)


class TestForbiddenFamilyGuard:
    """FORBIDDEN_FAMILIES are rejected."""

    @pytest.mark.parametrize("family", sorted(FORBIDDEN_FAMILIES))
    def test_each_forbidden_family_rejected(self, family: str):
        with pytest.raises(SecurityError, match="archived/forbidden"):
            check_family(family)

    def test_valid_family_passes(self):
        check_family("source")
        check_family("analysis")
        check_family(None)  # None is not in FORBIDDEN_FAMILIES, handled later

    def test_validate_artifact_rejects_deploy(self):
        """skills.validate_artifact also rejects archived families."""
        with pytest.raises(ValueError, match="archived/forbidden"):
            validate_artifact({"family": "deploy", "rules": ["r"]})

    def test_validate_artifact_rejects_core(self):
        with pytest.raises(ValueError, match="archived/forbidden"):
            validate_artifact({"family": "core", "rules": ["r"]})

    def test_validate_artifact_rejects_security(self):
        with pytest.raises(ValueError, match="archived/forbidden"):
            validate_artifact({"family": "security", "rules": ["r"]})

    def test_validate_artifact_rejects_cost(self):
        with pytest.raises(ValueError, match="archived/forbidden"):
            validate_artifact({"family": "cost", "rules": ["r"]})


class TestUnknownActionGuard:
    """Unknown action types are fail-closed rejected."""

    def test_unknown_action_type_rejected(self):
        artifact = {
            "family": "source",
            "name": "bad",
            "rules": ["r"],
            "actions": [{"type": "execute_shell"}],
        }
        with pytest.raises(SecurityError, match="unknown action type"):
            check_actions(artifact)

    def test_none_action_type_rejected(self):
        artifact = {
            "family": "source",
            "name": "bad",
            "rules": ["r"],
            "actions": [{"no_type_key": True}],
        }
        with pytest.raises(SecurityError, match="unknown action type"):
            check_actions(artifact)

    def test_allowed_action_types_pass(self):
        for action_type in sorted(ALLOWED_ACTION_TYPES):
            artifact = {
                "family": "source",
                "name": "ok",
                "rules": ["r"],
                "actions": [{"type": action_type, "key": "x", "value": 1}],
            }
            check_actions(artifact)  # Should not raise.

    def test_no_actions_field_passes(self):
        artifact = {"family": "source", "name": "ok", "rules": ["r"]}
        check_actions(artifact)  # Should not raise.


class TestCheckArtifactIntegrated:
    """check_artifact runs all guards in sequence."""

    def test_forbidden_family_caught_first(self):
        with pytest.raises(SecurityError, match="archived/forbidden"):
            check_artifact({"family": "deploy", "rules": ["r"]})

    def test_forbidden_key_caught(self):
        with pytest.raises(SecurityError, match="core controls"):
            check_artifact({"family": "source", "rules": ["r"], "trust_weights": {}})

    def test_injection_caught(self):
        with pytest.raises(SecurityError, match="injection"):
            check_artifact({"family": "source", "rules": ["eval(x)"]})

    def test_unknown_action_caught(self):
        with pytest.raises(SecurityError, match="unknown action"):
            check_artifact({"family": "source", "rules": ["r"], "actions": [{"type": "rm_rf"}]})

    def test_non_dict_rejected(self):
        with pytest.raises(SecurityError, match="must be a dict"):
            check_artifact("not a dict")  # type: ignore[arg-type]

    def test_clean_artifact_passes(self):
        """A valid artifact passes all guards without raising."""
        artifact = {
            "family": "source",
            "name": "valid",
            "rules": ["fetch with timeout"],
            "policy": {"timeout_sec": 20},
            "actions": [{"type": "set_param", "key": "timeout_sec", "value": 20}],
        }
        check_artifact(artifact)  # Should not raise.


class TestCompilerGuardIntegration:
    """compile_policy invokes guards before compilation."""

    def test_forbidden_family_via_compiler(self):
        with pytest.raises(SecurityError, match="archived/forbidden"):
            compile_policy({"family": "deploy", "name": "x", "rules": ["r"]})

    def test_injection_via_compiler(self):
        with pytest.raises(SecurityError, match="injection"):
            compile_policy({"family": "source", "name": "x", "rules": ["exec(x)"]})

    def test_forbidden_key_via_compiler(self):
        with pytest.raises(SecurityError, match="core controls"):
            compile_policy({"family": "source", "name": "x", "rules": ["r"], "trust_weights": {}})


class TestSecurityAuditEvent:
    """SecurityError carries structured audit data."""

    def test_audit_has_guard_and_reason(self):
        try:
            check_artifact({"family": "deploy", "rules": ["r"]})
        except SecurityError as exc:
            assert "guard" in exc.audit
            assert "ts" in exc.audit
            assert exc.audit["guard"] == "forbidden_family"
            assert exc.audit["family"] == "deploy"
        else:
            pytest.fail("SecurityError not raised")

    def test_injection_audit_includes_pattern(self):
        try:
            check_artifact({"family": "source", "rules": ["eval(x)"]})
        except SecurityError as exc:
            assert exc.audit["guard"] == "injection"
            assert "pattern" in exc.audit
        else:
            pytest.fail("SecurityError not raised")
