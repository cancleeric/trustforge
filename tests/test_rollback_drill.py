"""Tests for the hermetic release A/B rollback drill orchestrator (#877).

These exercise the drill producer end-to-end: it must drive the *existing*
deployment-control state machine through a full rollback-a lifecycle against
two loopback release services, produce a HMAC-signed ``rollback_drill``
GateReceipt bound to the drill's A/B digests, and emit a report whose bytes are
bound by the receipt's ``output_digest``. None of these tests may touch a real
production target, key, ledger, or host.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from run_rollback_drill import (  # noqa: E402
    DRILL_TARGET,
    GATE_NAME,
    GATE_RECEIPT_DOMAIN,
    REPORT_SCHEMA,
    SLO_A_HEALTH_RESTORED_SECONDS,
    SLO_ROLLBACK_RECONCILE_SECONDS,
    _InMemoryActivationLockBackend,
    build_gate_receipt,
    run,
    run_drill,
    write_drill_artifacts,
)
from trustforge.agent.shadow_contracts import canonical_json  # noqa: E402
from trustforge.deployment_evidence import GateReceipt  # noqa: E402


GATE_KEY = b"d" * 32
GATE_KEY_ID = "drill-gate-test-1"


def _run_full(tmp_path: Path) -> dict:
    return run(
        tmp_path,
        actor="test-ceo",
        reason="rollback drill test",
        gate_key=GATE_KEY,
        gate_key_id=GATE_KEY_ID,
    )


# ---------------------------------------------------------------------------
# 1. PASS path
# ---------------------------------------------------------------------------


def test_pass_path_produces_signed_receipt_and_bound_report(tmp_path):
    summary = _run_full(tmp_path)

    assert summary["result"] == "pass"
    assert summary["slo_pass"] is True
    assert summary["receipt_output_digest_match"] is True
    assert summary["gate_key_hex"] == GATE_KEY.hex()

    report_path = Path(summary["report_path"])
    receipt_path = Path(summary["receipt_path"])
    assert report_path == tmp_path / "rollback_drill_report.json"
    assert receipt_path == tmp_path / "rollback_drill_receipt.json"

    report = json.loads(report_path.read_text())
    receipt_raw = json.loads(receipt_path.read_text())

    assert report["schema"] == REPORT_SCHEMA
    assert report["gate"] == GATE_NAME
    assert report["result"] == "pass"

    # Receipt binds A/B digests, is zero-cost, and is the rollback_drill gate.
    assert receipt_raw["gate"] == GATE_NAME
    assert receipt_raw["result"] == "pass"
    assert receipt_raw["provider_calls"] == 0
    assert receipt_raw["cost_usd"] == 0.0
    assert receipt_raw["active_artifact_digest"] == report["active_artifact_digest"]
    assert receipt_raw["candidate_artifact_digest"] == report["candidate_artifact_digest"]
    assert receipt_raw["receipt_version"] == "trustforge.executable-gate-receipt/v1"
    assert receipt_raw["key_id"] == GATE_KEY_ID
    assert receipt_raw["nonce"]

    # output_digest binds the EXACT report file bytes (D1: no schema field added).
    expected_output_digest = "sha256:" + hashlib.sha256(
        report_path.read_bytes()
    ).hexdigest()
    assert receipt_raw["output_digest"] == expected_output_digest
    assert receipt_raw["output_digest"].startswith("sha256:")
    assert receipt_raw["command_digest"].startswith("sha256:")

    # executed/expires window is valid (executed <= now < expires, <= 24h span).
    executed = datetime.fromisoformat(receipt_raw["executed_at"])
    expires = datetime.fromisoformat(receipt_raw["expires_at"])
    assert executed.tzinfo is not None
    assert expires > executed
    assert expires - executed <= timedelta(hours=24)


def test_pass_path_receipt_hmac_signature_verifies(tmp_path):
    summary = _run_full(tmp_path)
    receipt_raw = json.loads(Path(summary["receipt_path"]).read_text())
    receipt = GateReceipt(**receipt_raw)
    expected = hmac.new(
        GATE_KEY,
        GATE_RECEIPT_DOMAIN + canonical_json(receipt.unsigned()),
        hashlib.sha256,
    ).hexdigest()
    assert hmac.compare_digest(receipt.signature, expected)

    # Tamper detection: flipping cost must break the signature.
    forged = dict(receipt_raw)
    forged["cost_usd"] = 1.0
    forged_receipt = GateReceipt(**forged)
    forged_expected = hmac.new(
        GATE_KEY,
        GATE_RECEIPT_DOMAIN + canonical_json(forged_receipt.unsigned()),
        hashlib.sha256,
    ).hexdigest()
    assert not hmac.compare_digest(forged["signature"], forged_expected)


def test_pass_path_receipt_file_permissions_are_safe(tmp_path):
    summary = _run_full(tmp_path)
    info = os.lstat(summary["receipt_path"])
    assert info.st_uid == os.geteuid()
    assert stat.S_IMODE(info.st_mode) & 0o077 == 0


# ---------------------------------------------------------------------------
# 2. regression -> rollback
# ---------------------------------------------------------------------------


def test_regression_injection_and_rollback_restores_disabled(tmp_path):
    report = run_drill(
        tmp_path,
        actor="test-ceo",
        reason="regression rollback path",
    )

    # The candidate was flipped to fail and the operator stopped + rolled back.
    assert report["health"]["candidate_after_injection"] == 503
    assert report["from_phase"] == "canary"
    assert report["to_phase"] == "disabled"
    assert report["transition"] == "canary->stopped->disabled"
    assert report["result"] == "pass"

    # The replay captured distinct behavioral digests for A vs B baseline.
    assert (
        report["replay"]["active_output_digest"]
        != report["replay"]["candidate_output_digest"]
    )

    # Both rollback SLOs held (D2).
    assert (
        report["slo"]["rollback_reconcile_seconds"]["observed"]
        <= SLO_ROLLBACK_RECONCILE_SECONDS
    )
    assert (
        report["slo"]["a_health_restored_seconds"]["observed"]
        <= SLO_A_HEALTH_RESTORED_SECONDS
    )
    assert report["slo"]["rollback_reconcile_seconds"]["budget"] == 300
    assert report["slo"]["a_health_restored_seconds"]["budget"] == 30


def test_rollback_reconcile_latency_measured_around_prepare_to_complete(tmp_path):
    """rollback_reconcile must span prepare(rollback-a) -> complete(rollback-a)."""
    report = run_drill(tmp_path, actor="lat", reason="latency probe")
    latency = report["latency"]
    reconcile = (
        latency["rollback_complete_monotonic"]
        - latency["rollback_prepare_start_monotonic"]
    )
    assert (
        pytest.approx(reconcile, rel=1e-3, abs=1e-3)
        == report["slo"]["rollback_reconcile_seconds"]["observed"]
    )
    a_health = (
        latency["a_health_confirmed_monotonic"]
        - latency["rollback_complete_monotonic"]
    )
    assert (
        pytest.approx(a_health, rel=1e-3, abs=1e-3)
        == report["slo"]["a_health_restored_seconds"]["observed"]
    )
    # Regression injection happened before the rollback prepare started.
    assert latency["regression_injected_at_monotonic"] < (
        latency["rollback_prepare_start_monotonic"]
    )


# ---------------------------------------------------------------------------
# 3. A stays healthy
# ---------------------------------------------------------------------------


def test_active_a_remains_healthy_before_during_and_after_rollback(tmp_path):
    report = run_drill(tmp_path, actor="a-health", reason="a health invariants")
    health = report["health"]
    assert health["active_before"] == 200
    assert health["active_during_regression"] == 200
    assert health["active_after_rollback"] == 200
    assert health["candidate_after_injection"] == 503


# ---------------------------------------------------------------------------
# 4. history + receipts survive
# ---------------------------------------------------------------------------


def test_control_ledger_history_and_outcome_records_survive(tmp_path):
    report = run_drill(tmp_path, actor="history", reason="survival audit")
    # deployment_initialized + activation_prepared/completed(start) +
    # operator_stop + activation_prepared/completed(rollback-a) = 6 events.
    assert report["control_event_count"] == 6
    assert report["control_ledger_id"]

    ledger_root = tmp_path / "ledger-root"
    control_events = (ledger_root / "control" / "events.jsonl").read_text()
    kinds = [
        json.loads(line)["event"]["kind"]
        for line in control_events.splitlines()
        if line.strip()
    ]
    assert kinds == [
        "deployment_initialized",
        "activation_prepared",
        "activation_completed",
        "operator_stop",
        "activation_prepared",
        "activation_completed",
    ]
    # The rollback-a completion restores A as the active pointer.
    last_rollback = [
        line
        for line in control_events.splitlines()
        if line.strip()
        and json.loads(line)["event"]["kind"] == "activation_completed"
        and json.loads(line)["event"]["action"] == "rollback-a"
    ][-1]
    last_event = json.loads(last_rollback)["event"]
    assert last_event["pointer_active_digest"] == report["active_artifact_digest"]
    assert last_event["observed_manifest_digest"] == report["active_artifact_digest"]


def test_written_receipt_round_trips_through_gate_receipt_dataclass(tmp_path):
    summary = _run_full(tmp_path)
    receipt_raw = json.loads(Path(summary["receipt_path"]).read_text())
    receipt = GateReceipt(**receipt_raw)
    assert receipt.gate == GATE_NAME
    assert receipt.result == "pass"
    assert receipt.provider_calls == 0
    assert receipt.cost_usd == 0.0
    # Binding survives dataclass round-trip.
    assert receipt.active_artifact_digest == summary["active_artifact_digest"]
    assert receipt.candidate_artifact_digest == summary["candidate_artifact_digest"]


def test_build_gate_receipt_is_bound_to_report_bytes_only(tmp_path):
    report = run_drill(tmp_path, actor="bind", reason="binding audit")
    report_bytes = canonical_json(report) + b"\n"
    now = datetime.now(timezone.utc)
    receipt, unsigned = build_gate_receipt(
        report,
        report_bytes=report_bytes,
        gate_key=GATE_KEY,
        gate_key_id=GATE_KEY_ID,
        command_digest="sha256:" + "f" * 64,
        now=now,
    )
    assert unsigned["output_digest"] == "sha256:" + hashlib.sha256(
        report_bytes
    ).hexdigest()
    assert unsigned["output_digest"] == receipt.output_digest

    # Mutating the report after binding must NOT change the already-bound digest.
    mutated_report = dict(report)
    mutated_report["reason"] = "tampered"
    assert receipt.output_digest != "sha256:" + hashlib.sha256(
        canonical_json(mutated_report) + b"\n"
    ).hexdigest()


# ---------------------------------------------------------------------------
# 5. hermetic isolation (no production touch)
# ---------------------------------------------------------------------------


def test_drill_target_is_not_a_production_target(tmp_path):
    report = run_drill(tmp_path, actor="hermetic", reason="isolation")
    assert report["drill_target"] == DRILL_TARGET
    assert DRILL_TARGET != "production"
    assert DRILL_TARGET != "trustforge-production"
    assert "rollback-drill" in DRILL_TARGET
    assert report["config"]["lock_backend"] == "in-memory-hermetic"
    assert report["config"]["require_distributed_lock"] is False


def test_no_activation_lock_file_or_production_env_is_touched(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TRUSTFORGE_ACTIVATION_LOCK_BACKEND", raising=False)
    monkeypatch.delenv("TRUSTFORGE_ACTIVATION_LOCK_TABLE", raising=False)
    report = run_drill(tmp_path, actor="hermetic", reason="no host mutation")
    # The in-memory backend never writes an activation-lock file to disk.
    lock_files = list(tmp_path.rglob("activation_locks.json*"))
    assert lock_files == []
    # The drill never promoted B / flipped a real flag: final phase is disabled
    # and A is the sole active pointer.
    assert report["to_phase"] == "disabled"
    assert report["health"]["active_after_rollback"] == 200


def test_in_memory_lock_backend_is_stateless_and_self_cleaning():
    backend = _InMemoryActivationLockBackend()
    assert backend.get("any-target") is None
    assert backend.acquire("target-x", "owner-1", ttl=60) is True
    record = backend.get("target-x")
    assert record is not None and record.owner_id == "owner-1"
    # Non-owner cannot take the lock while it is live.
    assert backend.acquire("target-x", "owner-2", ttl=60) is False
    # Wrong owner cannot release.
    assert backend.release("target-x", "owner-2") is False
    assert backend.get("target-x") is not None
    # Right owner releases cleanly; state is gone.
    assert backend.release("target-x", "owner-1") is True
    assert backend.get("target-x") is None


def test_evidence_bundle_digest_is_drill_scoped_not_production(tmp_path):
    report = run_drill(tmp_path, actor="evidence", reason="evidence scope")
    # The evidence bundle digest is derived from the drill seed, not a real
    # deployment evidence bundle (the 9-gate contract is frozen and untouched).
    expected = "sha256:" + hashlib.sha256(
        b"trustforge.rollback-drill.evidence-bundle.v1"
    ).hexdigest()
    assert report["evidence_bundle_digest"] == expected
    # The digest is a well-formed sha256 and distinct from any single-letter
    # placeholder used in unit fixtures.
    assert report["evidence_bundle_digest"].startswith("sha256:")
    assert report["evidence_bundle_digest"] != "sha256:" + "e" * 64


def test_write_drill_artifacts_is_idempotent_and_rebinds_output(tmp_path):
    report = run_drill(tmp_path, actor="rewrite", reason="idempotent write")
    now = datetime.now(timezone.utc)
    first_report, first_receipt = write_drill_artifacts(
        tmp_path,
        report=report,
        gate_key=GATE_KEY,
        gate_key_id=GATE_KEY_ID,
        now=now,
    )
    first_receipt_raw = json.loads(first_receipt.read_text())
    second_report, second_receipt = write_drill_artifacts(
        tmp_path,
        report=report,
        gate_key=GATE_KEY,
        gate_key_id=GATE_KEY_ID,
        now=now,
    )
    assert first_report == second_report
    assert first_receipt == second_receipt
    # Same report bytes -> same output_digest across rewrites.
    second_receipt_raw = json.loads(second_receipt.read_text())
    assert (
        first_receipt_raw["output_digest"] == second_receipt_raw["output_digest"]
    )
