"""Mocked regressions for the bounded, read-only Hermes audit adapter."""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from scripts import hermes_production_audit as cli
from trustforge import hermes_audit as audit
from trustforge.hermes_audit import (
    AwsAuditClients,
    DynamoAuditReader,
    _bounded_ssm_snapshot,
    dry_run_plan,
    run_audit,
    write_evidence_bundle,
)
from trustforge.hermes_audit_contracts import (
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

REGION = "ap-southeast-2"
INSTANCE = "i-0152b70368358a81c"
TARGET = AuditTarget(REGION, INSTANCE)


def _snapshot(*, runtime_guard: str = "disabled", version: str = "v0.27.37") -> dict[str, Any]:
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
        "config": {name: "configured" for name in audit._CONFIG_ALLOWLIST},
        "release": {
            "version": version,
            "python": "3.11.9",
            "manifest_sha256": sha256_digest({"manifest": "fixture"}),
        },
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
    clients, _, _ = _clients(snapshot=_snapshot(runtime_guard="disabled"))
    bundle = run_audit(TARGET, clients, expected_release="v0.27.37")
    assert bundle.overall_status is AuditStatus.PARTIAL
    assert bundle.control_plane.effective_control.effective is ControlState.DISABLED
    assert bundle.control_plane.units[0].timer_next_monotonic_usec == 100
    assert bundle.control_plane.units[0].journal_error_count == 0
    assert all(item.ttl_status is ControlState.ENABLED for item in bundle.data_plane.table_audits if item.table_type != "durable-dead-letter")
    assert bundle.dead_letters == ()
    assert "dynamodb-durable-dead-letter-insufficient-evidence" in bundle.warnings


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


def test_writer_creates_verified_local_bundle_and_rejects_outside_output() -> None:
    clients, _, _ = _clients(snapshot=_snapshot(runtime_guard="disabled"))
    bundle = run_audit(TARGET, clients, expected_release="v0.27.37")
    root = Path(__file__).resolve().parents[1] / "out" / "audits" / "hermes"
    destination = write_evidence_bundle(bundle, root)
    try:
        evidence = destination / "evidence.json"
        summary = destination / "summary.md"
        checksums = (destination / "SHA256SUMS").read_text(encoding="utf-8")
        assert json.loads(evidence.read_text(encoding="utf-8"))["canonical_payload_sha256"] == bundle.canonical_payload_sha256
        assert hashlib.sha256(evidence.read_bytes()).hexdigest() in checksums
        assert hashlib.sha256(summary.read_bytes()).hexdigest() in checksums
        assert "does not authorize feature flags" in summary.read_text(encoding="utf-8")
        with pytest.raises(FileExistsError):
            write_evidence_bundle(bundle, root)
        assert not list(root.glob(f".{bundle.audit_id}.*.incomplete"))
    finally:
        shutil.rmtree(destination)
    with pytest.raises(AuditContractError):
        write_evidence_bundle(bundle, Path("/tmp/outside-audit"))


def test_writer_does_not_delete_preexisting_staging_collision(monkeypatch: pytest.MonkeyPatch) -> None:
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
            write_evidence_bundle(bundle, root)
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
