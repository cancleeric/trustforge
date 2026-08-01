"""Mocked regressions for the bounded, read-only Hermes audit adapter."""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from scripts import hermes_production_audit as cli
from trustforge import hermes_audit as audit
from trustforge.authenticated_ledger import AuthenticatedLedger
from trustforge.hermes_audit import (
    AwsAuditClients,
    DynamoAuditReader,
    _bounded_ssm_snapshot,
    dry_run_plan,
    run_audit,
    write_evidence_bundle,
)
from trustforge.hermes_audit_contracts import (
    AuditBundle,
    AuditContractError,
    AuditLimits,
    AuditStatus,
    AuditTarget,
    ControlState,
    SYSTEMD_UNIT_ALLOWLIST,
    TABLE_TYPE_ALLOWLIST,
    ReadBudget,
    sha256_digest,
)
from trustforge.hermes_audit_signing import (
    APPROVAL_NONCE_LEDGER_KEY_ID,
    derive_approval_nonce_ledger_keyring,
    derive_nonce_ledger_keyring,
    sign_approval_attestation,
    sign_evidence_bundle,
)
from trustforge.secure_keyring import SecureKeyringError

REGION = "ap-southeast-2"
INSTANCE = "i-0" + "0" * 16
TARGET = AuditTarget(REGION, INSTANCE)


def _signer(tmp_path: Path, *, key_id: str = "test-signer"):
    """Build a real signer (fresh Ed25519 key + nonce ledger) for tests that
    only need write_evidence_bundle to succeed, not to inspect the signature.
    """
    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    ledger = AuthenticatedLedger(
        keyring=derive_nonce_ledger_keyring(key_id, private_bytes),
        active_key_id=key_id,
        test_directory_override=tmp_path / "nonce-ledger",
    )

    def sign(bundle: AuditBundle):
        return sign_evidence_bundle(
            bundle, private_key=private_bytes, key_id=key_id, actor="test-actor",
            nonce_store=ledger,
        )

    return sign


def _dead_letters_section(
    *, available: bool = True, truncated: bool = False, rows: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"available": available, "truncated": truncated, "rows": rows or []}


def _snapshot(
    *,
    runtime_guard: str = "disabled",
    version: str = "v0.27.37",
    table_names: dict[str, str] | None = None,
    dead_letters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    units = []
    for name in SYSTEMD_UNIT_ALLOWLIST:
        units.append(
            {
                "unit": name,
                "enabled": "disabled",
                "active": "inactive",
                "result": "success",
                "unit_sha256": None,
                "drop_in_sha256": None,
                "hermes_unit_env": "disabled" if name == "trustforge-analysis-flow.service" else "unknown",
                "timer_next_monotonic_usec": 100 if name.endswith(".timer") else None,
                "timer_last_trigger_monotonic_usec": 50 if name.endswith(".timer") else None,
                "journal_error_count": 0,
            }
        )
    return {
        "schema_version": audit.REMOTE_SCHEMA_VERSION,
        "units": units,
        "control": {
            "runtime_guard": runtime_guard,
            "unit_env": "disabled",
            "production_default": "disabled",
        },
        "config": {
            name: (
                (table_names or {}).get(name, audit._APPROVED_TABLE_NAMES[
                    next(t for t, flag in audit._TABLE_TYPE_NAME_FLAGS.items() if flag == name)
                ])
                if name in audit._TABLE_NAME_FLAGS
                else "configured"
            )
            for name in audit._CONFIG_ALLOWLIST
        },
        "release": {
            "version": version,
            "python": "3.11.9",
            "manifest_sha256": sha256_digest({"manifest": "fixture"}),
        },
        "dead_letters": dead_letters or _dead_letters_section(),
    }


class FakeSts:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.calls = 0

    def get_caller_identity(self) -> dict[str, str]:
        self.calls += 1
        if self.error:
            raise self.error
        return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:role/audit", "UserId": "audit-user"}


class FakeSsm:
    def __init__(self, snapshot: dict[str, Any] | None = None, *, online: bool = True, statuses: list[str] | None = None):
        self.snapshot = snapshot or _snapshot()
        self.online = online
        self.statuses = list(statuses or ["Success"])
        self.send_calls: list[dict[str, Any]] = []
        self.get_calls = 0

    def describe_instance_information(self, **kwargs: Any) -> dict[str, Any]:
        if not self.online:
            return {"InstanceInformationList": []}
        return {"InstanceInformationList": [{"InstanceId": INSTANCE, "PingStatus": "Online"}]}

    def send_command(self, **kwargs: Any) -> dict[str, Any]:
        self.send_calls.append(kwargs)
        return {"Command": {"CommandId": "command-1"}}

    def get_command_invocation(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls += 1
        status = self.statuses.pop(0) if self.statuses else "InProgress"
        return {"Status": status, "StandardOutputContent": json.dumps(self.snapshot, sort_keys=True)}


class ProvisionedThroughputExceededException(Exception):
    """Stand-in for the botocore throttling error raised under read pressure."""


class ValidationException(Exception):
    """Stand-in for the DynamoDB error raised when a projection is malformed."""


# Subset of the DynamoDB reserved words that the audit projections actually touch.
DYNAMO_RESERVED_WORDS = frozenset({"STATUS", "VERSION", "TTL", "TIMESTAMP", "SOURCE", "NAME", "VALUE", "SIZE"})


def _assert_projection_is_wire_safe(kwargs: dict[str, Any]) -> None:
    """Reject what real DynamoDB rejects: bare reserved words in a projection."""
    expression = kwargs.get("ProjectionExpression")
    if expression is None:
        return
    declared = kwargs.get("ExpressionAttributeNames") or {}
    for token in (part.strip() for part in expression.split(",")):
        if token.startswith("#"):
            if token not in declared:
                raise ValidationException(f"undefined expression attribute name: {token}")
            continue
        if token.upper() in DYNAMO_RESERVED_WORDS:
            raise ValidationException(f"reserved keyword used in projection: {token}")


class FakeDynamo:
    """Low-level Dynamo stub that records only audit reader operations."""

    _schemas = {
        "trustforge-connector-cache": {"source_id", "coin"},
        "trustforge-scheduler-runs": {"run_id", "ts"},
        "trustforge-cost-ledger": {"run_id", "ts"},
        "trustforge-analysis-dead-letters": {"job_id", "created_at"},
    }

    def __init__(self, *, empty: bool = False, deny_table: str | None = None, truncated: bool = False, admin_enabled: bool = False, malformed_table: str | None = None, secret_table: str | None = None, throttle_table: str | None = None):
        self.empty = empty
        self.deny_table = deny_table
        self.truncated = truncated
        self.admin_enabled = admin_enabled
        self.malformed_table = malformed_table
        self.secret_table = secret_table
        self.throttle_table = throttle_table
        self.throttle_hits = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, operation: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((operation, kwargs))
        _assert_projection_is_wire_safe(kwargs)
        if kwargs["TableName"] == self.deny_table:
            raise PermissionError("AccessDenied")
        if kwargs["TableName"] == self.throttle_table:
            self.throttle_hits += 1
            raise ProvisionedThroughputExceededException("Throughput exceeds the current capacity")

    def describe_table(self, **kwargs: Any) -> dict[str, Any]:
        self._record("describe_table", kwargs)
        return {"Table": {"KeySchema": [{"AttributeName": name} for name in sorted(self._schemas[kwargs["TableName"]])]}}

    def describe_time_to_live(self, **kwargs: Any) -> dict[str, Any]:
        self._record("describe_time_to_live", kwargs)
        return {"TimeToLiveDescription": {"TimeToLiveStatus": "ENABLED"}}

    def get_item(self, **kwargs: Any) -> dict[str, Any]:
        self._record("get_item", kwargs)
        if self.empty:
            return {}
        return {"Item": {"hermes_autonomy_enabled": {"BOOL": self.admin_enabled}, "version": {"N": "3"}, "updated_at": {"S": "2026-07-30T00:00:00Z"}}}

    def scan(self, **kwargs: Any) -> dict[str, Any]:
        self._record("scan", kwargs)
        if self.empty:
            return {"Items": []}
        table = kwargs["TableName"]
        items = {
            "trustforge-connector-cache": [{"source_id": {"S": "coingecko"}, "coin": {"S": "BTC"}, "fetched_at": {"N": "1"}, "ttl": {"N": "2"}}],
            "trustforge-scheduler-runs": [{"run_id": {"S": "run-1"}, "ts": {"S": "2026-07-30T00:00:00Z"}, "status": {"S": "success"}, "release_identity": {"S": "v0.27.37"}}],
            "trustforge-cost-ledger": [{"run_id": {"S": "run-1"}, "ts": {"S": "2026-07-30T00:00:00Z"}, "status": {"S": "ok"}, "cost_usd": {"N": "0.01"}}],
            "trustforge-analysis-dead-letters": [{"job_id": {"S": "job-1"}, "coin": {"S": "BTC"}, "stage": {"S": "fetch"}, "attempt": {"N": "2"}, "error_class": {"S": "TimeoutError"}, "retry_state": {"S": "quarantined"}, "release_identity": {"S": "v0.27.37"}, "created_at": {"S": "2026-07-30T00:00:00Z"}}],
        }[table]
        if self.secret_table == table:
            items = [{"run_id": {"S": "run-1"}, "ts": {"S": "2026-07-30T00:00:00Z"}, "status": {"S": "sk-0123456789abcdef"}}]
        if self.malformed_table == table:
            items = [{"unexpected": {"S": "not-a-valid-row"}}]
        response: dict[str, Any] = {"Items": items}
        if self.truncated:
            response["LastEvaluatedKey"] = {"opaque": {"S": "not-persisted"}}
        return response

    def __getattr__(self, name: str) -> Any:
        if name in {"put_item", "update_item", "delete_item", "transact_write_items"}:
            raise AssertionError(f"mutation must not be called: {name}")
        raise AttributeError(name)


def _clients(*, snapshot: dict[str, Any] | None = None, online: bool = True, statuses: list[str] | None = None, dynamo: FakeDynamo | None = None) -> tuple[AwsAuditClients, FakeSsm, FakeDynamo]:
    ssm = FakeSsm(snapshot, online=online, statuses=statuses)
    database = dynamo or FakeDynamo()
    return AwsAuditClients(FakeSts(), ssm, database), ssm, database


def test_dry_run_and_cli_are_local_only_and_validate_output_path(capsys: pytest.CaptureFixture[str]) -> None:
    output_dir = Path(__file__).resolve().parents[1] / "out" / "audits" / "hermes" / "dry-run-no-write"
    plan = dry_run_plan(TARGET, output_dir, AuditLimits.defaults(), "v0.27.37")
    assert plan["mode"] == "dry-run"
    assert plan["no_mutation"] is True
    assert plan["static_ssm_command_sha256"] == audit.STATIC_SSM_COMMAND_DIGEST
    assert not output_dir.exists()

    assert cli.main(["--region", REGION, "--instance-id", INSTANCE, "--output-dir", str(output_dir), "--expected-release", "v0.27.37", "--dry-run"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["mode"] == "dry-run"
    assert not output_dir.exists()
    with pytest.raises(AuditContractError):
        dry_run_plan(TARGET, Path("/tmp/outside-audit"), AuditLimits.defaults(), None)
    with pytest.raises(AuditContractError):
        dry_run_plan(TARGET, output_dir, AuditLimits.defaults(), "v0.27.37; injected")


def test_static_command_has_no_cli_interpolation_or_mutation_surface() -> None:
    source = inspect.getsource(audit)
    assert audit.STATIC_SSM_COMMAND_DIGEST == "sha256:" + hashlib.sha256(
        b"trustforge.hermes-static-ssm.v1\x00" + audit.STATIC_SSM_COMMAND.encode("utf-8")
    ).hexdigest()
    assert "shell=True" not in audit.STATIC_SSM_COMMAND
    assert "systemctl cat" not in audit.STATIC_SSM_COMMAND
    assert "systemctl restart" not in audit.STATIC_SSM_COMMAND
    assert "aws " not in audit.STATIC_SSM_COMMAND
    for mutation in (".put_item(", ".update_item(", ".delete_item(", ".transact_write_items(", "start_session"):
        assert mutation not in source
    with pytest.raises(AuditContractError):
        AuditTarget(REGION, f"{INSTANCE}; whoami")


def test_preflight_target_mismatch_is_blocked_without_ssm_command() -> None:
    clients, ssm, dynamo = _clients(online=False)
    bundle = run_audit(TARGET, clients, expected_release="v0.27.37")
    assert bundle.overall_status is AuditStatus.BLOCKED
    assert bundle.blockers == ("ssm-target-not-managed",)
    assert not ssm.send_calls
    assert not dynamo.calls


def test_unknown_control_is_fail_closed_and_static_command_is_exact() -> None:
    clients, ssm, dynamo = _clients(snapshot=_snapshot(runtime_guard="unknown"))
    bundle = run_audit(TARGET, clients, expected_release="v0.27.37")
    assert bundle.overall_status is AuditStatus.PARTIAL
    assert "release-evidence-unknown" in bundle.blockers
    assert ssm.send_calls[0]["Parameters"] == {"commands": [audit.STATIC_SSM_COMMAND]}
    assert ssm.send_calls[0]["DocumentName"] == "AWS-RunShellScript"
    assert all(operation in {"describe_table", "describe_time_to_live", "get_item", "scan"} for operation, _ in dynamo.calls)
    serialized = json.dumps(bundle.to_dict(), sort_keys=True)
    assert "Environment=" not in serialized
    assert "journalctl" not in serialized


def test_complete_fixture_collects_ttl_timer_and_redacted_dead_letter_summary() -> None:
    row = {
        "job_id": "job-42", "coin": "btc", "stage": "fetch", "attempt": 2,
        "error_class": "TimeoutError", "retry_state": "dead-lettered", "release_identity": None,
    }
    snapshot = _snapshot(runtime_guard="disabled", dead_letters=_dead_letters_section(rows=[row]))
    clients, _, _ = _clients(snapshot=snapshot)
    bundle = run_audit(TARGET, clients, expected_release="v0.27.37")
    # Whatever this fixture's status turns out to be for unrelated release-comparison
    # reasons, the durable-dead-letter table itself must read as healthy: an
    # available, non-truncated SQLite read is COMPLETE, not insufficient evidence.
    dead_letter_audit = next(item for item in bundle.data_plane.table_audits if item.table_type == "durable-dead-letter")
    assert dead_letter_audit.status is AuditStatus.COMPLETE
    assert bundle.control_plane.effective_control.effective is ControlState.DISABLED
    assert bundle.control_plane.units[0].timer_next_monotonic_usec == 100
    assert bundle.control_plane.units[0].journal_error_count == 0
    assert all(item.ttl_status is ControlState.ENABLED for item in bundle.data_plane.table_audits if item.table_type != "durable-dead-letter")
    assert len(bundle.dead_letters) == 1
    summary = bundle.dead_letters[0]
    assert summary.coin == "BTC"
    assert summary.stage == "fetch"
    assert summary.attempt == 2
    assert summary.error_class == "TimeoutError"
    assert summary.retry_state == "dead-lettered"
    assert summary.release_identity is None
    assert "dynamodb-durable-dead-letter-insufficient-evidence" not in bundle.warnings
    serialized = json.dumps(bundle.to_dict(), sort_keys=True)
    assert "job-42" not in serialized


def test_secret_like_ssm_output_is_integrity_failure_without_payload_leakage() -> None:
    snapshot = _snapshot()
    secret = "sk-0123456789abcdef"
    snapshot["release"]["version"] = secret
    clients, _, _ = _clients(snapshot=snapshot)
    bundle = run_audit(TARGET, clients)
    assert bundle.overall_status is AuditStatus.INTEGRITY_FAILURE
    assert secret not in json.dumps(bundle.to_dict(), sort_keys=True)


def test_secret_like_dynamodb_payload_is_integrity_failure_without_payload_leakage() -> None:
    database = FakeDynamo(secret_table="trustforge-scheduler-runs")
    clients, _, _ = _clients(dynamo=database)
    bundle = run_audit(TARGET, clients)
    assert bundle.overall_status is AuditStatus.INTEGRITY_FAILURE
    assert "sk-0123456789abcdef" not in json.dumps(bundle.to_dict(), sort_keys=True)


def test_ssm_timeout_is_partial_before_dynamodb_read() -> None:
    clients, _, dynamo = _clients(statuses=["InProgress"])
    bundle = run_audit(TARGET, clients, sleep=lambda _: None)
    assert bundle.overall_status is AuditStatus.PARTIAL
    assert bundle.blockers == ("ssm-static-command-timeout-or-budget-exhausted",)
    assert not dynamo.calls


def test_dynamodb_empty_denied_and_truncated_results_are_insufficient_evidence() -> None:
    empty = FakeDynamo(empty=True)
    state, data, _, _, warnings = DynamoAuditReader(empty, AuditLimits.defaults()).collect()
    assert state is ControlState.UNKNOWN
    assert all(item.status is AuditStatus.INSUFFICIENT_EVIDENCE for item in data.table_audits)
    assert "dynamodb-durable-dead-letter-insufficient-evidence" in warnings
    assert {name for name, _ in empty.calls} == {"describe_table", "describe_time_to_live", "get_item", "scan"}

    denied = FakeDynamo(deny_table="trustforge-scheduler-runs")
    _, data, _, _, warnings = DynamoAuditReader(denied, AuditLimits.defaults()).collect()
    scheduler = next(item for item in data.table_audits if item.table_type == "scheduler-run")
    assert scheduler.status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert "dynamodb-scheduler-run-PermissionError" in warnings

    truncated = FakeDynamo(truncated=True)
    _, data, _, _, _ = DynamoAuditReader(truncated, AuditLimits.defaults()).collect()
    assert all(item.status is AuditStatus.INSUFFICIENT_EVIDENCE or item.table_type == "admin-config" for item in data.table_audits)
    assert next(item for item in data.table_audits if item.table_type == "connector-cache").truncated is True


def test_every_projected_attribute_is_aliased_against_dynamodb_reserved_words() -> None:
    reader = FakeDynamo()
    _, data, _, _, warnings = DynamoAuditReader(reader, AuditLimits.defaults()).collect()

    # No table may degrade: a ValidationException here means the projection hit the wire bare.
    assert not any(item.endswith("ValidationException") for item in warnings)
    for audit_row in data.table_audits:
        if audit_row.table_type != "durable-dead-letter":
            assert audit_row.status is AuditStatus.COMPLETE

    projected = [(name, kwargs) for name, kwargs in reader.calls if "ProjectionExpression" in kwargs]
    assert {name for name, _ in projected} == {"get_item", "scan"}
    for _, kwargs in projected:
        expression = kwargs["ProjectionExpression"]
        declared = kwargs["ExpressionAttributeNames"]
        tokens = [part.strip() for part in expression.split(",")]
        # Every token is an alias, every alias is declared, and no bare name survives.
        assert tokens and all(token.startswith("#") for token in tokens)
        assert {token: declared[token] for token in tokens} == declared
        assert not any(value.upper() in DYNAMO_RESERVED_WORDS and value in tokens for value in declared.values())

    # The contract holds for the reserved words the audit actually reads.
    admin = next(kwargs for name, kwargs in projected if name == "get_item")
    assert "version" in admin["ExpressionAttributeNames"].values()
    assert "#version" in admin["ProjectionExpression"]
    scans = [kwargs for name, kwargs in projected if name == "scan"]
    assert any("status" in kwargs["ExpressionAttributeNames"].values() for kwargs in scans)
    assert any("ttl" in kwargs["ExpressionAttributeNames"].values() for kwargs in scans)


def test_dynamodb_throttling_is_insufficient_evidence_and_never_reads_as_healthy() -> None:
    throttled = FakeDynamo(throttle_table="trustforge-connector-cache")
    limits = AuditLimits.defaults()
    _, data, _, refs, warnings = DynamoAuditReader(throttled, limits).collect()

    cache = next(item for item in data.table_audits if item.table_type == "connector-cache")
    assert throttled.throttle_hits >= 1
    assert cache.status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert (cache.items_used, cache.bytes_used, cache.truncated) == (0, 0, False)
    assert cache.ttl_status is ControlState.UNKNOWN
    assert "dynamodb-connector-cache-ProvisionedThroughputExceededException" in warnings

    # A throttled read must never be laundered into a healthy aggregate.
    assert data.cache_summary.status is not AuditStatus.COMPLETE

    # Neither the raw table name nor the driver message may reach persisted evidence.
    persisted = json.dumps({"warnings": list(warnings), "refs": [dataclasses.asdict(ref) for ref in refs], "tables": [item.table_type for item in data.table_audits]}, sort_keys=True)
    assert "trustforge-connector-cache" not in persisted
    assert "Throughput exceeds" not in persisted

    # Failure stays isolated: the remaining allowlisted tables are still collected.
    scheduler = next(item for item in data.table_audits if item.table_type == "scheduler-run")
    cost = next(item for item in data.table_audits if item.table_type == "cost-ledger")
    assert scheduler.status is AuditStatus.COMPLETE
    assert cost.status is AuditStatus.COMPLETE

    # Throttling must not buy extra retries beyond the reserved request budget.
    assert sum(item.requests_used for item in data.table_audits) <= limits.read_budgets["dynamodb"].request_limit
    assert len(throttled.calls) <= limits.read_budgets["dynamodb"].request_limit

    # A throttled collection degrades the whole audit to partial, not complete.
    clients, _, _ = _clients(snapshot=_snapshot(runtime_guard="disabled"), dynamo=FakeDynamo(throttle_table="trustforge-connector-cache"))
    bundle = run_audit(TARGET, clients, expected_release="v0.27.37")
    assert bundle.overall_status is AuditStatus.PARTIAL
    assert "insufficient-evidence" in bundle.blockers
    assert "collector-warning" in bundle.blockers
    assert "trustforge-connector-cache" not in json.dumps(bundle.to_dict(), sort_keys=True)


def test_writer_creates_verified_local_bundle_and_rejects_outside_output(tmp_path: Path) -> None:
    clients, _, _ = _clients(snapshot=_snapshot(runtime_guard="disabled"))
    bundle = run_audit(TARGET, clients, expected_release="v0.27.37")
    root = Path(__file__).resolve().parents[1] / "out" / "audits" / "hermes"
    signer = _signer(tmp_path)
    destination = write_evidence_bundle(bundle, root, signer=signer)
    try:
        evidence = destination / "evidence.json"
        summary = destination / "summary.md"
        attestation = destination / "ATTESTATION.json"
        checksums = (destination / "SHA256SUMS").read_text(encoding="utf-8")
        assert json.loads(evidence.read_text(encoding="utf-8"))["canonical_payload_sha256"] == bundle.canonical_payload_sha256
        assert hashlib.sha256(evidence.read_bytes()).hexdigest() in checksums
        assert hashlib.sha256(summary.read_bytes()).hexdigest() in checksums
        assert hashlib.sha256(attestation.read_bytes()).hexdigest() in checksums
        assert json.loads(attestation.read_text(encoding="utf-8"))["audit_id"] == bundle.audit_id
        assert "does not authorize feature flags" in summary.read_text(encoding="utf-8")
        with pytest.raises(FileExistsError):
            write_evidence_bundle(bundle, root, signer=signer)
        assert not list(root.glob(f".{bundle.audit_id}.*.incomplete"))
    finally:
        shutil.rmtree(destination)
    with pytest.raises(AuditContractError):
        write_evidence_bundle(bundle, Path("/tmp/outside-audit"), signer=signer)


def test_writer_does_not_delete_preexisting_staging_collision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clients, _, _ = _clients(snapshot=_snapshot(runtime_guard="disabled"))
    bundle = run_audit(TARGET, clients, expected_release="v0.27.37")
    root = Path(__file__).resolve().parents[1] / "out" / "audits" / "hermes"
    root.mkdir(parents=True, exist_ok=True)
    fixed_uuid = uuid.UUID("00000000-0000-4000-8000-000000000001")
    staging = root / f".{bundle.audit_id}.{fixed_uuid.hex}.incomplete"
    staging.mkdir(exist_ok=False)
    marker = staging / "preexisting-marker"
    marker.write_text("must-survive", encoding="utf-8")
    monkeypatch.setattr(audit.uuid, "uuid4", lambda: fixed_uuid)
    try:
        with pytest.raises(FileExistsError):
            write_evidence_bundle(bundle, root, signer=_signer(tmp_path))
        assert marker.read_text(encoding="utf-8") == "must-survive"
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def test_contract_coverage_is_exact_and_no_raw_table_name_is_persisted() -> None:
    clients, _, _ = _clients(snapshot=_snapshot(runtime_guard="disabled"))
    bundle = run_audit(TARGET, clients)
    payload = bundle.to_dict()
    assert {entry["table_type"] for entry in payload["data_plane"]["table_audits"]} == set(TABLE_TYPE_ALLOWLIST)
    assert "trustforge-connector-cache" not in json.dumps(payload, sort_keys=True)
    assert all(ref["content_sha256"].startswith("sha256:") for ref in payload["evidence_refs"])


def test_local_table_environment_cannot_redirect_approved_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTFORGE_CACHE_TABLE", "unrelated-table")
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_TABLE", "unrelated-table")
    bindings = audit.TableBinding.configured()
    assert [(item.table_type, item.table_name) for item in bindings] == [
        (table_type, audit._APPROVED_TABLE_NAMES[table_type])
        for table_type in TABLE_TYPE_ALLOWLIST
        if table_type in audit._APPROVED_TABLE_NAMES
    ]


def test_global_deadline_stops_before_ssm_or_dynamodb_collection() -> None:
    clients, ssm, dynamo = _clients()
    readings = iter((0.0, 0.0, 301.0))
    bundle = run_audit(TARGET, clients, now=lambda: next(readings), sleep=lambda _: None)
    assert bundle.overall_status is AuditStatus.PARTIAL
    assert bundle.blockers == ("audit-deadline-exhausted",)
    assert not ssm.send_calls
    assert not dynamo.calls


def test_static_collector_uses_bounded_streaming_subprocesses() -> None:
    assert "capture_output=True" not in audit.STATIC_SSM_COMMAND
    assert "stderr=subprocess.DEVNULL" in audit.STATIC_SSM_COMMAND
    assert "limit=8192" in audit.STATIC_SSM_COMMAND
    assert "--lines=100" in audit.STATIC_SSM_COMMAND


def test_release_comparison_preserves_match_mismatch_and_unknown_states() -> None:
    clients, _, _ = _clients(snapshot=_snapshot(runtime_guard="disabled"))
    mismatch = run_audit(TARGET, clients, expected_release="v0.27.38")
    assert mismatch.overall_status is AuditStatus.PARTIAL
    assert "release-mismatch" in mismatch.blockers
    comparison = {item.field: item.status.value for item in mismatch.control_plane.release_comparison}
    assert comparison["expected-release"] == "mismatch"
    assert comparison["manifest-digest"] == "unknown"
    assert comparison["config-digest"] == "unknown"
    assert comparison["unit-0-digest"] == "unknown"


def test_missing_aws_session_and_schema_drift_remain_fail_closed() -> None:
    ssm, dynamo = FakeSsm(), FakeDynamo()
    no_session = run_audit(TARGET, AwsAuditClients(FakeSts(error=RuntimeError("credential unavailable")), ssm, dynamo))
    assert no_session.overall_status is AuditStatus.BLOCKED
    assert no_session.blockers == ("sts-get-caller-identity-failed",)
    assert not ssm.send_calls and not dynamo.calls

    drift = FakeDynamo()
    drift._schemas = dict(drift._schemas)
    drift._schemas["trustforge-scheduler-runs"] = {"unexpected"}
    _, data, _, _, warnings = DynamoAuditReader(drift, AuditLimits.defaults()).collect()
    assert next(item for item in data.table_audits if item.table_type == "scheduler-run").status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert "dynamodb-scheduler-run-SchemaOrBudgetError" in warnings


def test_static_runtime_guard_mirrors_production_precedence_without_raw_values() -> None:
    command = audit.STATIC_SSM_COMMAND
    assert "TRUSTFORGE_RUNTIME_SWITCH" in command
    assert "TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS" in command
    assert "runtime_guard_state(service_env)" in command
    assert '"runtime_guard":runtime_guard_state(service_env)' in command
    assert "hermes-cycle.service" in command
    assert 'environment_values("hermes-cycle.service")' in command
    assert "trustforge-analysis-flow.service" in command
    assert "os.environ.get" not in command
    assert "TRUSTFORGE_COST_LEDGER_TABLE" in command
    assert "manifest.json" in command


def test_admin_config_overrides_unset_production_default_but_not_explicit_runtime_stop() -> None:
    database = FakeDynamo(admin_enabled=True)
    clients, _, _ = _clients(snapshot=_snapshot(runtime_guard="unknown"), dynamo=database)
    enabled = run_audit(TARGET, clients, expected_release="v0.27.37")
    assert enabled.control_plane.effective_control.effective is ControlState.ENABLED

    clients, _, _ = _clients(snapshot=_snapshot(runtime_guard="disabled"), dynamo=database)
    stopped = run_audit(TARGET, clients, expected_release="v0.27.37")
    assert stopped.control_plane.effective_control.effective is ControlState.DISABLED


def test_dynamodb_request_budget_is_reserved_before_each_call() -> None:
    defaults = AuditLimits.defaults()
    budgets = dict(defaults.read_budgets)
    budgets["dynamodb"] = ReadBudget(2, 200, 524_288, 10, 2)
    limits = AuditLimits(60, budgets)
    database = FakeDynamo()
    with pytest.raises(audit.AuditPartial, match="dynamodb-request-budget-exhausted"):
        DynamoAuditReader(database, limits).collect()
    assert [operation for operation, _ in database.calls] == ["describe_table", "describe_time_to_live"]


def test_malformed_dynamodb_rows_do_not_count_as_complete() -> None:
    database = FakeDynamo(malformed_table="trustforge-scheduler-runs")
    _, data, _, _, warnings = DynamoAuditReader(database, AuditLimits.defaults()).collect()
    scheduler = next(item for item in data.table_audits if item.table_type == "scheduler-run")
    assert scheduler.status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert "dynamodb-scheduler-run-SchemaOrBudgetError" in warnings


def test_strict_dynamodb_row_validation_rejects_negative_time_and_unsafe_optional_fields() -> None:
    assert audit._decode_item(
        {"source_id": {"S": "coingecko"}, "coin": {"S": "BTC"}, "fetched_at": {"N": "-1"}, "ttl": {"N": "2"}},
        "connector-cache",
    ) is None
    assert audit._decode_item(
        {"run_id": {"S": "run-1"}, "ts": {"S": "not-a-timestamp"}, "status": {"S": "success"}},
        "scheduler-run",
    ) is None
    assert audit._decode_item(
        {"run_id": {"S": "run-1"}, "ts": {"S": "2026-07-30T00:00:00Z"}, "status": {"S": "success"}, "error_class": {"S": "sk-0123456789abcdef"}},
        "scheduler-run",
    ) is None


def test_non_dry_cli_requires_human_approval_before_client_creation(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fail_if_called(*_: Any, **__: Any) -> Any:
        raise AssertionError("AWS clients must not be created without approval")

    monkeypatch.setattr(cli, "create_aws_clients", fail_if_called)
    output_dir = Path(__file__).resolve().parents[1] / "out" / "audits" / "hermes" / "approval-required"
    result = cli.main(["--region", REGION, "--instance-id", INSTANCE, "--output-dir", str(output_dir)])
    assert result == 5
    assert json.loads(capsys.readouterr().err) == {"status": "internal-failure", "error_class": "AuditContractError"}


def _valid_approval_bundle(
    tmp_path: Path, *, output_dir: Path, expected_release: str | None = None
) -> dict[str, Path]:
    """Build four independently Ed25519-signed approval attestations (one per
    role) plus their shared public verification keyring, all bound to
    ``TARGET``/``output_dir``/the current ``STATIC_SSM_COMMAND_DIGEST`` --
    this is the CLI-integration counterpart of
    tests/test_hermes_audit_approval.py's direct unit coverage of
    ``validate_approval_bundle``.
    """
    now = datetime.now(timezone.utc)
    issued_at = (now - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    expires_at = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    verification_keys: dict[str, bytes] = {}
    paths: dict[str, Path] = {}
    for role in ("ceo", "cpo", "ciso", "operator"):
        private = Ed25519PrivateKey.generate()
        private_bytes = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        public_bytes = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        key_id = f"{role}-test-key"
        verification_keys[key_id] = public_bytes
        attestation = sign_approval_attestation(
            role=role,
            region=TARGET.region,
            instance_id=TARGET.instance_id,
            expected_release=expected_release,
            output_dir=str(output_dir),
            static_ssm_command_sha256=audit.STATIC_SSM_COMMAND_DIGEST,
            actor=f"{role}-actor",
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=f"{role}-nonce-{uuid.uuid4()}",
            key_id=key_id,
            private_key=private_bytes,
        )
        path = tmp_path / f"{role}-approval.json"
        path.write_text(json.dumps(attestation.to_dict()), encoding="utf-8")
        paths[role] = path
    keyring_path = tmp_path / "approval-verification-keyring.json"
    keyring_path.write_text(json.dumps({
        "verification_keys": {key_id: key.hex() for key_id, key in verification_keys.items()}
    }), encoding="utf-8")
    paths["verification_keyring"] = keyring_path
    return paths


def _approval_flags(bundle: dict[str, Path]) -> list[str]:
    return [
        "--ceo-approval", str(bundle["ceo"]),
        "--cpo-approval", str(bundle["cpo"]),
        "--ciso-approval", str(bundle["ciso"]),
        "--operator-approval", str(bundle["operator"]),
        "--approval-verification-keyring", str(bundle["verification_keyring"]),
    ]


def test_non_dry_cli_requires_signing_keyring_before_client_creation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def fail_if_called(*_: Any, **__: Any) -> Any:
        raise AssertionError("AWS clients must not be created without a signing keyring")

    monkeypatch.setattr(cli, "create_aws_clients", fail_if_called)
    output_dir = Path(__file__).resolve().parents[1] / "out" / "audits" / "hermes" / "signing-keyring-required"
    bundle = _valid_approval_bundle(tmp_path, output_dir=output_dir)
    result = cli.main([
        "--region", REGION, "--instance-id", INSTANCE, "--output-dir", str(output_dir),
        *_approval_flags(bundle),
        "--approval-nonce-ledger-dir", str(tmp_path / "approval-nonce-ledger"),
    ])
    assert result == 5
    assert json.loads(capsys.readouterr().err) == {"status": "internal-failure", "error_class": "AuditContractError"}


@pytest.mark.parametrize("missing_flag", ["--ceo-approval", "--cpo-approval", "--ciso-approval", "--operator-approval", "--approval-verification-keyring"])
def test_non_dry_cli_requires_all_four_approvals_before_client_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], missing_flag: str
) -> None:
    """Plan 3.3 case #14: omitting any single one of the four
    ``--xxx-approval`` flags (or the verification keyring) must prevent
    ``create_aws_clients`` from ever being called -- a partially-supplied
    approval bundle is exactly as unauthorized as none at all.
    """
    def fail_if_called(*_: Any, **__: Any) -> Any:
        raise AssertionError(f"AWS clients must not be created without {missing_flag}")

    monkeypatch.setattr(cli, "create_aws_clients", fail_if_called)
    output_dir = Path(__file__).resolve().parents[1] / "out" / "audits" / "hermes" / "partial-approval-rejected"
    bundle = _valid_approval_bundle(tmp_path, output_dir=output_dir)
    args = [
        "--region", REGION, "--instance-id", INSTANCE, "--output-dir", str(output_dir),
        *_approval_flags(bundle),
        "--approval-nonce-ledger-dir", str(tmp_path / "approval-nonce-ledger"),
    ]
    # drop the flag under test and its value from the argument list.
    index = args.index(missing_flag)
    del args[index:index + 2]
    result = cli.main(args)
    assert result == 5
    assert json.loads(capsys.readouterr().err) == {"status": "internal-failure", "error_class": "AuditContractError"}


def test_dry_run_cli_never_creates_the_signing_nonce_ledger(tmp_path: Path) -> None:
    output_dir = Path(__file__).resolve().parents[1] / "out" / "audits" / "hermes" / "dry-run-no-ledger"
    nonce_dir = tmp_path / "nonce-ledger"
    result = cli.main([
        "--region", REGION, "--instance-id", INSTANCE, "--output-dir", str(output_dir),
        "--nonce-ledger-dir", str(nonce_dir), "--dry-run",
    ])
    assert result == 0
    assert not nonce_dir.exists()


def _write_private_keyring(path: Path, *, mode: int = 0o600) -> str:
    """Write a valid private keyring JSON (per secure_keyring's contract) and
    return its key_id, so _load_signer tests only need to vary the file's own
    symlink/permission properties, not the key material inside it.
    """
    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public_bytes = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_id = "test-signing-key"
    path.write_text(json.dumps({
        "key_id": key_id,
        "private_key": private_bytes.hex(),
        "verification_keys": {key_id: public_bytes.hex()},
    }), encoding="utf-8")
    os.chmod(path, mode)
    return key_id


def test_load_signer_rejects_a_symlinked_keyring_file(tmp_path: Path) -> None:
    real = tmp_path / "real-keyring.json"
    _write_private_keyring(real)
    link = tmp_path / "signing-keyring.json"
    os.symlink(real, link)
    with pytest.raises(SecureKeyringError):
        cli._load_signer(link, tmp_path / "nonce-ledger")


def test_load_signer_rejects_a_loosely_permissioned_keyring_file(tmp_path: Path) -> None:
    keyring_path = tmp_path / "signing-keyring.json"
    _write_private_keyring(keyring_path, mode=0o644)
    with pytest.raises(SecureKeyringError):
        cli._load_signer(keyring_path, tmp_path / "nonce-ledger")


def test_output_validation_rejects_symlinked_ancestors() -> None:
    root = Path(__file__).resolve().parents[1] / "out" / "audits" / "hermes"
    root.mkdir(parents=True, exist_ok=True)
    link = root / "symlinked-output"
    try:
        os.symlink("/tmp", link)
        with pytest.raises(AuditContractError):
            dry_run_plan(TARGET, link / "nested", AuditLimits.defaults(), None)
    finally:
        if link.is_symlink():
            link.unlink()


# ---------------------------------------------------------------------------
# Phase 2: STATIC_SSM_COMMAND dead-letter SQLite + table-name resolution
# (docs/plans/PLAN-HERMES-PR1197-REMEDIATION-2026-07-31.md section 2).
# ---------------------------------------------------------------------------

def _load_ssm_functions() -> dict[str, Any]:
    """Extract and exec() the pure function/constant definitions embedded in
    STATIC_SSM_COMMAND (everything before the driver marker), so the
    heredoc's own classify_error/dead_letters/config_value can be
    behaviourally unit tested without actually invoking SSM RunCommand.
    """
    body = audit.STATIC_SSM_COMMAND.split("python3 - <<'PY'\n", 1)[1]
    body = body.split("# ---- driver:", 1)[0]
    namespace: dict[str, Any] = {}
    exec(compile(body, "<static-ssm-command>", "exec"), namespace)
    return namespace


def _write_dead_letter_db(path: Path, rows: list[tuple[Any, ...]], *, wal: bool = True) -> None:
    connection = sqlite3.connect(str(path))
    try:
        if wal:
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE analysis_dead_letters ("
            "job_id TEXT, stage TEXT, coin TEXT, mode TEXT, question TEXT, "
            "snapshot_id TEXT, attempts INTEGER, error TEXT, failed_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO analysis_dead_letters "
            "(job_id, stage, coin, mode, question, snapshot_id, attempts, error, failed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def test_classify_error_recognizes_allowlisted_exception_prefix_and_reduces_everything_else() -> None:
    classify_error = _load_ssm_functions()["classify_error"]
    assert classify_error("TimeoutError: connection reset") == "TimeoutError"
    assert classify_error("BedrockThrottlingException: throttled") == "BedrockThrottlingException"
    # Free-form text -- including anything secret-like -- never survives as-is.
    assert classify_error("sk-0123456789abcdef leaked in traceback") == "unclassified-error"
    assert classify_error(None) == "unclassified-error"
    assert classify_error("") == "unclassified-error"


def test_config_value_distinguishes_genuinely_absent_from_present_but_invalid_table_name() -> None:
    """Bug 1 (2026-07-31 adversarial codex review, CEO-confirmed): a remote
    table-name flag that was genuinely never set and one that WAS set but to
    a syntactically-invalid value must not collapse into the identical
    "absent" sentinel -- doing so lets a present-but-malformed override be
    miscategorized upstream as "not configured" and silently fall back to
    the static approved table (see the TableBinding.configured() tests
    below for the end-to-end consequence).
    """
    config_value = _load_ssm_functions()["config_value"]
    # Genuinely absent: the key never appears in service_env at all.
    assert config_value("TRUSTFORGE_CACHE_TABLE", {}) == "absent"
    # Present-but-empty is NOT the same as genuinely absent -- a systemd unit
    # misconfigured with e.g. `Environment=TRUSTFORGE_CACHE_TABLE=` (empty
    # value) still populates the dict key, distinct from the key being
    # missing entirely, and must fail closed exactly like any other
    # syntactically-invalid present value.
    assert (
        config_value("TRUSTFORGE_CACHE_TABLE", {"TRUSTFORGE_CACHE_TABLE": ""})
        == audit._TABLE_NAME_INVALID_SENTINEL
    )
    for malformed in ("evil/table", " HermesCache", "ab", "a" * 256, "trustforge`; rm -rf /`"):
        sentinel = config_value("TRUSTFORGE_CACHE_TABLE", {"TRUSTFORGE_CACHE_TABLE": malformed})
        assert sentinel not in ("absent", malformed)
        assert sentinel == audit._TABLE_NAME_INVALID_SENTINEL
    # Non-table-name flags are unaffected: still the plain configured/absent
    # tri-state, no new sentinel leaks into unrelated flags.
    assert config_value("TRUSTFORGE_HERMES_AUTONOMY_ENABLED", {"TRUSTFORGE_HERMES_AUTONOMY_ENABLED": "1"}) == "configured"
    assert config_value("TRUSTFORGE_HERMES_AUTONOMY_ENABLED", {}) == "absent"


def test_dead_letter_sqlite_positive_reads_existing_rows_with_safe_field_mapping(tmp_path: Path) -> None:
    dead_letters = _load_ssm_functions()["dead_letters"]
    db_path = tmp_path / "trustforge.sqlite3"
    _write_dead_letter_db(db_path, [
        ("job-1", "fetch", "BTC", "m", "q", "s", 2, "TimeoutError: connection reset", "2026-07-30T00:00:00Z"),
    ])
    result = dead_letters(str(db_path), 32)
    assert result == {
        "available": True, "truncated": False,
        "rows": [{
            "job_id": "job-1", "coin": "BTC", "stage": "fetch", "attempt": 2,
            "error_class": "TimeoutError", "retry_state": "dead-lettered", "release_identity": None,
        }],
    }


def test_dead_letter_sqlite_empty_table_is_available_and_complete_not_insufficient(tmp_path: Path) -> None:
    dead_letters = _load_ssm_functions()["dead_letters"]
    db_path = tmp_path / "trustforge.sqlite3"
    _write_dead_letter_db(db_path, [])
    remote = dead_letters(str(db_path), 32)
    assert remote == {"available": True, "truncated": False, "rows": []}
    audit_row, letters, _, warning = audit._dead_letter_evidence(remote, ReadBudget(12, 32, 1_048_576, 5, 1))
    assert audit_row.status is AuditStatus.COMPLETE
    assert letters == ()
    assert warning is None


def test_dead_letter_sqlite_missing_file_is_insufficient_evidence_without_crash(tmp_path: Path) -> None:
    dead_letters = _load_ssm_functions()["dead_letters"]
    assert dead_letters(str(tmp_path / "does-not-exist.sqlite3"), 32) is None
    audit_row, letters, _, warning = audit._dead_letter_evidence(
        {"available": False, "truncated": False, "rows": []}, ReadBudget(12, 32, 1_048_576, 5, 1),
    )
    assert audit_row.status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert letters == ()
    assert warning == "dynamodb-durable-dead-letter-insufficient-evidence"


def test_dead_letter_sqlite_schema_drift_missing_table_is_insufficient_evidence(tmp_path: Path) -> None:
    dead_letters = _load_ssm_functions()["dead_letters"]
    db_path = tmp_path / "trustforge.sqlite3"
    connection = sqlite3.connect(str(db_path))
    connection.execute("CREATE TABLE unrelated_table (x TEXT)")
    connection.commit()
    connection.close()
    assert dead_letters(str(db_path), 32) is None


def test_dead_letter_error_text_never_leaves_the_ssm_script_process(tmp_path: Path) -> None:
    dead_letters = _load_ssm_functions()["dead_letters"]
    db_path = tmp_path / "trustforge.sqlite3"
    secret = "sk-0123456789abcdef"
    _write_dead_letter_db(db_path, [
        ("job-1", "fetch", "BTC", "m", "q", "s", 1, f"ValueError: password={secret}", "2026-07-30T00:00:00Z"),
    ])
    result = dead_letters(str(db_path), 32)
    assert result["rows"][0]["error_class"] == "ValueError"
    assert secret not in json.dumps(result)

    # Even if a compromised remote somehow reported the secret directly as
    # error_class (bypassing classify_error), Python-side contract validation
    # drops that one row rather than persisting it anywhere.
    letters = audit._dead_letters([{
        "job_id": "job-1", "coin": "BTC", "stage": "fetch", "attempt": 1,
        "error_class": secret, "retry_state": "dead-lettered", "release_identity": None,
    }])
    assert letters == ()


def test_dead_letter_row_limit_truncates_and_orders_by_failed_at_desc(tmp_path: Path) -> None:
    dead_letters = _load_ssm_functions()["dead_letters"]
    db_path = tmp_path / "trustforge.sqlite3"
    rows = [
        (f"job-{i}", "fetch", "BTC", "m", "q", "s", 1, "TimeoutError: x", f"2026-07-{i:02d}T00:00:00Z")
        for i in range(1, 35)
    ]
    _write_dead_letter_db(db_path, rows)
    result = dead_letters(str(db_path), 32)
    assert result["truncated"] is True
    assert len(result["rows"]) == 32
    assert result["rows"][0]["job_id"] == "job-34"

    # A snapshot claiming more rows than the local-io item_limit is a fatal
    # structural violation, not a soft warning: this script always caps its
    # own output at `limit`, so seeing more means the snapshot was tampered
    # with or the schema drifted.
    row = {
        "job_id": "job", "coin": "BTC", "stage": "fetch", "attempt": 1,
        "error_class": "TimeoutError", "retry_state": "dead-lettered", "release_identity": None,
    }
    oversized = _snapshot(dead_letters=_dead_letters_section(rows=[row] * 33))
    with pytest.raises(AuditContractError):
        audit._validate_ssm_snapshot(oversized)


def test_dead_letter_malformed_attempt_is_dropped_row_by_row_not_fatal() -> None:
    rows = [
        {
            "job_id": "job-good", "coin": "BTC", "stage": "fetch", "attempt": 2,
            "error_class": "TimeoutError", "retry_state": "dead-lettered", "release_identity": None,
        },
        {
            "job_id": "job-bad", "coin": "BTC", "stage": "fetch", "attempt": -1,
            "error_class": "TimeoutError", "retry_state": "dead-lettered", "release_identity": None,
        },
    ]
    letters = audit._dead_letters(rows)
    assert len(letters) == 1
    assert letters[0].coin == "BTC"
    audit_row, letters, _, warning = audit._dead_letter_evidence(
        {"available": True, "truncated": False, "rows": rows}, ReadBudget(12, 32, 1_048_576, 5, 1),
    )
    assert audit_row.status is AuditStatus.COMPLETE
    assert len(letters) == 1
    assert warning is None


def test_dead_letter_over_byte_budget_degrades_to_insufficient_evidence_not_crash() -> None:
    rows = [
        {
            "job_id": f"job-{i}", "coin": "BTC", "stage": "fetch", "attempt": 1,
            "error_class": "TimeoutError", "retry_state": "dead-lettered", "release_identity": None,
        }
        for i in range(20)
    ]
    tiny_budget = ReadBudget(12, 32, 64, 5, 1)
    audit_row, letters, ref, warning = audit._dead_letter_evidence(
        {"available": True, "truncated": False, "rows": rows}, tiny_budget,
    )
    assert audit_row.status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert letters == ()
    assert warning == "local-io-byte-budget-exceeded"
    assert ref.content_sha256.startswith("sha256:")


def test_dead_letter_sqlite_read_respects_busy_timeout_under_a_concurrent_writer_lock(tmp_path: Path) -> None:
    dead_letters = _load_ssm_functions()["dead_letters"]
    db_path = tmp_path / "trustforge.sqlite3"
    _write_dead_letter_db(db_path, [
        ("job-1", "fetch", "BTC", "m", "q", "s", 1, "TimeoutError: x", "2026-07-30T00:00:00Z"),
    ])
    released = threading.Event()

    def hold_exclusive_lock(hold_seconds: float) -> None:
        writer = sqlite3.connect(str(db_path), timeout=5)
        writer.execute("PRAGMA locking_mode=EXCLUSIVE")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE analysis_dead_letters SET attempts = attempts")
        writer.execute("UPDATE analysis_dead_letters SET attempts = attempts")
        time.sleep(hold_seconds)
        writer.commit()
        writer.close()
        released.set()

    thread = threading.Thread(target=hold_exclusive_lock, args=(0.3,))
    thread.start()
    time.sleep(0.05)  # let the writer actually acquire the exclusive lock first
    started = time.monotonic()
    try:
        result = dead_letters(str(db_path), 32)
    finally:
        thread.join(timeout=5)
    elapsed = time.monotonic() - started
    # The read-only connection's own busy_timeout (1000ms, shorter than the
    # audit's overall SSM read budget) must be honoured: it waits for the
    # writer to release rather than failing immediately, but the wait stays
    # bounded well under the SSM command's own timeout.
    assert released.is_set()
    assert elapsed < 2.0
    assert result == {"available": True, "truncated": False, "rows": [{
        "job_id": "job-1", "coin": "BTC", "stage": "fetch", "attempt": 1,
        "error_class": "TimeoutError", "retry_state": "dead-lettered", "release_identity": None,
    }]}


def test_dead_letter_sqlite_lock_held_longer_than_busy_timeout_is_fail_closed(tmp_path: Path) -> None:
    dead_letters = _load_ssm_functions()["dead_letters"]
    db_path = tmp_path / "trustforge.sqlite3"
    _write_dead_letter_db(db_path, [
        ("job-1", "fetch", "BTC", "m", "q", "s", 1, "TimeoutError: x", "2026-07-30T00:00:00Z"),
    ])
    released = threading.Event()

    def hold_exclusive_lock() -> None:
        writer = sqlite3.connect(str(db_path), timeout=5)
        writer.execute("PRAGMA locking_mode=EXCLUSIVE")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE analysis_dead_letters SET attempts = attempts")
        writer.execute("UPDATE analysis_dead_letters SET attempts = attempts")
        released.wait(5)
        writer.commit()
        writer.close()

    thread = threading.Thread(target=hold_exclusive_lock)
    thread.start()
    time.sleep(0.05)
    try:
        started = time.monotonic()
        result = dead_letters(str(db_path), 32)
        elapsed = time.monotonic() - started
        # busy_timeout is 1000ms: a lock held well beyond that must fail
        # closed (None), never hang or raise, and must not block much longer
        # than the configured timeout.
        assert result is None
        assert elapsed < 3.0
    finally:
        released.set()
        thread.join(timeout=5)


def test_table_binding_configured_adopts_a_remote_value_that_matches_its_own_approved_name() -> None:
    remote_config = {name: "absent" for name in audit._TABLE_NAME_FLAGS}
    remote_config["TRUSTFORGE_CACHE_TABLE"] = audit._APPROVED_TABLE_NAMES["connector-cache"]
    bindings = {item.table_type: item.table_name for item in audit.TableBinding.configured(remote_config=remote_config)}
    assert bindings["connector-cache"] == audit._APPROVED_TABLE_NAMES["connector-cache"]
    assert bindings["admin-config"] == audit._APPROVED_TABLE_NAMES["admin-config"]
    # Untouched flags fall back to the static approved table, unchanged.
    assert bindings["scheduler-run"] == audit._APPROVED_TABLE_NAMES["scheduler-run"]
    assert bindings["cost-ledger"] == audit._APPROVED_TABLE_NAMES["cost-ledger"]


def test_table_binding_configured_fails_closed_when_remote_value_is_valid_but_not_approved() -> None:
    """CEO ruling 2026-07-31: a syntactically valid remote table name that is
    not in `_APPROVED_TABLE_NAMES.values()` must never be adopted, and must
    never fall back to silently continuing to read the old static table --
    the binding for that type is dropped entirely (fail-closed).
    """
    remote_config = {name: "absent" for name in audit._TABLE_NAME_FLAGS}
    # Syntactically valid (passes TABLE_NAME_RE) but not connector-cache's
    # approved name -- a plausible "production accidentally repointed the env
    # var at a different, still legitimately-named table" scenario.
    remote_config["TRUSTFORGE_CACHE_TABLE"] = "trustforge-some-other-table"
    bindings = audit.TableBinding.configured(remote_config=remote_config)
    types = {item.table_type for item in bindings}
    assert "connector-cache" not in types
    assert "admin-config" not in types
    assert {"scheduler-run", "cost-ledger"}.issubset(types)

    # End-to-end: the dropped binding must degrade only that table to
    # insufficient evidence with a distinct warning, never silently keep
    # reading the static table, and never crash.
    reader = DynamoAuditReader(FakeDynamo(), AuditLimits.defaults(), bindings=bindings)
    _, data, _, refs, warnings = reader.collect()
    cache = next(item for item in data.table_audits if item.table_type == "connector-cache")
    admin = next(item for item in data.table_audits if item.table_type == "admin-config")
    assert cache.status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert admin.status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert "dynamodb-connector-cache-table-name-not-approved" in warnings
    assert "dynamodb-admin-config-table-name-not-approved" in warnings
    assert "trustforge-some-other-table" not in json.dumps(
        {"warnings": list(warnings), "refs": [dataclasses.asdict(ref) for ref in refs]}, sort_keys=True,
    )


def test_table_binding_configured_fails_closed_when_remote_value_is_present_but_syntactically_invalid() -> None:
    """Bug 1 end-to-end (2026-07-31 adversarial codex review, CEO-confirmed):
    a remote table-name flag that IS set but to a value that fails
    TABLE_NAME_RE must fail closed exactly like a syntactically valid but
    non-approved value -- it must NEVER be miscategorized as "genuinely
    absent" and silently fall back to reading the static approved table.
    """
    config_value = _load_ssm_functions()["config_value"]
    sentinel = config_value("TRUSTFORGE_CACHE_TABLE", {"TRUSTFORGE_CACHE_TABLE": "evil/table"})

    snapshot = _snapshot(table_names={"TRUSTFORGE_CACHE_TABLE": sentinel})
    validated = audit._validate_ssm_snapshot(snapshot)  # must not raise: sentinel is schema-valid

    bindings = audit.TableBinding.configured(remote_config=validated["config"])
    types = {item.table_type for item in bindings}
    assert "connector-cache" not in types
    assert "admin-config" not in types
    assert {"scheduler-run", "cost-ledger"}.issubset(types)

    # End-to-end: the dropped binding degrades only that table to
    # insufficient evidence, never silently keeps reading the static table.
    reader = DynamoAuditReader(FakeDynamo(), AuditLimits.defaults(), bindings=bindings)
    _, data, _, _, warnings = reader.collect()
    cache = next(item for item in data.table_audits if item.table_type == "connector-cache")
    admin = next(item for item in data.table_audits if item.table_type == "admin-config")
    assert cache.status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert admin.status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert "dynamodb-connector-cache-table-name-not-approved" in warnings
    assert "dynamodb-admin-config-table-name-not-approved" in warnings


def test_table_binding_configured_fails_closed_when_remote_value_is_present_but_empty() -> None:
    """Bug 1 sibling end-to-end (2026-07-31 adversarial codex re-review): a
    remote table-name flag that IS a key in service_env but whose value is
    the empty string (e.g. a systemd unit misconfigured with
    `Environment=TRUSTFORGE_CACHE_TABLE=`) must fail closed exactly like any
    other present-but-invalid value -- it must NEVER be miscategorized as
    "genuinely absent" and silently fall back to reading the static
    approved table. This drives the value through config_value() itself
    (not a manually constructed sentinel) to prove the whole pipeline --
    config_value -> _validate_ssm_snapshot -> TableBinding.configured --
    closes the gap end to end.
    """
    config_value = _load_ssm_functions()["config_value"]
    sentinel = config_value("TRUSTFORGE_CACHE_TABLE", {"TRUSTFORGE_CACHE_TABLE": ""})
    assert sentinel == audit._TABLE_NAME_INVALID_SENTINEL

    snapshot = _snapshot(table_names={"TRUSTFORGE_CACHE_TABLE": sentinel})
    validated = audit._validate_ssm_snapshot(snapshot)  # must not raise: sentinel is schema-valid

    bindings = audit.TableBinding.configured(remote_config=validated["config"])
    types = {item.table_type for item in bindings}
    assert "connector-cache" not in types
    assert "admin-config" not in types
    assert {"scheduler-run", "cost-ledger"}.issubset(types)

    # End-to-end: the dropped binding degrades only that table to
    # insufficient evidence, never silently keeps reading the static table.
    reader = DynamoAuditReader(FakeDynamo(), AuditLimits.defaults(), bindings=bindings)
    _, data, _, _, warnings = reader.collect()
    cache = next(item for item in data.table_audits if item.table_type == "connector-cache")
    admin = next(item for item in data.table_audits if item.table_type == "admin-config")
    assert cache.status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert admin.status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert "dynamodb-connector-cache-table-name-not-approved" in warnings
    assert "dynamodb-admin-config-table-name-not-approved" in warnings


def test_dynamo_audit_reader_empty_bindings_tuple_is_not_replaced_by_static_defaults() -> None:
    """Bug 4 (2026-07-31 adversarial codex third-pass review, CEO-confirmed):
    `TableBinding.configured(remote_config=...)` legitimately returns an empty
    tuple when every `_TABLE_NAME_FLAGS` entry fails closed. `bindings=()` is
    falsy, so `self._bindings = bindings or TableBinding.configured()` used to
    treat "caller explicitly supplied an empty tuple" the same as "caller
    passed nothing" and silently re-populated it with the full set of static
    approved bindings -- inverting fail-closed into "the more broken the
    remote config, the healthier the tool looks." Only the true default
    (`bindings=None`, not supplied at all) may fall back to the static set.
    """
    reader = DynamoAuditReader(FakeDynamo(), AuditLimits.defaults(), bindings=())
    assert reader._bindings == ()
    _, data, _, _, warnings = reader.collect()
    for item in data.table_audits:
        if item.table_type == "durable-dead-letter":
            continue
        assert item.status is AuditStatus.INSUFFICIENT_EVIDENCE
    assert len(data.table_audits) == len(TABLE_TYPE_ALLOWLIST)


def test_run_audit_end_to_end_when_all_table_name_flags_fail_closed_never_falls_back_to_static_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug 4 end-to-end: drive the same scenario through `run_audit()` with a
    remote SSM snapshot where all three `_TABLE_NAME_FLAGS` are present but
    syntactically invalid, so `TableBinding.configured(remote_config=...)`
    returns `()`. `run_audit()` must report INSUFFICIENT_EVIDENCE for every
    non-dead-letter table type, never silently recover by reading the old
    static tables.
    """
    config_value = _load_ssm_functions()["config_value"]
    table_names = {
        flag: config_value(flag, {flag: "evil/table"}) for flag in audit._TABLE_NAME_FLAGS
    }
    for sentinel in table_names.values():
        assert sentinel == audit._TABLE_NAME_INVALID_SENTINEL
    snapshot = _snapshot(table_names=table_names)
    validated = audit._validate_ssm_snapshot(snapshot)
    assert audit.TableBinding.configured(remote_config=validated["config"]) == ()

    clients, _, _ = _clients(snapshot=snapshot)
    bundle = run_audit(TARGET, clients, expected_release="v0.27.37")
    for item in bundle.data_plane.table_audits:
        if item.table_type == "durable-dead-letter":
            continue
        assert item.status is AuditStatus.INSUFFICIENT_EVIDENCE


def test_local_environment_still_cannot_override_when_ssm_snapshot_also_supplies_an_approved_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTFORGE_CACHE_TABLE", "attacker-controlled-table")
    remote_config = {name: "absent" for name in audit._TABLE_NAME_FLAGS}
    remote_config["TRUSTFORGE_CACHE_TABLE"] = audit._APPROVED_TABLE_NAMES["connector-cache"]
    bindings = {item.table_type: item.table_name for item in audit.TableBinding.configured(remote_config=remote_config)}
    assert bindings["connector-cache"] == audit._APPROVED_TABLE_NAMES["connector-cache"]
    assert "attacker-controlled-table" not in bindings.values()


def test_table_name_with_shell_characters_or_overlong_value_is_rejected_by_snapshot_validation() -> None:
    for bad_value in ("trustforge`; rm -rf /`", "a" * 256, "ab"):
        snapshot = _snapshot(table_names={"TRUSTFORGE_CACHE_TABLE": bad_value})
        with pytest.raises(AuditContractError):
            audit._validate_ssm_snapshot(snapshot)


def test_config_schema_drift_is_integrity_failure() -> None:
    snapshot = _snapshot()
    snapshot["config"] = dict(snapshot["config"])
    snapshot["config"]["UNEXPECTED_FLAG"] = "configured"
    with pytest.raises(AuditContractError):
        audit._validate_ssm_snapshot(snapshot)

    clients, _, _ = _clients(snapshot=snapshot)
    bundle = run_audit(TARGET, clients)
    assert bundle.overall_status is AuditStatus.INTEGRITY_FAILURE
