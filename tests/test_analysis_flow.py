from __future__ import annotations

import time

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


def test_same_snapshot_matrix_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *args, **kwargs: _docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    assert len(flow.enqueue_matrix(snapshot)) == len(MODES)
    assert flow.enqueue_matrix(snapshot) == []


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
    assert matches[0]["coin"] == "BTC"
    assert matches[0]["mode"] == "risk"
