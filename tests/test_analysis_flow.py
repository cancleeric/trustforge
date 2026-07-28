from __future__ import annotations

import time
import sqlite3
import fcntl
import threading
import importlib.util
import sys
from pathlib import Path

import pytest

from trustforge import budget_guard
from trustforge import analysis_flow as analysis_flow_module
from trustforge.analysis_flow import AnalysisFlow, MODES, STAGES
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document

ROOT = Path(__file__).resolve().parents[1]

_PRICED_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


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


def test_concurrent_manual_request_is_deduplicated_across_flow_instances(tmp_path, monkeypatch):
    calls = 0
    collect_started = threading.Event()
    contention_seen = threading.Event()
    release_collect = threading.Event()
    real_flock = fcntl.flock
    def observed_flock(fd, operation):
        try:
            return real_flock(fd, operation)
        except BlockingIOError:
            contention_seen.set()
            raise

    def collect(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        collect_started.set()
        assert release_collect.wait(timeout=5)
        return _docs()

    monkeypatch.setattr("trustforge.analysis_flow.collect", collect)
    monkeypatch.setattr("trustforge.analysis_flow.fcntl.flock", observed_flock)
    path = tmp_path / "flow.sqlite3"
    flows = [AnalysisFlow(path), AnalysisFlow(path)]
    question = "BTC 是否出現新的跨來源分歧？"
    results: list[tuple[str, str | None]] = []
    threads = [
        threading.Thread(target=lambda flow=flow: results.append(flow.submit_manual("BTC", "risk", question)))
        for flow in flows
    ]

    threads[0].start()
    assert collect_started.wait(timeout=5)
    threads[1].start()
    assert contention_seen.wait(timeout=5)
    assert calls == 1
    release_collect.set()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert len(results) == 2
    assert results[0][1] == results[1][1]
    assert calls == 1
    assert flows[0]._conn().execute(
        "SELECT count(*) FROM analysis_jobs WHERE origin='manual'",
    ).fetchone()[0] == 1
    for flow in flows:
        flow.stop()


def test_different_manual_request_keys_collect_concurrently(tmp_path, monkeypatch):
    collectors = threading.Barrier(2)
    calls = 0
    calls_lock = threading.Lock()

    def collect(*_args, **_kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        collectors.wait(timeout=5)
        return _docs()

    monkeypatch.setattr("trustforge.analysis_flow.collect", collect)
    path = tmp_path / "flow.sqlite3"
    flows = [AnalysisFlow(path), AnalysisFlow(path)]
    results: list[tuple[str, str | None]] = []
    threads = [
        threading.Thread(target=lambda: results.append(flows[0].submit_manual("BTC", "risk", "BTC risk"))),
        threading.Thread(target=lambda: results.append(flows[1].submit_manual("ETH", "risk", "ETH risk"))),
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert calls == 2
    assert len(results) == 2
    assert results[0][1] != results[1][1]
    for flow in flows:
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


def test_stale_running_job_is_requeued_with_locale_preserved(tmp_path, monkeypatch):
    # N16: a job whose worker thread is still `is_alive()` but hung (or whose
    # owning process crashed and never restarted) is left with state='running'
    # and a frozen updated_at forever, since reconcile_runtime() only detects
    # dead threads. reap_stale_running() must find it via the updated_at
    # threshold and route it back through the existing retry path, carrying
    # the N11 locale (not silently falling back to Chinese).
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    job_id = flow.enqueue_job(snapshot, "risk", "BTC 來源是否分歧", locale="en")
    assert job_id
    # Drain the fresh in-process package: it represents runtime state that a
    # crashed/hung process would not actually have.
    flow._queues["source_ingestion"].get_nowait()
    flow._queues["source_ingestion"].task_done()
    stale_updated_at = time.time() - 9999
    flow._conn().execute(
        "UPDATE analysis_jobs SET state='running',current_stage='claim_extraction',updated_at=? WHERE job_id=?",
        (stale_updated_at, job_id),
    )
    flow._conn().execute(
        "INSERT INTO analysis_stage_runs(job_id,stage,state,queue_entered_at,started_at,finished_at,duration_sec,event_count,retry_count,error)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (job_id, "claim_extraction", "running", stale_updated_at, stale_updated_at, None, None, 0, 0, None),
    )

    reaped = flow.reap_stale_running(threshold_seconds=1)

    assert reaped == 1
    job = flow._job(job_id)
    assert job["state"] == "queued"
    assert job["retry_count"] == 1
    retry_row = flow._conn().execute(
        "SELECT stage FROM analysis_retry_queue WHERE job_id=?", (job_id,),
    ).fetchone()
    assert retry_row["stage"] == "claim_extraction"

    adopted = flow.adopt_due_retries()
    assert adopted == 1
    _, _, package = flow._queues["source_ingestion"].get_nowait()
    assert package["job_id"] == job_id
    assert package["locale"] == "en"
    flow.stop()


def test_stale_running_job_enters_dead_letter_after_retry_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    job_id = flow.enqueue_job(snapshot, "risk", "BTC 來源是否分歧")
    assert job_id
    flow._queues["source_ingestion"].get_nowait()
    flow._queues["source_ingestion"].task_done()
    stale_updated_at = time.time() - 9999
    flow._conn().execute(
        "UPDATE analysis_jobs SET state='running',current_stage='trust_reasoning',retry_count=2,updated_at=? WHERE job_id=?",
        (stale_updated_at, job_id),
    )

    reaped = flow.reap_stale_running(threshold_seconds=1)

    assert reaped == 1
    job = flow._job(job_id)
    assert job["state"] == "failed"
    dead = flow._conn().execute(
        "SELECT stage,attempts FROM analysis_dead_letters WHERE job_id=?", (job_id,),
    ).fetchone()
    assert dead["stage"] == "trust_reasoning"
    assert dead["attempts"] == 3
    assert flow._conn().execute(
        "SELECT 1 FROM analysis_retry_queue WHERE job_id=?", (job_id,),
    ).fetchone() is None
    flow.stop()


def test_local_daemon_runs_overlapping_workers_per_stage():
    spec = importlib.util.spec_from_file_location(
        "analysis_launch_agent", ROOT / "scripts/install_launch_agent.py"
    )
    assert spec and spec.loader
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    arguments = generator.payload(
        "analysis", ROOT.resolve(), Path(sys.executable).resolve(), None
    )["ProgramArguments"]

    index = arguments.index("--workers-per-stage")
    assert int(arguments[index + 1]) >= 2
    index = arguments.index("--schedule-seconds")
    assert int(arguments[index + 1]) == 1800


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


# ---------------------------------------------------------------------------
# Live Bedrock gating for the daemon STAGES pipeline (`_stage_claim_extraction`
# / `_stage_trust_reasoning` / `_stage_evidence_assembly`), reusing
# `web._bedrock_allowed()` + `budget_guard`'s cap/reservation/pricing checks
# instead of the previous hardcoded `offline=True`.
# ---------------------------------------------------------------------------


class _FakeLiveBedrockClient:
    """Stand-in for `BedrockClient` in live-gate tests: never touches boto3/AWS,
    but mirrors the `.offline` / `.config.model_id` / `extract_claims_with_llm`
    surface `_stage_claim_extraction` relies on, and records a real `llm.cost`
    log event when "live" so the ledger-accounting path can be exercised."""

    def __init__(self, offline: bool = False):
        self.offline = offline
        self.config = type("Cfg", (), {"model_id": _PRICED_MODEL_ID if not offline else None})()

    def extract_claims_with_llm(self, docs, log=None):
        if not self.offline and log is not None:
            log.record_llm_cost(self.config.model_id, tokens_in=100, tokens_out=50, cost_usd=0.01)
        return []


def _claim_extraction_package() -> dict:
    return {
        "docs": _docs(),
        "log": ExecutionLog(run_id="test-run"),
        "job": {"question": "BTC 近期風險？", "coin": "BTC", "question_type": "multi_source"},
    }


def test_claim_extraction_stays_offline_when_bedrock_gate_closed(tmp_path):
    """預設測試環境沒有 BEDROCK_MODEL_ID／live 閘關閉 → 維持離線，不記帳。"""
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    package = flow._stage_claim_extraction(_claim_extraction_package())

    assert package["client"].offline is True
    llm_event = next(e for e in package["log"].events if e["tool"] == "bedrock.complete")
    assert llm_event["params"]["llm_active"] is False
    assert budget_guard.daily_cost_usd() == 0


def test_claim_extraction_goes_live_when_gate_open_and_budget_available(tmp_path, monkeypatch):
    """gate 開＋budget 有額度 → offline=False、llm_active=True，且真花費透過
    `_bedrock_live_attempt()` 補記進 `ledger.append_run()`，讓 `daily_cost_usd()`
    看得到——證明這條管線的 Bedrock 呼叫確實仍過 budget_guard 的每日 cap 帳本。
    """
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda *a, **k: True)
    monkeypatch.setattr("trustforge.analysis_flow.BedrockClient", _FakeLiveBedrockClient)
    monkeypatch.setenv("BEDROCK_MODEL_ID", _PRICED_MODEL_ID)

    assert budget_guard.daily_cost_usd() == 0
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    package = flow._stage_claim_extraction(_claim_extraction_package())

    assert package["client"].offline is False
    llm_event = next(e for e in package["log"].events if e["tool"] == "bedrock.complete")
    assert llm_event["params"]["llm_active"] is True
    assert budget_guard.daily_cost_usd() == pytest.approx(0.01)


def test_claim_extraction_forced_offline_when_daily_cap_exceeded(tmp_path, monkeypatch):
    """gate 開，但每日 cap 已達標 → 強制離線，不得繞過 budget_guard 的 cap。"""
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda *a, **k: True)
    monkeypatch.setattr("trustforge.analysis_flow.BedrockClient", _FakeLiveBedrockClient)
    monkeypatch.setenv("BEDROCK_MODEL_ID", _PRICED_MODEL_ID)
    monkeypatch.setattr(budget_guard, "daily_cap_exceeded", lambda *a, **k: True)

    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    package = flow._stage_claim_extraction(_claim_extraction_package())

    assert package["client"].offline is True
    assert budget_guard.daily_cost_usd() == 0


def test_claim_extraction_fail_closed_when_gate_raises(tmp_path, monkeypatch):
    """live 閘判定本身炸例外 → fail-closed 維持離線，例外不得外傳。"""
    def _boom(*a, **k):
        raise RuntimeError("gate blew up")

    monkeypatch.setattr("trustforge.web._bedrock_allowed", _boom)
    monkeypatch.setattr("trustforge.analysis_flow.BedrockClient", _FakeLiveBedrockClient)
    monkeypatch.setenv("BEDROCK_MODEL_ID", _PRICED_MODEL_ID)

    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    package = flow._stage_claim_extraction(_claim_extraction_package())

    assert package["client"].offline is True
    assert budget_guard.daily_cost_usd() == 0


def test_trust_reasoning_passes_client_offline_state_to_score(tmp_path, monkeypatch):
    """Step2 resolution 的 ``offline`` 必須反映 Step1 真實狀態。"""
    captured = {}
    from trustforge.agent import kernel_mapper
    real_resolver = kernel_mapper.resolve_kernel_run_resolution

    def _capturing_resolver(claims, now, **kwargs):
        offline = kwargs.get("offline")
        captured["offline"] = offline
        return real_resolver(claims, now, **kwargs)

    monkeypatch.setattr(kernel_mapper, "resolve_kernel_run_resolution", _capturing_resolver)

    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    package = _claim_extraction_package()
    package["client"] = _FakeLiveBedrockClient(offline=False)
    package["claims"] = []
    flow._stage_trust_reasoning(package)

    assert captured["offline"] is False


def test_evidence_assembly_rechecks_gate_and_can_flip_to_offline_when_cap_hits_between_steps(
    tmp_path, monkeypatch,
):
    """Step1 判定 live 後、排隊等到 Step3 之間 cap 才被打滿：Step3 必須重新
    走一次獨立閘判定，不得沿用 Step1 過期的 live 決定去打真 Bedrock。"""
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda *a, **k: True)
    monkeypatch.setattr("trustforge.analysis_flow.budget_guard.daily_cap_exceeded", lambda *a, **k: True)
    monkeypatch.setenv("BEDROCK_MODEL_ID", _PRICED_MODEL_ID)

    captured = {}

    def _fake_build_report(question, coin, qtype, brief, *, client, log, stance_fn, scored,
                           kernel_judgment,
                           locale="zh-Hant"):
        captured["offline_at_call"] = client.offline
        return {"report": "stub"}, {"evidence": "stub"}

    monkeypatch.setattr("trustforge.analysis_flow.build_report", _fake_build_report)

    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    package = _claim_extraction_package()
    package["client"] = _FakeLiveBedrockClient(offline=False)  # Step1 曾判 live
    package["claims"] = []
    package["scored"] = []
    package["brief"] = None
    package["stance"] = None
    package["kernel_judgment"] = object()
    flow._stage_evidence_assembly(package)

    assert captured["offline_at_call"] is True
    assert budget_guard.daily_cost_usd() == 0


def test_bedrock_live_attempt_records_ledger_before_releasing_reservation(tmp_path, monkeypatch):
    """codex + harper CISO 雙審 HIGH（TOCTOU）：`_bedrock_live_attempt()` 的
    finally 必須先把花費寫進帳本（或落 unledgered fallback），才可以釋放
    預留。反過來（先 release 後記帳）會在記帳完成前留出空窗：並發的另一個
    daemon job 這時呼叫 `try_reserve_request_budget()` 會讀到偏低的
    `daily_cost_usd()`，誤判還有額度，繞過 $/天 cap。

    這裡把 `append_run` 模擬成延遲（掛起一小段時間才回傳），驗證
    `release_request_budget` 只會在 `append_run` 真的跑完之後才被呼叫。
    全程 mock，不打真 Bedrock。"""
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda *a, **k: True)
    monkeypatch.setattr("trustforge.analysis_flow.BedrockClient", _FakeLiveBedrockClient)
    monkeypatch.setenv("BEDROCK_MODEL_ID", _PRICED_MODEL_ID)

    order = []
    real_append_run = analysis_flow_module.append_run
    real_release = budget_guard.release_request_budget

    def _slow_append_run(record):
        time.sleep(0.05)  # 模擬帳本寫入被延遲/掛起
        result = real_append_run(record)
        order.append("append_run")
        return result

    def _tracking_release(amount):
        order.append("release")
        return real_release(amount)

    monkeypatch.setattr("trustforge.analysis_flow.append_run", _slow_append_run)
    monkeypatch.setattr("trustforge.analysis_flow.budget_guard.release_request_budget", _tracking_release)

    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    flow._stage_claim_extraction(_claim_extraction_package())

    assert order == ["append_run", "release"]
    # 記帳真的先落地成功，非只是呼叫順序對但實際沒寫入。
    assert budget_guard.daily_cost_usd() == pytest.approx(0.01)


def test_bedrock_live_attempt_releases_reservation_even_when_ledger_accounting_raises(
    tmp_path, monkeypatch,
):
    """記帳區塊本身丟非預期例外時，release 仍必須執行（不可讓預留卡死
    漏放）——巢狀 finally 的另一半保證。"""
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda *a, **k: True)
    monkeypatch.setattr("trustforge.analysis_flow.BedrockClient", _FakeLiveBedrockClient)
    monkeypatch.setenv("BEDROCK_MODEL_ID", _PRICED_MODEL_ID)

    def _boom(record):
        raise RuntimeError("ledger backend on fire")

    released = {}
    real_release = budget_guard.release_request_budget

    def _tracking_release(amount):
        released["amount"] = amount
        return real_release(amount)

    monkeypatch.setattr("trustforge.analysis_flow.append_run", _boom)
    monkeypatch.setattr("trustforge.analysis_flow.budget_guard.release_request_budget", _tracking_release)

    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    # 不得讓例外外傳到呼叫端（daemon 一個 job 記帳故障不該讓整個 stage 掛掉）。
    flow._stage_claim_extraction(_claim_extraction_package())

    assert released.get("amount") is not None
    # append_run 丟例外 → 記帳走 unledgered fallback，仍算進今日已花費。
    assert budget_guard.daily_cost_usd() == pytest.approx(0.01)
