"""CLI contract tests for the production multi-angle acceptance verifier."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "verify_multi_angle_production_snapshot.py"
)


def _load_verifier_module():
    spec = importlib.util.spec_from_file_location("verify_multi_angle_production_snapshot", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verifier_requires_deployed_snapshot_id():
    verifier = _load_verifier_module()

    with pytest.raises(SystemExit) as exc_info:
        verifier.parse_args([])

    assert exc_info.value.code == 2


def test_verifier_accepts_new_snapshot_id_and_shared_store_path(tmp_path):
    verifier = _load_verifier_module()
    store = tmp_path / "shared-production.sqlite3"

    args = verifier.parse_args([
        "snap-btc-new-production-run",
        "--sqlite-path",
        str(store),
    ])

    assert args.snapshot_id == "snap-btc-new-production-run"
    assert args.sqlite_path == store


def _production_store(verifier, path: Path, *, mutation: str | None = None) -> str:
    snapshot_id = "snap-btc-new-production-run"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE analysis_snapshots(snapshot_id TEXT PRIMARY KEY, coin TEXT);
        CREATE TABLE analysis_jobs(
          job_id TEXT PRIMARY KEY, snapshot_id TEXT, mode TEXT,
          question TEXT, state TEXT
        );
        CREATE TABLE analysis_stage_runs(job_id TEXT, stage TEXT, state TEXT);
        CREATE TABLE analysis_results(
          job_id TEXT, snapshot_id TEXT, coin TEXT, mode TEXT,
          payload_json TEXT, published_at REAL
        );
        """
    )
    conn.execute(
        "INSERT INTO analysis_snapshots VALUES(?, 'BTC')", (snapshot_id,)
    )
    for index, mode in enumerate(sorted(verifier.MODES)):
        job_id = f"job-{mode}"
        question = f"{mode} question {index}"
        conn.execute(
            "INSERT INTO analysis_jobs VALUES(?,?,?,?,?)",
            (job_id, snapshot_id, mode, question, "completed"),
        )
        conn.executemany(
            "INSERT INTO analysis_stage_runs VALUES(?,?, 'completed')",
            [(job_id, stage) for stage in verifier.REQUIRED_STAGES],
        )
        receipt = {
            "contract_version": verifier._CLAIM_EXTRACTION_CONTEXT_VERSION,
            "mode": mode,
            "question_sha256": verifier._sha256(question),
            "prompt_context_sha256": verifier._sha256(
                verifier.claim_extraction_prompt_context(mode, question)
            ),
            "model_invoked": True,
        }
        if mutation == "model_not_invoked" and index == 0:
            receipt["model_invoked"] = False
        if mutation == "bad_hash" and index == 0:
            receipt["prompt_context_sha256"] = "0" * 64
        events = [{
            "tool": "bedrock.claim_extraction_context",
            "params": dict(receipt),
        }]
        if mutation == "missing_execution_receipt" and index == 0:
            events = []
        payload = {
            "claim_extraction_context": receipt,
            "execution_log": events,
        }
        conn.execute(
            "INSERT INTO analysis_results VALUES(?,?,?,?,?,?)",
            (job_id, snapshot_id, "BTC", mode, json.dumps(payload), 1.0),
        )
    synthesis = {
        "direction_divergences": [],
        "completeness_gaps": [],
        "evidence_overlaps": [],
        "evidence_independence": 0,
        "limits": ["沒有獨立交叉佐證；重複來源不可視為驗證。"],
    }
    if mutation == "missing_synthesis_limit":
        synthesis["limits"] = []
    conn.execute(
        "INSERT INTO analysis_results VALUES(?,?,?,?,?,?)",
        ("job-synthesis", snapshot_id, "BTC", "multi_angle",
         json.dumps(synthesis, ensure_ascii=False), 2.0),
    )
    conn.commit()
    conn.close()
    return snapshot_id


def test_verifier_accepts_complete_readonly_production_projection(tmp_path, capsys):
    verifier = _load_verifier_module()
    store = tmp_path / "production.sqlite3"
    snapshot_id = _production_store(verifier, store)

    verifier.main([snapshot_id, "--sqlite-path", str(store)])

    assert json.loads(capsys.readouterr().out)["ok"] is True
    conn = verifier._connect_readonly(store)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM analysis_jobs")
    conn.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "model_not_invoked",
        "bad_hash",
        "missing_execution_receipt",
        "missing_synthesis_limit",
    ],
)
def test_verifier_fails_closed_on_invalid_production_evidence(
    tmp_path, mutation,
):
    verifier = _load_verifier_module()
    store = tmp_path / f"{mutation}.sqlite3"
    snapshot_id = _production_store(verifier, store, mutation=mutation)

    with pytest.raises(SystemExit) as exc_info:
        verifier.main([snapshot_id, "--sqlite-path", str(store)])

    assert exc_info.value.code == 1
