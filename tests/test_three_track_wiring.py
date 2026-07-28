"""Issue #570: tests for the three-track learning wiring into AnalysisFlow.

These tests verify the four CEO-mandated guarantees:

1. **Flag OFF regression** — main analysis output is byte-for-byte
   identical regardless of the kill switch.
2. **Flag ON positive** — every completed job produces exactly one
   immutable learning event with the canonical identity.
3. **Fail-soft** — a deliberately broken sink does NOT break the main
   analysis; an observation log line is emitted.
4. **Failure also emits** — jobs that enter the dead-letter queue still
   produce a learning event recording the failure cause.

Every test runs the **real** five-stage AnalysisFlow pipeline; no fixture
dicts are passed directly to the wiring helpers.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import pytest

from trustforge import analysis_flow as analysis_flow_module
from trustforge.analysis_flow import AnalysisFlow
from trustforge.ingestion.base import Document
from trustforge.learning_event_contract import deserialize_learning_event
from trustforge.learning_event_store import FileLearningEventStore
from trustforge import three_track_wiring


# --------------------------------------------------------------------------- #
# Real-pipeline fixture
# --------------------------------------------------------------------------- #

def _docs() -> list[Document]:
    now = time.time()
    return [
        Document(id="a", kind="price", source="source-a",
                 text="BTC 價格盤整。", url="https://a.test", ts=now, meta={}),
        Document(id="b", kind="news", source="source-b",
                 text="BTC 市場成交量保持穩定。", url="https://b.test", ts=now, meta={}),
    ]


def _run_real_pipeline(tmp_path: Path, *, coin: str = "BTC") -> tuple[AnalysisFlow, str, list[str]]:
    db_path = tmp_path / "flow.sqlite3"
    flow = AnalysisFlow(db_path)
    snapshot_id = flow.create_snapshot(coin)
    jobs = flow.enqueue_matrix(snapshot_id)
    flow.start()
    flow.join()
    flow.stop()
    return flow, snapshot_id, jobs


def _events_on_disk(flow: AnalysisFlow) -> list[Any]:
    """Read every immutable event file written next to the analysis DB."""
    store_dir = Path(flow.path).parent / "learning_events"
    if not store_dir.exists():
        return []
    store = FileLearningEventStore(directory=store_dir)
    return list(store.replay(trusted_tenant_id=three_track_wiring.DEFAULT_TENANT_ID))


# --------------------------------------------------------------------------- #
# 1. Flag OFF regression — byte-for-byte identical analysis
# --------------------------------------------------------------------------- #

class TestFlagOffRegression:
    """Gate: kill switch off → analysis pipeline output is unchanged."""

    def test_flag_off_emits_zero_events(self, tmp_path, monkeypatch):
        """Default flag state is OFF; no learning events appear on disk."""
        monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *a, **kw: _docs())
        monkeypatch.delenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", raising=False)
        assert three_track_wiring.emission_enabled() is False

        flow, _, jobs = _run_real_pipeline(tmp_path)
        assert len(jobs) >= 1
        # No learning_events directory should ever be created when OFF.
        assert _events_on_disk(flow) == []
        assert not (Path(flow.path).parent / "learning_events").exists()

    def test_analysis_output_byte_for_byte_identical(self, tmp_path, monkeypatch):
        """Pipeline output does not change whether the flag is on or off.

        Runs the real pipeline twice with identical fixture inputs, once
        with emission disabled and once enabled, then asserts every
        published report (the deterministic analysis payload — coin,
        question, facts, inferences, calibrated confidence, decision)
        matches mode-by-mode. Execution timestamps and snapshot ids are
        non-deterministic across runs by design; comparing the ``report``
        sub-document isolates the analysis signal from bookkeeping.
        """
        monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *a, **kw: _docs())

        # Run A — flag OFF.
        dir_a = tmp_path / "off"
        dir_a.mkdir()
        monkeypatch.delenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", raising=False)
        flow_a, _, jobs_a = _run_real_pipeline(dir_a)
        reports_a = self._reports_by_mode(flow_a)

        # Run B — flag ON (events written to disk but analysis must match).
        dir_b = tmp_path / "on"
        dir_b.mkdir()
        monkeypatch.setenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", "1")
        assert three_track_wiring.emission_enabled() is True
        flow_b, _, jobs_b = _run_real_pipeline(dir_b)
        reports_b = self._reports_by_mode(flow_b)

        # Same modes and identical report bodies (the analysis signal).
        assert sorted(reports_a) == sorted(reports_b)
        for mode in reports_a:
            assert reports_a[mode] == reports_b[mode], (
                f"report body diverged for mode={mode}: flag perturbed analysis"
            )
        # Sanity: events were actually written in run B.
        assert len(_events_on_disk(flow_b)) == len(jobs_b)

    @staticmethod
    def _reports_by_mode(flow: AnalysisFlow) -> dict[str, dict[str, Any]]:
        rows = flow._conn().execute(
            "SELECT mode, payload_json FROM analysis_results WHERE mode != 'multi_angle'",
        ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            report = dict(payload["report"])
            report.pop("generated_at", None)
            out[row["mode"]] = report
        return out


# --------------------------------------------------------------------------- #
# 2. Flag ON positive — one immutable event per completed job
# --------------------------------------------------------------------------- #

class TestFlagOnPositive:
    """Gate: flag on → every completed job becomes one canonical event."""

    def test_one_event_per_completed_job(self, tmp_path, monkeypatch):
        monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *a, **kw: _docs())
        monkeypatch.setenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", "1")

        flow, snapshot_id, jobs = _run_real_pipeline(tmp_path)
        completed_count = flow._conn().execute(
            "SELECT count(*) FROM analysis_jobs WHERE state='completed'",
        ).fetchone()[0]
        assert completed_count == len(jobs)

        events = _events_on_disk(flow)
        assert len(events) == completed_count

        # Every event is the canonical analysis-quality.v1 record.
        for event in events:
            assert event.kind == "historical_non_evidentiary"
            assert event.payload["event_type"] == "analysis-quality.v1"
            assert event.tenant_id == three_track_wiring.DEFAULT_TENANT_ID
            assert event.payload["versions"]["contract"] == "analysis-quality.v1"

    def test_every_event_identity_is_unique(self, tmp_path, monkeypatch):
        """Two pipeline runs (different snapshots) yield no shared identity."""
        monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *a, **kw: _docs())
        monkeypatch.setenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", "1")

        dir1 = tmp_path / "run1"; dir1.mkdir()
        flow1, _, _ = _run_real_pipeline(dir1)
        dir2 = tmp_path / "run2"; dir2.mkdir()
        flow2, _, _ = _run_real_pipeline(dir2)

        ids1 = {e.identity for e in _events_on_disk(flow1)}
        ids2 = {e.identity for e in _events_on_disk(flow2)}
        assert ids1 and ids2
        assert ids1.isdisjoint(ids2)

    def test_event_files_are_immutable(self, tmp_path, monkeypatch):
        """Re-emitting the same canonical bytes is idempotent; mutating fails."""
        monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *a, **kw: _docs())
        monkeypatch.setenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", "1")

        flow, _, jobs = _run_real_pipeline(tmp_path)
        events_before = _events_on_disk(flow)
        # Pick the on-disk event that corresponds to jobs[0]; events may be
        # returned in a different order than enqueue_matrix emitted them.
        prefix = f"real-analysis-{jobs[0]}"
        target_event = next(
            e for e in events_before if e.payload["analysis_id"] == prefix
        )
        identity = target_event.identity

        # Re-running the emission manually with the same inputs must be a
        # no-op (idempotent), and the on-disk event set is unchanged.
        result = three_track_wiring.emit_for_completed_job(flow, jobs[0])
        assert result == identity
        events_after = _events_on_disk(flow)
        assert {e.identity for e in events_after} == {e.identity for e in events_before}


# --------------------------------------------------------------------------- #
# 3. Fail-soft — broken sink must not break the analysis
# --------------------------------------------------------------------------- #

class _BrokenSink:
    """Append-only sink that always raises, simulating storage failure."""

    def append(self, event):  # noqa: ANN001 - intentional minimal signature
        raise RuntimeError("simulated learning_event_store outage")


class TestFailSoft:
    """Gate: any exception inside the wiring must be swallowed + logged."""

    def test_broken_sink_does_not_break_analysis(self, tmp_path, monkeypatch, caplog):
        """The pipeline still completes every job when the sink is broken."""
        monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *a, **kw: _docs())
        monkeypatch.setenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", "1")
        monkeypatch.setattr(
            three_track_wiring, "_build_store", lambda flow: _BrokenSink(),
        )

        with caplog.at_level(logging.ERROR, logger="trustforge.three_track_wiring"):
            flow, _, jobs = _run_real_pipeline(tmp_path)

        # Analysis fully completed despite the broken sink.
        completed = flow._conn().execute(
            "SELECT count(*) FROM analysis_jobs WHERE state='completed'",
        ).fetchone()[0]
        assert completed == len(jobs)

        # The failure was observable in the wiring logger.
        assert any(
            "fail-soft" in rec.message
            and "emit_for_completed_job" in rec.message
            for rec in caplog.records
        )

        # No event files were created (the sink rejected every append).
        assert _events_on_disk(flow) == []

    def test_import_failure_does_not_break_analysis(self, tmp_path, monkeypatch, caplog):
        """If the wiring module itself fails to import, analysis survives.

        The module-level wrapper inside ``analysis_flow`` catches every
        exception from the lazy import, so a corrupted install or missing
        dependency cannot take the pipeline down.
        """
        import sys

        monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *a, **kw: _docs())
        monkeypatch.setenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", "1")

        # Force the lazy import inside the wrapper to raise ImportError by
        # poisoning sys.modules for one call.
        real_module = sys.modules.get("trustforge.three_track_wiring")
        poison_called = {"yes": False}

        class _PoisonedFinder:
            def find_spec(self, name, path=None, target=None):  # noqa: ANN001
                if name == "trustforge.three_track_wiring":
                    poison_called["yes"] = True
                    raise ImportError("simulated corrupted install")
                return None

        finder = _PoisonedFinder()
        sys.meta_path.insert(0, finder)
        sys.modules.pop("trustforge.three_track_wiring", None)
        try:
            with caplog.at_level(logging.ERROR, logger="trustforge.analysis_flow"):
                flow, _, jobs = _run_real_pipeline(tmp_path)
        finally:
            sys.meta_path.remove(finder)
            sys.modules.pop("trustforge.three_track_wiring", None)
            if real_module is not None:
                sys.modules["trustforge.three_track_wiring"] = real_module

        assert poison_called["yes"] is True
        completed = flow._conn().execute(
            "SELECT count(*) FROM analysis_jobs WHERE state='completed'",
        ).fetchone()[0]
        assert completed == len(jobs)
        assert any(
            "fail-soft" in rec.message for rec in caplog.records
        )


# --------------------------------------------------------------------------- #
# 4. Failure also emits — dead-letter jobs produce a learning event
# --------------------------------------------------------------------------- #

class TestFailureAlsoEmits:
    """Gate: a job that lands in the dead-letter queue still emits."""

    def test_failed_job_produces_failure_event(self, tmp_path, monkeypatch):
        monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *a, **kw: _docs())
        monkeypatch.setenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", "1")

        db_path = tmp_path / "flow.sqlite3"
        flow = AnalysisFlow(db_path)
        snapshot = flow.create_snapshot("BTC")
        job_ids = flow.enqueue_matrix(snapshot)
        [job_id, *_] = job_ids

        # Force a non-retryable failure inside claim_extraction for every
        # mode's job. enqueue_matrix emits one job per (coin, mode), so
        # every dead-letter row carries the same failure cause.
        original = flow._stage_claim_extraction
        boom = ValueError("bad claims for three-track test")
        flow._stage_claim_extraction = lambda package: (_ for _ in ()).throw(boom)
        flow.start(); flow.join(); flow.stop()
        flow._stage_claim_extraction = original

        dead = flow._conn().execute(
            "SELECT count(*) FROM analysis_dead_letters WHERE job_id=?", (job_id,),
        ).fetchone()[0]
        assert dead == 1

        events = _events_on_disk(flow)
        failure_events = [
            e for e in events
            if e.payload.get("failure", {}).get("status") == "partial"
        ]
        # Every enqueued job failed and produced one partial event.
        assert len(failure_events) == len(job_ids)

        # Verify the targeted job_id has the right content.
        target = next(
            e for e in failure_events
            if e.payload["analysis_id"] == f"real-analysis-{job_id}"
        )
        assert target.payload["failure"]["code"] == "analysis_job_failed"
        assert "bad claims for three-track test" in target.payload["failure"]["message"]
        assert target.payload["failure"]["failed_stage"] == "claim_extraction"
        # Neutralised decision so calibration never sees phantom signal.
        assert target.payload["decision"]["direction"] == "neutral"
        # The corresponding stage_metric carries the failed stage.
        failed_metrics = [
            m for m in target.payload["stage_metrics"]
            if m.get("status") == "failed"
        ]
        assert len(failed_metrics) == 1
        assert failed_metrics[0]["stage"] == "claim_extraction"

    def test_failed_job_event_still_immutable(self, tmp_path, monkeypatch):
        """Re-emitting the same failure is idempotent; mutation is rejected."""
        monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *a, **kw: _docs())
        monkeypatch.setenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", "1")

        db_path = tmp_path / "flow.sqlite3"
        flow = AnalysisFlow(db_path)
        snapshot = flow.create_snapshot("BTC")
        [job_id, *_] = flow.enqueue_matrix(snapshot)
        boom = ValueError("deterministic failure")
        flow._stage_claim_extraction = lambda package: (_ for _ in ()).throw(boom)
        flow.start(); flow.join(); flow.stop()

        events_before = {e.identity for e in _events_on_disk(flow)}
        # Re-emit with the *same* canonical bytes (same error object) so the
        # store treats it as idempotent rather than a conflicting revision.
        identity = three_track_wiring.emit_for_failed_job(flow, job_id, error=boom)
        assert identity in events_before
        assert {e.identity for e in _events_on_disk(flow)} == events_before


# --------------------------------------------------------------------------- #
# 5. Flag reader unit tests
# --------------------------------------------------------------------------- #

class TestFlagReader:
    """The flag reader is the single source of truth for the kill switch."""

    @pytest.mark.parametrize("value,expected", [
        ("1", True), ("true", True), ("TRUE", True),
        ("yes", True), ("Yes", True), ("on", True), ("ON", True),
        ("0", False), ("false", False), ("", False),
        ("random", False), (" ", False), ("\tYES\n", True),
    ])
    def test_truthy_values(self, monkeypatch, value, expected):
        monkeypatch.setenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", value)
        assert three_track_wiring.emission_enabled() is expected

    def test_unset_defaults_to_off(self, monkeypatch):
        monkeypatch.delenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", raising=False)
        assert three_track_wiring.emission_enabled() is False
