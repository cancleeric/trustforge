"""Integration tests for multi-angle analysis flow (#809).

Tests:
- submit_multi_angle produces five job_ids
- _maybe_trigger_synthesis only fires when all five angles complete
- Idempotency: duplicate trigger is blocked
- multi_angle_status returns None when no result
- multi_angle_status returns payload when result exists
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import contextmanager

import pytest

from trustforge.analysis_flow import (
    AnalysisFlow,
    MODES,
    MultiAngleAuthorityError,
    MultiAngleBudgetError,
    MultiAngleCapacityError,
    MultiAngleIdempotencyConflictError,
    _bedrock_live_attempt,
)
from trustforge.execlog import ExecutionLog
from trustforge.admin_config import AdminConfig


@pytest.fixture
def flow(tmp_path):
    """Create a writable AnalysisFlow with a temp SQLite DB."""
    db_path = tmp_path / "test_flow.sqlite3"
    with AnalysisFlow(path=db_path) as f:
        yield f


class TestSubmitMultiAngle:
    def test_returns_five_job_ids(self, flow):
        result = flow.submit_multi_angle("BTC", "測試五角度分析")

        assert result["coin"] == "BTC"
        assert "snapshot_id" in result
        assert result["snapshot_id"].startswith("snap-btc-")
        assert set(result["job_ids"].keys()) == set(MODES.keys())
        # All job_ids should be non-None (new snapshot, no duplicates)
        for mode, job_id in result["job_ids"].items():
            assert job_id is not None, f"{mode} should have a job_id"

    def test_invalid_coin_raises(self, flow):
        with pytest.raises(ValueError, match="unsupported coin"):
            flow.submit_multi_angle("INVALID", "test")

    def test_lineage_recorded(self, flow):
        result = flow.submit_multi_angle("ETH", "分析")
        events = flow.lineage(snapshot_id=result["snapshot_id"])
        types = [e["event_type"] for e in events]
        assert "multi_angle_submitted" in types

    def test_same_caller_key_replays_exact_batch_without_new_jobs(self, flow):
        first = flow.submit_multi_angle(
            "BTC", "same request", caller_id="203.0.113.8", idempotency_key="retry-1"
        )
        before = flow._conn().execute("SELECT count(*) FROM analysis_jobs").fetchone()[0]
        second = flow.submit_multi_angle(
            " btc ", "same request", caller_id="203.0.113.8", idempotency_key="retry-1"
        )
        assert second == first
        assert flow._conn().execute("SELECT count(*) FROM analysis_jobs").fetchone()[0] == before
        request = flow._conn().execute(
            """SELECT caller_hash,idempotency_key_hash,state,batch_id
               FROM atomic_requests"""
        ).fetchone()
        assert request["caller_hash"] != "203.0.113.8"
        assert request["idempotency_key_hash"] != "retry-1"
        assert request["state"] == "active"
        assert request["batch_id"] == first["batch_id"]

    def test_completed_replay_does_not_repeat_admission(self, flow):
        calls: list[str] = []
        kwargs = {
            "caller_id": "203.0.113.8",
            "idempotency_key": "admit-once",
            "admission_check": lambda: calls.append("admitted"),
        }
        first = flow.submit_multi_angle("BTC", "same request", **kwargs)
        second = flow.submit_multi_angle("BTC", "same request", **kwargs)
        assert second == first
        # Public rate admission is checked on every HTTP attempt, including a
        # durable replay; only budget/job creation is replayed.
        assert calls == ["admitted", "admitted"]

    def test_same_caller_key_different_payload_conflicts(self, flow):
        flow.submit_multi_angle(
            "BTC", "first", caller_id="203.0.113.9", idempotency_key="conflict-1"
        )
        with pytest.raises(MultiAngleIdempotencyConflictError):
            flow.submit_multi_angle(
                "BTC", "different", caller_id="203.0.113.9",
                idempotency_key="conflict-1",
            )
        assert flow._conn().execute("SELECT count(*) FROM analysis_jobs").fetchone()[0] == 5

    def test_incomplete_authority_replay_fails_closed(self, flow):
        caller_hash = hashlib.sha256(b"203.0.113.10").hexdigest()
        flow._atomic_store()
        flow._conn().execute(
            """INSERT INTO atomic_requests VALUES(?,?,?,?,?)""",
            (
                caller_hash, hashlib.sha256(b"pending-1").hexdigest(),
                hashlib.sha256(
                    b'{"coin":"BTC","locale":"zh-Hant","question":"pending"}'
                ).hexdigest(),
                "ma-request-fixed", "active",
            ),
        )
        with pytest.raises(MultiAngleAuthorityError):
            flow.submit_multi_angle(
                "BTC", "pending", caller_id="203.0.113.10",
                idempotency_key="pending-1",
            )

    def test_different_keys_are_distinct_paid_batches(self, flow):
        first = flow.submit_multi_angle(
            "BTC", "concurrent", caller_id="203.0.113.12",
            idempotency_key="same-key",
        )
        second = flow.submit_multi_angle(
            "BTC", "concurrent", caller_id="203.0.113.12",
            idempotency_key="other-tab-key",
        )
        assert first["batch_id"] != second["batch_id"]
        assert first["snapshot_id"] != second["snapshot_id"]
        assert flow._conn().execute(
            "SELECT count(*) FROM atomic_requests"
        ).fetchone()[0] == 2
        assert flow._conn().execute(
            "SELECT count(*) FROM analysis_jobs"
        ).fetchone()[0] == 10

    def test_failed_admission_can_retry_same_key(self, flow):
        def denied():
            raise RuntimeError("denied")

        with pytest.raises(RuntimeError, match="denied"):
            flow.submit_multi_angle(
                "BTC", "retry", caller_id="203.0.113.11",
                idempotency_key="retry-failed", admission_check=denied,
            )
        row = flow._conn().execute(
            "SELECT state FROM analysis_multi_angle_requests"
        ).fetchone()
        assert row is None
        result = flow.submit_multi_angle(
            "BTC", "retry", caller_id="203.0.113.11",
            idempotency_key="retry-failed",
        )
        assert len(result["job_ids"]) == 5

    def test_budget_preflight_rejects_before_snapshot(self, flow, monkeypatch):
        monkeypatch.setattr(
            "trustforge.analysis_flow.budget_guard.request_budget_available",
            lambda _count: False,
        )
        before = flow._conn().execute("SELECT count(*) FROM analysis_snapshots").fetchone()[0]
        with pytest.raises(MultiAngleBudgetError):
            flow.submit_multi_angle("BTC", "budget denied")
        after = flow._conn().execute("SELECT count(*) FROM analysis_snapshots").fetchone()[0]
        assert after == before
        assert flow._conn().execute("SELECT count(*) FROM analysis_jobs").fetchone()[0] == 0
        assert flow._conn().execute(
            "SELECT count(*) FROM analysis_multi_angle_runs"
        ).fetchone()[0] == 0

    def test_capacity_rejects_without_partial_jobs_or_snapshot(self, flow, monkeypatch):
        flow._conn().execute(
            "INSERT INTO analysis_snapshots VALUES(?,?,?,?,?,?)",
            ("snap-btc-existing", "BTC", time.time(), "old", "[]", 0),
        )
        monkeypatch.setattr("trustforge.analysis_flow.QUEUE_CAPACITY", 4)
        before_snapshots = flow._conn().execute(
            "SELECT count(*) FROM analysis_snapshots"
        ).fetchone()[0]
        with pytest.raises(MultiAngleCapacityError):
            flow.submit_multi_angle("BTC", "capacity denied")
        assert flow._conn().execute("SELECT count(*) FROM analysis_jobs").fetchone()[0] == 0
        assert flow._conn().execute(
            "SELECT count(*) FROM analysis_snapshots"
        ).fetchone()[0] == before_snapshots

    def test_transaction_failure_removes_exact_new_snapshot_with_prior_same_coin(
        self, flow, monkeypatch
    ):
        flow._conn().execute(
            "INSERT INTO analysis_snapshots VALUES(?,?,?,?,?,?)",
            ("snap-btc-prior", "BTC", time.time(), "prior", "[]", 0),
        )
        before = flow._conn().execute(
            "SELECT snapshot_id FROM analysis_snapshots"
        ).fetchall()
        monkeypatch.setattr(
            flow, "_checkpoint",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("tx fail")),
        )
        with pytest.raises(RuntimeError, match="tx fail"):
            flow.submit_multi_angle("BTC", "new snapshot")
        after = flow._conn().execute(
            "SELECT snapshot_id FROM analysis_snapshots"
        ).fetchall()
        assert [row[0] for row in after] == [row[0] for row in before]
        assert flow._conn().execute("SELECT count(*) FROM analysis_jobs").fetchone()[0] == 0


def test_multi_angle_worker_never_bypasses_per_call_budget_guard(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda: True)
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.daily_cap_exceeded", lambda: False
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.narrative_model_priced", lambda: True
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.try_reserve_request_budget",
        lambda **_kwargs: calls.append(True) or None,
    )

    with _bedrock_live_attempt(ExecutionLog(run_id="ma-worker-guard")) as live:
        assert live is False
    assert calls == [True]


class TestMaybeTriggerSynthesis:
    def _insert_fake_result(self, flow, snapshot_id: str, coin: str, mode: str):
        """Insert a fake analysis_results row for testing."""
        flow._conn().execute(
            "INSERT OR IGNORE INTO analysis_multi_angle_runs VALUES(?,?,?)",
            (snapshot_id, coin, time.time()),
        )
        payload = json.dumps({
            "report": {
                "direction": "偏多",
                "calibrated_confidence": 0.65,
                "decision_state": "normal",
                "question_type": "multi_source",
                "market_judgment": f"{coin} {mode} 測試判斷",
                "key_basis": [{"claim": "test", "explanation": "test"}],
            },
            "evidence": [
                {"source": f"{mode}-source-1", "trust": 0.8},
                {"source": f"{mode}-source-2", "trust": 0.6},
            ],
            "snapshot_id": snapshot_id,
        })
        flow._conn().execute(
            "INSERT OR REPLACE INTO analysis_results VALUES(?,?,?,?,?,?,?,?)",
            (f"result-test-{mode}", f"job-{mode}", snapshot_id, coin, mode,
             f"test question {mode}", payload, time.time()),
        )

    def test_does_not_trigger_with_four_angles(self, flow):
        snapshot_id = "snap-btc-test123"
        # Insert a fake snapshot row
        flow._conn().execute(
            "INSERT INTO analysis_snapshots VALUES(?,?,?,?,?,?)",
            (snapshot_id, "BTC", time.time(), "rev123", "[]", 0),
        )
        modes = list(MODES.keys())
        for mode in modes[:4]:  # Only 4 out of 5
            self._insert_fake_result(flow, snapshot_id, "BTC", mode)

        result = flow._maybe_trigger_synthesis(snapshot_id, "BTC")
        assert result is False

    def test_triggers_with_all_five_angles(self, flow):
        snapshot_id = "snap-btc-full5"
        flow._conn().execute(
            "INSERT INTO analysis_snapshots VALUES(?,?,?,?,?,?)",
            (snapshot_id, "BTC", time.time(), "revfull", "[]", 0),
        )
        for mode in MODES:
            self._insert_fake_result(flow, snapshot_id, "BTC", mode)

        result = flow._maybe_trigger_synthesis(snapshot_id, "BTC")
        assert result is True

        # Verify the synthesis result was stored
        row = flow._conn().execute(
            "SELECT payload_json FROM analysis_results WHERE snapshot_id=? AND mode='multi_angle'",
            (snapshot_id,),
        ).fetchone()
        assert row is not None
        payload = json.loads(row["payload_json"])
        assert payload["consensus"] == "偏多"  # All angles are 偏多
        assert payload["coin"] == "BTC"
        assert len(payload["angles"]) == 5

    def test_idempotency_blocks_duplicate(self, flow):
        snapshot_id = "snap-btc-idem"
        flow._conn().execute(
            "INSERT INTO analysis_snapshots VALUES(?,?,?,?,?,?)",
            (snapshot_id, "BTC", time.time(), "revidem", "[]", 0),
        )
        for mode in MODES:
            self._insert_fake_result(flow, snapshot_id, "BTC", mode)

        # First trigger succeeds
        assert flow._maybe_trigger_synthesis(snapshot_id, "BTC") is True
        # Second trigger blocked
        assert flow._maybe_trigger_synthesis(snapshot_id, "BTC") is False

    def test_concurrent_synthesis_has_single_winner(self, flow):
        snapshot_id = "snap-btc-race"
        flow._conn().execute(
            "INSERT INTO analysis_snapshots VALUES(?,?,?,?,?,?)",
            (snapshot_id, "BTC", time.time(), "revrace", "[]", 0),
        )
        for mode in MODES:
            self._insert_fake_result(flow, snapshot_id, "BTC", mode)
        barrier = threading.Barrier(2)
        results: list[bool] = []

        def trigger():
            barrier.wait()
            results.append(flow._maybe_trigger_synthesis(snapshot_id, "BTC"))

        workers = [threading.Thread(target=trigger) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        assert sorted(results) == [False, True]
        assert flow._conn().execute(
            "SELECT count(*) FROM analysis_results WHERE snapshot_id=? AND mode='multi_angle'",
            (snapshot_id,),
        ).fetchone()[0] == 1

    def test_failed_synthesis_releases_claim(self, flow, monkeypatch):
        snapshot_id = "snap-btc-claim-failure"
        flow._conn().execute(
            "INSERT INTO analysis_snapshots VALUES(?,?,?,?,?,?)",
            (snapshot_id, "BTC", time.time(), "revfail", "[]", 0),
        )
        for mode in MODES:
            self._insert_fake_result(flow, snapshot_id, "BTC", mode)
        monkeypatch.setattr(
            flow,
            "_complete_claimed_synthesis",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(RuntimeError, match="boom"):
            flow._maybe_trigger_synthesis(snapshot_id, "BTC")
        assert flow._conn().execute(
            "SELECT count(*) FROM analysis_synthesis_claims WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()[0] == 0

    def test_daemon_recover_repairs_stale_synthesis_claim(self, flow):
        snapshot_id = "snap-btc-crash-recovery"
        flow._conn().execute(
            "INSERT INTO analysis_snapshots VALUES(?,?,?,?,?,?)",
            (snapshot_id, "BTC", time.time(), "revcrash", "[]", 0),
        )
        for mode in MODES:
            self._insert_fake_result(flow, snapshot_id, "BTC", mode)
        flow._conn().execute(
            "INSERT OR REPLACE INTO analysis_synthesis_claims VALUES(?,?,?)",
            (snapshot_id, "BTC", 0.0),
        )

        flow.recover()

        assert flow._conn().execute(
            "SELECT count(*) FROM analysis_results "
            "WHERE snapshot_id=? AND mode='multi_angle'",
            (snapshot_id,),
        ).fetchone()[0] == 1


class TestMultiAngleStatus:
    def test_returns_none_when_no_result(self, flow):
        result = flow.multi_angle_status("BTC")
        assert result is None

    def test_returns_payload_when_exists(self, flow):
        snapshot_id = "snap-sol-status"
        flow._conn().execute(
            "INSERT INTO analysis_snapshots VALUES(?,?,?,?,?,?)",
            (snapshot_id, "SOL", time.time(), "revstatus", "[]", 0),
        )
        # Insert all five angles + trigger synthesis
        flow._conn().execute(
            "INSERT INTO analysis_multi_angle_runs VALUES(?,?,?)",
            (snapshot_id, "SOL", time.time()),
        )
        modes = list(MODES.keys())
        for mode in modes:
            payload = json.dumps({
                "report": {
                    "direction": "偏空",
                    "calibrated_confidence": 0.55,
                    "decision_state": "normal",
                    "question_type": "multi_source",
                    "market_judgment": f"SOL {mode} test",
                    "key_basis": [],
                },
                "evidence": [{"source": f"src-{mode}", "trust": 0.7}],
                "snapshot_id": snapshot_id,
            })
            flow._conn().execute(
                "INSERT INTO analysis_results VALUES(?,?,?,?,?,?,?,?)",
                (f"result-sol-{mode}", f"job-sol-{mode}", snapshot_id, "SOL", mode,
                 "test", payload, time.time()),
            )
        flow._maybe_trigger_synthesis(snapshot_id, "SOL")

        result = flow.multi_angle_status("SOL")
        assert result is not None
        assert result["coin"] == "SOL"
        assert result["consensus"] == "偏空"

    def test_returns_specific_snapshot(self, flow):
        # Create two snapshots with results
        for snap_id in ["snap-xrp-a", "snap-xrp-b"]:
            flow._conn().execute(
                "INSERT INTO analysis_snapshots VALUES(?,?,?,?,?,?)",
                (snap_id, "XRP", time.time(), f"rev-{snap_id}", "[]", 0),
            )
            flow._conn().execute(
                "INSERT INTO analysis_multi_angle_runs VALUES(?,?,?)",
                (snap_id, "XRP", time.time()),
            )
            for mode in MODES:
                payload = json.dumps({
                    "report": {
                        "direction": "中性" if snap_id == "snap-xrp-a" else "偏多",
                        "calibrated_confidence": 0.5,
                        "decision_state": "normal",
                        "question_type": "multi_source",
                        "market_judgment": "test",
                        "key_basis": [],
                    },
                    "evidence": [{"source": "s", "trust": 0.5}],
                    "snapshot_id": snap_id,
                })
                flow._conn().execute(
                    "INSERT INTO analysis_results VALUES(?,?,?,?,?,?,?,?)",
                    (f"result-{snap_id}-{mode}", f"job-{snap_id}-{mode}",
                     snap_id, "XRP", mode, "q", payload, time.time()),
                )
            flow._maybe_trigger_synthesis(snap_id, "XRP")

        # Query specific snapshot
        result_a = flow.multi_angle_status("XRP", "snap-xrp-a")
        assert result_a["consensus"] == "中性"

        result_b = flow.multi_angle_status("XRP", "snap-xrp-b")
        assert result_b["consensus"] == "偏多"


def _insert_complete_angle_inputs(flow, snapshot_id="snap-narration", coin="BTC"):
    for mode in MODES:
        payload = json.dumps({
            "report": {
                "direction": "偏多",
                "calibrated_confidence": 0.65,
                "decision_state": "normal",
                "question_type": "multi_source",
                "market_judgment": f"{coin} {mode}",
                "key_basis": [],
            },
            "evidence": [{"source": f"source-{mode}", "trust": 0.8}],
            "snapshot_id": snapshot_id,
        })
        flow._conn().execute(
            "INSERT INTO analysis_results VALUES(?,?,?,?,?,?,?,?)",
            (
                f"result-{snapshot_id}-{mode}",
                f"job-{snapshot_id}-{mode}",
                snapshot_id,
                coin,
                mode,
                "q",
                payload,
                time.time(),
            ),
        )


def test_default_on_runtime_calls_llm_exactly_once_and_persists_narration(
    flow, monkeypatch
):
    calls = []

    @contextmanager
    def live_gate(_log):
        yield True

    class Result:
        text = "五方向一致偏多，證據來源彼此獨立。"
        model_id = None

    class Client:
        def __init__(self, *, offline):
            assert offline is False
            self.offline = offline

        def complete(self, **kwargs):
            calls.append(kwargs)
            return Result()

    monkeypatch.delenv("TRUSTFORGE_MULTI_ANGLE_NARRATION", raising=False)
    monkeypatch.setattr(
        "trustforge.admin_config.get_config_cached",
        lambda: AdminConfig(multi_angle_narration_enabled=None),
    )
    monkeypatch.setattr("trustforge.analysis_flow._bedrock_live_attempt", live_gate)
    monkeypatch.setattr("trustforge.analysis_flow.BedrockClient", Client)
    _insert_complete_angle_inputs(flow)

    assert flow._complete_claimed_synthesis("snap-narration", "BTC") is True
    result = flow.multi_angle_status("BTC", "snap-narration")

    assert len(calls) == 1
    assert result["narration"] == Result.text
    assert result["synthesis_summary"]


@pytest.mark.parametrize(
    ("case", "resolver_result"),
    [
        ("admin_off", (False, "config")),
        ("config_read_error", (False, "config_read_error")),
    ],
)
def test_runtime_disabled_resolver_never_constructs_client(
    flow, monkeypatch, case, resolver_result
):
    constructed = []
    monkeypatch.setattr(
        "trustforge.admin_config.multi_angle_narration_enabled_resolved",
        lambda: resolver_result,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.BedrockClient",
        lambda **kwargs: constructed.append(kwargs),
    )
    snapshot_id = f"snap-{case}"
    _insert_complete_angle_inputs(flow, snapshot_id)

    assert flow._complete_claimed_synthesis(snapshot_id, "BTC") is True
    result = flow.multi_angle_status("BTC", snapshot_id)

    assert constructed == []
    assert "narration" not in result
    assert result["synthesis_summary"]


def test_runtime_env_zero_never_constructs_client(flow, monkeypatch):
    constructed = []
    monkeypatch.setenv("TRUSTFORGE_MULTI_ANGLE_NARRATION", "0")
    monkeypatch.setattr(
        "trustforge.analysis_flow.BedrockClient",
        lambda **kwargs: constructed.append(kwargs),
    )
    _insert_complete_angle_inputs(flow, "snap-env-zero")

    assert flow._complete_claimed_synthesis("snap-env-zero", "BTC") is True
    result = flow.multi_angle_status("BTC", "snap-env-zero")

    assert constructed == []
    assert "narration" not in result


def test_llm_error_falls_back_without_changing_structural_fields(flow, monkeypatch):
    @contextmanager
    def live_gate(_log):
        yield True

    class BrokenClient:
        def __init__(self, *, offline):
            assert offline is False
            self.offline = offline

        def complete(self, **_kwargs):
            raise RuntimeError("provider failure")

    monkeypatch.setattr(
        "trustforge.admin_config.multi_angle_narration_enabled_resolved",
        lambda: (True, "default"),
    )
    monkeypatch.setattr("trustforge.analysis_flow._bedrock_live_attempt", live_gate)
    monkeypatch.setattr("trustforge.analysis_flow.BedrockClient", BrokenClient)
    _insert_complete_angle_inputs(flow, "snap-llm-error")

    assert flow._complete_claimed_synthesis("snap-llm-error", "BTC") is True
    result = flow.multi_angle_status("BTC", "snap-llm-error")

    assert "narration" not in result
    assert result["coin"] == "BTC"
    assert result["consensus"] == "偏多"
    assert result["angles"]
    assert result["synthesis_summary"]
    assert "agreement_matrix" in result
