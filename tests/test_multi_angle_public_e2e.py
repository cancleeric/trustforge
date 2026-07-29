"""Public five-angle journey through HTTP, durable SQLite, workers, and synthesis."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import threading
import time
from urllib.request import Request, urlopen

import pytest

from trustforge import analysis_flow, web
from trustforge.analysis_flow import AnalysisFlow, MODES
from trustforge.ingestion.base import Document


@pytest.mark.serial
def test_public_http_multi_angle_runs_real_durable_pipeline(
    tmp_path, monkeypatch
) -> None:
    """Only external source/model/cost seams are deterministic.

    Handler routing, submit_multi_angle, SQLite handoff, all five real stages,
    synthesis, and GET projection remain production implementations.
    """
    db_path = tmp_path / "public-multi-angle.sqlite3"
    monkeypatch.setattr(analysis_flow, "_db_path", lambda path=None: db_path)
    monkeypatch.setattr(
        analysis_flow,
        "collect",
        lambda query, coin=None, **_kwargs: [
            Document(
                id="official-1",
                kind="regulatory",
                source="official-test-source",
                text=f"{coin} official filing confirms reserves and operating status.",
                url="https://example.invalid/official-1",
                ts=time.time(),
            ),
            Document(
                id="market-1",
                kind="news",
                source="market-test-source",
                text=f"{coin} market activity is stable with independently reported volume.",
                url="https://example.invalid/market-1",
                ts=time.time(),
            ),
        ],
    )
    monkeypatch.setattr(
        analysis_flow.budget_guard, "request_budget_available", lambda count: count == 5
    )
    monkeypatch.setattr(web, "_bedrock_allowed", lambda: False)

    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    daemon = None
    try:
        request = Request(
            f"{base_url}/api/multi-angle",
            data=json.dumps({"coin": "BTC", "locale": "zh-Hant"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            submitted = json.load(response)
        assert submitted["ok"] is True
        snapshot_id = submitted["data"]["snapshot_id"]
        assert set(submitted["data"]["job_ids"]) == set(MODES)

        # This is a distinct flow instance, matching the production web/daemon
        # process boundary and forcing adoption from the durable database.
        daemon = AnalysisFlow(path=db_path)
        daemon.start()

        deadline = time.monotonic() + 20
        result = None
        while time.monotonic() < deadline:
            with urlopen(
                f"{base_url}/api/multi-angle?coin=BTC&snapshot_id={snapshot_id}",
                timeout=3,
            ) as response:
                status = json.load(response)
            result = status["data"]["multi_angle"]
            if result is not None:
                break
            time.sleep(0.05)

        assert result is not None, "bounded daemon journey did not publish synthesis"
        assert result["snapshot_id"] == snapshot_id
        assert result["coin"] == "BTC"
        assert {angle["angle"] for angle in result["angles"]} == set(MODES)

        rows = daemon._conn().execute(
            "SELECT mode,state FROM analysis_jobs WHERE snapshot_id=? ORDER BY mode",
            (snapshot_id,),
        ).fetchall()
        assert {row["mode"]: row["state"] for row in rows} == {
            mode: "completed" for mode in MODES
        }
        job_ids = set(submitted["data"]["job_ids"].values())
        stage_rows = daemon._conn().execute(
            "SELECT job_id,stage,state FROM analysis_stage_runs "
            "WHERE job_id IN (?,?,?,?,?)",
            tuple(job_ids),
        ).fetchall()
        assert {
            (row["job_id"], row["stage"], row["state"]) for row in stage_rows
        } == {
            (job_id, stage, "completed")
            for job_id in job_ids
            for stage in analysis_flow.STAGES
        }
        assert daemon._conn().execute(
            "SELECT count(*) FROM analysis_results "
            "WHERE snapshot_id=? AND mode='multi_angle'",
            (snapshot_id,),
        ).fetchone()[0] == 1
        lineage_types = [
            event["event_type"]
            for event in daemon.lineage(snapshot_id=snapshot_id)
        ]
        assert lineage_types.count("multi_angle_submitted") == 1
        assert lineage_types.count("multi_angle_synthesized") == 1
    finally:
        if daemon is not None:
            daemon.stop()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
