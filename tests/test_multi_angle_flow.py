"""Integration tests for multi-angle analysis flow (#809).

Tests:
- submit_multi_angle produces five job_ids
- _maybe_trigger_synthesis only fires when all five angles complete
- Idempotency: duplicate trigger is blocked
- multi_angle_status returns None when no result
- multi_angle_status returns payload when result exists
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from trustforge.analysis_flow import (
    AnalysisFlow,
    MODES,
    MultiAngleBudgetError,
    MultiAngleCapacityError,
    _bedrock_live_attempt,
)
from trustforge.execlog import ExecutionLog


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
        lambda: calls.append(True) or None,
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
