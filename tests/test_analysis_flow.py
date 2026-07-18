from __future__ import annotations

import time
import sqlite3

import plistlib
import pytest

from trustforge.analysis_flow import AnalysisFlow, MODES, STAGES
from trustforge.ingestion.base import Document


def _docs() -> list[Document]:
    now = time.time()
    return [
        Document(id="a", kind="price", source="source-a", text="BTC 價格盤整。", url="https://a.test", ts=now, meta={}),
        Document(id="b", kind="news", source="source-b", text="BTC 市場成交量保持穩定。", url="https://b.test", ts=now, meta={}),
    ]


def test_matrix_is_snapshot_isolated_and_atomically_published(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    jobs = flow.enqueue_matrix(snapshot)
    flow.start(); flow.join(); flow.stop()

    assert len(jobs) == len(MODES)
    assert flow.latest("BTC", "risk")["snapshot_id"] == snapshot
    assert flow._conn().execute("SELECT count(*) FROM analysis_results").fetchone()[0] == len(MODES)
    assert flow._conn().execute("SELECT count(*) FROM analysis_jobs WHERE state='completed'").fetchone()[0] == len(MODES)
    assert flow.status()["stages"] == [
        {"id": stage, "queued": 0, "current": None, "next_retry_at": None} for stage in STAGES
    ]
    events = flow.lineage(job_id=jobs[0])
    assert events[0]["event_type"] == "job_enqueued"
    assert [event["stage"] for event in events if event["event_type"] == "stage_completed"] == list(STAGES)
    published = next(event for event in events if event["event_type"] == "result_published")
    assert published["parent_id"] == jobs[0]
    assert published["metadata"]["report_schema_version"] == "1.0.0"
    features = flow._conn().execute(
        "SELECT feature_name FROM trust_feature_values WHERE run_id=? ORDER BY feature_name", (jobs[0],),
    ).fetchall()
    assert [row[0] for row in features] == [
        "average_evidence_trust", "calibrated_confidence", "evidence_count",
        "independent_source_count", "raw_confidence",
    ]


def test_lineage_snapshot_event_is_idempotent_and_events_are_immutable(tmp_path, monkeypatch):
    docs = _docs()
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: docs)
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    assert flow.create_snapshot("BTC") == snapshot
    events = flow.lineage(snapshot_id=snapshot)
    assert [event["event_type"] for event in events] == ["snapshot_created"]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        flow._conn().execute(
            "UPDATE analysis_lineage_events SET entity_id='changed' WHERE event_id=?",
            (events[0]["event_id"],),
        )


def test_same_snapshot_matrix_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    assert len(flow.enqueue_matrix(snapshot)) == len(MODES)
    assert flow.enqueue_matrix(snapshot) == []


def test_full_queue_is_normal_backpressure_and_refresh_fills_missing_matrix(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    monkeypatch.setattr("trustforge.analysis_flow.QUEUE_CAPACITY", 2)
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")

    assert len(flow.enqueue_matrix(snapshot)) == 2
    assert flow.status()["queue"] == {"pending": 2, "capacity": 2, "backpressure": True, "manual_pending": 0}

    # Free one durable slot. The next normal refresh revisits the same snapshot,
    # skips existing entries, and fills exactly one previously omitted mode.
    first = flow._conn().execute("SELECT job_id FROM analysis_jobs ORDER BY created_at LIMIT 1").fetchone()[0]
    flow._conn().execute("UPDATE analysis_jobs SET state='completed' WHERE job_id=?", (first,))
    assert len(flow.refresh_once()) == 1
    assert flow._conn().execute("SELECT count(*) FROM analysis_jobs").fetchone()[0] == 3


def test_disabled_scheduled_cycle_is_audited_without_enqueuing(tmp_path):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")

    run = flow.scheduled_cycle(enabled=False, config_source="production_default")

    assert run["state"] == "skipped"
    assert run["skip_reason"] == "automation_disabled"
    assert run["job_count"] == 0
    assert run["jobs"] == []
    assert 1700 < run["next_run_at"] - run["started_at"] < 1900


def test_manual_pending_has_priority_over_scheduled_cycle(tmp_path, monkeypatch):
    calls = 0

    def collect_docs(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _docs()

    monkeypatch.setattr("trustforge.analysis_flow.collect", collect_docs)
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    _, manual_job = flow.submit_manual("BTC", "risk", "優先處理手動分析")
    assert manual_job

    run = flow.scheduled_cycle(enabled=True, config_source="admin_config")

    assert run["state"] == "skipped"
    assert run["skip_reason"] == "manual_pending"
    assert calls == 1


def test_scheduled_cycle_binds_jobs_to_durable_run(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")

    run = flow.scheduled_cycle(enabled=True, config_source="admin_config")

    assert run["state"] == "completed"
    assert run["job_count"] == len(MODES) * 5
    assert len(run["jobs"]) == run["job_count"]
    assert flow._conn().execute(
        "SELECT count(*) FROM analysis_jobs WHERE scheduler_run_id=?",
        (run["scheduler_run_id"],),
    ).fetchone()[0] == run["job_count"]
    assert flow.status()["scheduler"]["recent_runs"][0]["scheduler_run_id"] == run["scheduler_run_id"]


def test_stale_scheduler_run_is_projected_as_timed_out(tmp_path):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    started = time.time() - 901
    flow._conn().execute(
        "INSERT INTO analysis_scheduler_runs VALUES(?,?,?,?,?,?,?,?,?)",
        ("stale", "running", started, None, started + 1800, "timer", 0, None, None),
    )

    assert flow.scheduler_status()["recent_runs"][0]["state"] == "timed_out"


def test_registered_question_is_adopted_and_reanalyzed_on_new_snapshot(tmp_path, monkeypatch):
    docs = _docs()
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: docs)
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    flow.enqueue_matrix(snapshot)
    question = "BTC 是否出現新的跨來源分歧？"
    _, job_id = flow.register_question("BTC", "risk", question)
    assert job_id is not None
    flow.start(); flow.join(); flow.stop()
    assert flow.latest("BTC", "risk", question)["report"]["question"] == question

    docs.append(Document(id="c", kind="news", source="source-c", text="BTC 新資料。", url="https://c.test", ts=time.time(), meta={}))
    next_flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    next_flow.start()
    next_jobs = next_flow.refresh_once()
    next_flow.join(); next_flow.stop()
    assert next_jobs
    assert flow.latest("BTC", "risk", question)["snapshot_id"] != snapshot


def test_manual_job_precedes_waiting_scheduled_work_and_records_origin(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    scheduled = flow.enqueue_job(snapshot, "risk", "scheduled work")
    _, manual = flow.submit_manual("BTC", "risk", "manual work")

    assert scheduled and manual
    priority, _, package = flow._queues["source_ingestion"].get_nowait()
    flow._queues["source_ingestion"].task_done()
    assert priority == 0
    assert package["job_id"] == manual
    job = flow._job(manual)
    assert (job["origin"], job["priority"]) == ("manual", 0)
    assert flow.job_status(manual)["queue_position"] == 1
    flow.stop()


def test_manual_priority_is_preserved_at_later_stage_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    scheduled = flow.enqueue_job(snapshot, "risk", "scheduled work")
    _, manual = flow.submit_manual("ETH", "risk", "manual work")

    assert scheduled and manual
    flow._put_package("trust_reasoning", {"job_id": scheduled})
    flow._put_package("trust_reasoning", {"job_id": manual})
    priority, _, package = flow._queues["trust_reasoning"].get_nowait()
    flow._queues["trust_reasoning"].task_done()
    assert priority == 0
    assert package["job_id"] == manual
    flow.stop()


def test_repeated_manual_request_reuses_recent_job_without_collecting_again(tmp_path, monkeypatch):
    calls = 0

    def collect(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _docs()

    monkeypatch.setattr("trustforge.analysis_flow.collect", collect)
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    question = "BTC 是否出現新的跨來源分歧？"
    _, first = flow.submit_manual("BTC", "risk", question)
    _, second = flow.submit_manual(" btc ", " risk ", f" {question} ")

    assert first == second
    assert calls == 1
    assert flow._conn().execute(
        "SELECT count(*) FROM analysis_jobs WHERE origin='manual'",
    ).fetchone()[0] == 1
    flow.stop()


def test_invalid_manual_job_never_collects_sources(tmp_path, monkeypatch):
    called = False
    def collect(*_args, **_kwargs):
        nonlocal called
        called = True
        return _docs()
    monkeypatch.setattr("trustforge.analysis_flow.collect", collect)
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")

    with pytest.raises(ValueError, match="question must contain"):
        flow.submit_manual("BTC", "risk", "")

    assert called is False


def test_nonretryable_failure_enters_dead_letter_with_attempt_history(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    [job_id, *_] = flow.enqueue_matrix(snapshot)
    original = flow._stage_claim_extraction
    flow._stage_claim_extraction = lambda package: (_ for _ in ()).throw(ValueError("bad claims"))
    flow.start(); flow.join(); flow.stop()
    journey = flow.journey(limit=100)
    dead = next(item for item in journey["dead_letters"] if item["job_id"] == job_id)
    assert dead["attempts"] == 1
    assert dead["error"] == "bad claims"
    job = next(item for item in journey["jobs"] if item["job_id"] == job_id)
    assert job["attempts"][0]["retryable"] == 0
    flow._stage_claim_extraction = original
    assert flow.requeue_dead_letter(job_id) is True
    assert flow.requeue_dead_letter("missing") is False


def test_prune_keeps_referenced_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    flow.enqueue_matrix(snapshot); flow.start(); flow.join(); flow.stop()
    flow._conn().execute("UPDATE analysis_snapshots SET created_at=0")
    counts = flow.prune(snapshot_days=1, job_days=1, result_days=1)
    assert counts["snapshots"] == 0
    assert flow._conn().execute("SELECT 1 FROM analysis_snapshots WHERE snapshot_id=?", (snapshot,)).fetchone()


def test_due_retry_survives_process_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    path = tmp_path / "flow.sqlite3"
    producer = AnalysisFlow(path)
    snapshot = producer.create_snapshot("BTC")
    [job_id, *_] = producer.enqueue_matrix(snapshot)
    producer._conn().execute(
        "INSERT INTO analysis_retry_queue VALUES(?,?,?,?,?)",
        (job_id, "trust_reasoning", time.time() - 1, 1, "temporary"),
    )
    consumer = AnalysisFlow(path)
    consumer.start(); consumer.join(); consumer.stop()
    assert consumer._conn().execute("SELECT 1 FROM analysis_retry_queue WHERE job_id=?", (job_id,)).fetchone() is None
    assert consumer._conn().execute("SELECT state FROM analysis_jobs WHERE job_id=?", (job_id,)).fetchone()[0] == "completed"


def test_question_rag_retrieves_sqlite_history_with_snapshot_lineage(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    question = "BTC 是否出現跨來源分歧與操縱風險？"
    flow.register_question("BTC", "risk", question)
    flow.start(); flow.join(); flow.stop()

    context = flow.question_context("BTC", "risk", "BTC 跨來源分歧是否代表操縱風險")
    assert context["retrieval"] == "sqlite_char_bigram_v1"
    assert context["matches"][0]["question"] == question
    assert context["matches"][0]["snapshot_id"] == snapshot
    assert context["matches"][0]["job_id"].startswith("flow-")
    assert context["matches"][0]["answer"]
    assert [message["role"] for message in context["conversation"]][-2:] == ["user", "hermes"]
    payload = flow.latest("BTC", "risk", question)
    assert "retrieval_context" in payload
    assert any(event["tool"] == "retrieval.question_memory" for event in payload["execution_log"])


def test_question_rag_prefers_same_coin_and_mode(tmp_path):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    flow.register_question("BTC", "risk", "BTC 市場風險與來源分歧", enqueue=False)
    flow.register_question("ETH", "news", "ETH 市場風險與來源分歧", enqueue=False)
    matches = flow.question_context("BTC", "risk", "市場風險與來源分歧")["matches"]
    assert matches
    assert {match["source_tier"] for match in matches} == {"historical_non_evidentiary"}
    assert matches[0]["coin"] == "BTC"
    assert matches[0]["mode"] == "risk"


def test_runtime_reconciles_orphaned_intermediate_stage_from_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    job_id = flow.enqueue_job(snapshot, "risk", "BTC 來源是否分歧")
    assert job_id
    # Simulate a process losing its in-memory package after durably checkpointing
    # an intermediate stage. The source queue from enqueue_job is deliberately
    # drained so reconciliation has one unambiguous package to rebuild.
    _, _, _ = flow._queues["source_ingestion"].get_nowait()
    flow._queues["source_ingestion"].task_done()
    flow._checkpoint(job_id, "claim_extraction", "queued")
    monkeypatch.setattr(flow, "_spawn_worker", lambda *_args: None)

    repaired = flow.reconcile_runtime()

    assert repaired["jobs"] == 1
    job = flow._job(job_id)
    assert job["current_stage"] == "source_ingestion"
    assert flow._queues["source_ingestion"].qsize() == 1
    assert flow._conn().execute(
        "SELECT count(*) FROM analysis_stage_runs WHERE job_id=? AND stage='claim_extraction'",
        (job_id,),
    ).fetchone()[0] == 0
    flow.stop()


def test_local_daemon_runs_overlapping_workers_per_stage():
    with open("deploy/launchd/com.hurricanesoft.trustforge-analysis-flow.plist", "rb") as handle:
        arguments = plistlib.load(handle)["ProgramArguments"]

    index = arguments.index("--workers-per-stage")
    assert int(arguments[index + 1]) >= 2
    index = arguments.index("--schedule-seconds")
    assert int(arguments[index + 1]) == 1800


def test_production_analysis_scheduler_is_bounded_and_timer_driven():
    installer = open("deploy/install_hermes_scheduler.sh", encoding="utf-8").read()

    daemon_line = next(line for line in installer.splitlines() if "run_analysis_flow.py --daemon" in line)
    assert "--schedule-seconds" not in daemon_line
    assert "run_analysis_flow.py --enqueue-scheduled --schedule-seconds 1800" in installer
    assert "OnUnitActiveSec=30min" in installer
    assert "TimeoutStartSec=900" in installer


def test_readonly_projection_skips_schema_writes_and_reads_existing_state(tmp_path):
    path = tmp_path / "flow.sqlite3"
    writer = AnalysisFlow(path)
    writer.register_question("BTC", "risk", "BTC 來源是否分歧", enqueue=False)
    writer.close()

    with AnalysisFlow(path, readonly=True) as reader:
        context = reader.question_context("BTC", "risk", "BTC 來源是否分歧")
        assert context["matches"][0]["coin"] == "BTC"

    missing = tmp_path / "missing.sqlite3"
    with AnalysisFlow(missing, readonly=True) as reader:
        status = reader.status()
        journey = reader.journey()
        context = reader.question_context("BTC", "risk", "BTC 來源是否分歧")
        latest = reader.latest("BTC", "risk")
    assert status["queue"]["pending"] == 0
    assert all(stage["queued"] == 0 and stage["current"] is None for stage in status["stages"])
    assert journey["jobs"] == []
    assert journey["dead_letters"] == []
    assert context["matches"] == []
    assert context["conversation"] == []
    assert latest is None
    assert not missing.exists()


def test_readonly_pre_scheduler_database_returns_empty_scheduler_state(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE analysis_jobs(job_id TEXT)")
    connection.commit()
    connection.close()

    with AnalysisFlow(path, readonly=True) as reader:
        assert reader.scheduler_status() == {"next_run_at": None, "recent_runs": []}
