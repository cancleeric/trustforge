"""Unit tests for the fail-closed Hermes production audit contracts."""
from __future__ import annotations

import inspect
import json
from dataclasses import replace

import pytest

import trustforge.hermes_audit_contracts as contracts
from trustforge.hermes_audit_contracts import (
    AggregateSummary,
    AuditBundle,
    AuditContractError,
    AuditLimits,
    AuditStatus,
    AuditTarget,
    ControlPlane,
    ControlState,
    DataPlane,
    DeadLetterSummary,
    EffectiveControl,
    EvidenceRef,
    ReadBudget,
    ReleaseComparison,
    ReleaseComparisonStatus,
    TableAudit,
    UnitEvidence,
    canonical_json,
    exit_code_for,
    format_safe_error,
    redact_or_reject_remote_payload,
    redact_secrets,
    secret_like_paths,
    sha256_digest,
)


def _digest(label: str) -> str:
    return sha256_digest({"label": label})


def _control_plane() -> ControlPlane:
    units = tuple(
        UnitEvidence(
            unit=unit,
            status=AuditStatus.COMPLETE,
            enabled=ControlState.DISABLED,
            active=ControlState.DISABLED,
            result="success",
            unit_sha256=_digest(f"unit-{index}"),
            drop_in_sha256=_digest(f"dropin-{index}"),
        )
        for index, unit in enumerate(contracts.SYSTEMD_UNIT_ALLOWLIST)
    )
    return ControlPlane(
        units=units,
        effective_control=EffectiveControl(
            runtime_guard=ControlState.DISABLED,
            dynamodb_config=ControlState.UNKNOWN,
            unit_env=ControlState.UNKNOWN,
            production_default=ControlState.ENABLED,
            effective=ControlState.DISABLED,
        ),
        redacted_config={"region": "ap-southeast-2", "coin-pool": "BTC-ETH-SOL"},
        release_comparison=(
            ReleaseComparison(
                field="manifest-digest",
                expected="release-20260730",
                deployed="release-20260730",
                status=ReleaseComparisonStatus.MATCH,
            ),
        ),
    )


def _data_plane() -> DataPlane:
    audits = tuple(
        TableAudit(
            table_type=table_type,
            status=AuditStatus.INSUFFICIENT_EVIDENCE,
            requests_used=1,
            items_used=0,
            bytes_used=0,
            truncated=False,
        )
        for table_type in contracts.TABLE_TYPE_ALLOWLIST
    )
    summary = AggregateSummary(
        status=AuditStatus.INSUFFICIENT_EVIDENCE,
        count=0,
        truncated=False,
    )
    return DataPlane(
        table_audits=audits,
        scheduler_summary=summary,
        cache_summary=summary,
        cost_summary=summary,
    )


def _bundle() -> AuditBundle:
    return AuditBundle.build(
        audit_id="audit-20260730",
        captured_at="2026-07-30T08:00:00Z",
        target=AuditTarget("ap-southeast-2", "i-0" + "0" * 16),
        invoker_identity_hash=_digest("invoker"),
        limits=AuditLimits.defaults(),
        overall_status=AuditStatus.PARTIAL,
        blockers=("insufficient-evidence",),
        control_plane=_control_plane(),
        data_plane=_data_plane(),
        dead_letters=(
            DeadLetterSummary(
                job_id_sha256=_digest("job-1"),
                coin="BTC",
                stage="fetch",
                attempt=2,
                error_class="TimeoutError",
                retry_state="quarantined",
                release_identity="release-20260730",
            ),
        ),
        evidence_refs=(
            EvidenceRef(
                acquisition_method="ssm-static-command-v1",
                acquired_at="2026-07-30T08:00:00Z",
                reduction_rule="allowlisted-aggregate-v1",
                truncated=False,
                content_sha256=_digest("ssm-summary"),
            ),
        ),
    )


def test_canonical_json_and_digest_are_deterministic_and_bounded() -> None:
    assert canonical_json({"b": [2, 1], "a": "ok"}) == canonical_json({"a": "ok", "b": [2, 1]})
    assert sha256_digest({"a": 1, "b": 2}) == sha256_digest({"b": 2, "a": 1})

    cycle: list[object] = []
    cycle.append(cycle)
    with pytest.raises(AuditContractError):
        canonical_json(cycle)
    with pytest.raises(AuditContractError):
        canonical_json({"not-finite": float("nan")})
    with pytest.raises(AuditContractError):
        canonical_json({1: "non-string-key"})


def test_limits_are_exact_and_may_only_be_lowered() -> None:
    defaults = AuditLimits.defaults()
    lowered = dict(defaults.read_budgets)
    lowered["dynamodb"] = ReadBudget(1, 1, 128, 1, 0)
    assert AuditLimits(60, lowered).overall_deadline_seconds == 60

    unknown = dict(defaults.read_budgets)
    unknown["arbitrary-table"] = ReadBudget(1, 1, 1, 1, 0)
    with pytest.raises(AuditContractError):
        AuditLimits(60, unknown)
    with pytest.raises(AuditContractError):
        AuditLimits(contracts.DEFAULT_AUDIT_DEADLINE_SECONDS + 1, dict(defaults.read_budgets))
    with pytest.raises(AuditContractError):
        AuditLimits(
            60,
            {
                **dict(defaults.read_budgets),
                "ssm": ReadBudget(13, 16, 262_144, 15, 2),
            },
        )


def test_release_comparison_and_control_precedence_fail_closed() -> None:
    assert ReleaseComparison(
        field="version",
        expected="v1",
        deployed=None,
        status=ReleaseComparisonStatus.UNKNOWN,
    ).status is ReleaseComparisonStatus.UNKNOWN
    with pytest.raises(AuditContractError):
        ReleaseComparison(
            field="version",
            expected="v1",
            deployed="v2",
            status=ReleaseComparisonStatus.MATCH,
        )

    assert EffectiveControl(
        runtime_guard=ControlState.UNKNOWN,
        dynamodb_config=ControlState.ENABLED,
        unit_env=ControlState.DISABLED,
        production_default=ControlState.DISABLED,
        effective=ControlState.ENABLED,
    ).effective is ControlState.ENABLED
    assert EffectiveControl(
        runtime_guard=ControlState.DISABLED,
        dynamodb_config=ControlState.UNKNOWN,
        unit_env=ControlState.UNKNOWN,
        production_default=ControlState.ENABLED,
        effective=ControlState.DISABLED,
    ).effective is ControlState.DISABLED


def test_secret_detection_redaction_and_safe_errors_never_emit_values() -> None:
    secret = "sk-0123456789abcdef"
    raw = {
        "api_key": secret,
        "error": f"authorization: {secret}",
        "nested": {"private_key": "-----BEGIN PRIVATE KEY-----\nexample"},
    }

    assert set(secret_like_paths(raw)) == {"api_key", "error", "nested.private_key"}
    redacted = redact_secrets(raw)
    serialized = json.dumps(redacted, sort_keys=True)
    assert secret not in serialized
    assert "PRIVATE KEY" not in serialized
    assert redacted["api_key"] == "[REDACTED]"
    with pytest.raises(AuditContractError):
        redact_or_reject_remote_payload(raw)

    formatted = format_safe_error(
        operation="ssm-send-command",
        resource="hermes-cycle.service",
        error=RuntimeError(f"remote response contained {secret}"),
    )
    assert formatted == {
        "operation": "ssm-send-command",
        "resource": "hermes-cycle.service",
        "error_class": "RuntimeError",
    }
    assert secret not in json.dumps(formatted)


def test_audit_bundle_is_sealed_secret_free_and_rejects_tampering() -> None:
    bundle = _bundle()
    evidence = bundle.to_dict()
    assert set(evidence) == {
        "schema_version", "audit_id", "captured_at", "target", "invoker_identity_hash", "limits",
        "overall_status", "warnings", "blockers", "control_plane", "data_plane", "dead_letters",
        "evidence_refs", "canonical_payload_sha256",
    }
    assert bundle.canonical_payload_sha256 == sha256_digest(bundle.payload_dict())
    assert "market body" not in json.dumps(evidence)
    assert "sk-0123456789abcdef" not in json.dumps(evidence)

    with pytest.raises(AuditContractError):
        replace(bundle, audit_id="audit-20260731")
    with pytest.raises(TypeError):
        AuditBundle.build(
            audit_id="audit-20260730",
            captured_at="2026-07-30T08:00:00Z",
            target=AuditTarget("ap-southeast-2", "i-0" + "0" * 16),
            invoker_identity_hash=_digest("invoker"),
            limits=AuditLimits.defaults(),
            overall_status=AuditStatus.PARTIAL,
            blockers=("insufficient-evidence",),
            control_plane=_control_plane(),
            data_plane=_data_plane(),
            unexpected_raw_payload="must-not-be-accepted",
        )


def test_exit_codes_and_module_surface_have_no_mutating_or_aws_clients() -> None:
    assert exit_code_for(AuditStatus.COMPLETE) == 0
    assert exit_code_for(AuditStatus.BLOCKED) == 2
    assert exit_code_for(AuditStatus.PARTIAL) == 3
    assert exit_code_for(AuditStatus.INSUFFICIENT_EVIDENCE) == 3
    assert exit_code_for(AuditStatus.INTEGRITY_FAILURE) == 4
    assert exit_code_for(AuditStatus.INTERNAL_FAILURE) == 5

    source = inspect.getsource(contracts)
    assert "import boto3" not in source
    for mutation in ("put_item", "update_item", "delete_item", "send_command", "start_session"):
        assert mutation not in source
