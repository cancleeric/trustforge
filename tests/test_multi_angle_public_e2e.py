"""Public five-angle journey through HTTP, durable SQLite, workers, and synthesis."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from trustforge import analysis_flow, web
from trustforge.analysis_flow import AnalysisFlow, MODES
from trustforge.ingestion.base import Document


def _post(base_url, key, body=None):
    request = Request(
        f"{base_url}/api/multi-angle",
        data=json.dumps(body or {"coin": "BTC", "locale": "zh-Hant"}).encode(),
        headers={"Content-Type": "application/json", "Idempotency-Key": key},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


@pytest.fixture
def shared_public_server(tmp_path, monkeypatch):
    db_path = tmp_path / "shared-public.sqlite3"
    monkeypatch.setattr(
        web.rate_limit_store, "try_increment", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(analysis_flow, "_db_path", lambda path=None: db_path)
    monkeypatch.setattr(
        analysis_flow, "collect",
        lambda query, coin=None, **_kwargs: [
            Document(
                id="official", kind="regulatory", source="official",
                text=f"{coin} durable source", url="https://example.invalid",
                ts=time.time(),
            )
        ],
    )
    monkeypatch.setattr(web, "_bedrock_allowed", lambda: False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", db_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _counts(db_path):
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        def count(table):
            try:
                return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
                return 0
        return {
            "batches": count("atomic_batches"),
            "allocations": count("atomic_allocations"),
            "authority_jobs": count("atomic_jobs"),
            "local_jobs": count("analysis_jobs"),
            "projections": count("analysis_atomic_projection_queue"),
            "snapshots": count("analysis_snapshots"),
        }


def _wait_until(predicate, *, timeout=10):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        threading.Event().wait(0.02)
    raise AssertionError(f"bounded condition was not met; last={last!r}")


def _wait_for_completed_stages(daemon, job_ids, *, timeout=10):
    expected = {
        (job_id, stage)
        for job_id in job_ids
        for stage in analysis_flow.STAGES
    }
    placeholders = ",".join("?" for _ in job_ids)
    deadline = time.monotonic() + timeout
    observed = {}
    while time.monotonic() < deadline:
        rows = daemon._conn().execute(
            "SELECT job_id,stage,state FROM analysis_stage_runs "
            f"WHERE job_id IN ({placeholders})",
            tuple(job_ids),
        ).fetchall()
        observed = {
            (row["job_id"], row["stage"]): row["state"]
            for row in rows
        }
        if set(observed) == expected and all(
            state == "completed" for state in observed.values()
        ):
            return rows
        threading.Event().wait(0.02)

    missing = sorted(expected - set(observed))
    incomplete = sorted(
        (job_id, stage, state)
        for (job_id, stage), state in observed.items()
        if state != "completed"
    )
    unexpected = sorted(set(observed) - expected)
    raise AssertionError(
        "analysis stages did not reach completed before timeout; "
        f"missing={missing!r}; incomplete={incomplete!r}; "
        f"unexpected={unexpected!r}"
    )


@pytest.mark.serial
def test_public_http_multi_angle_runs_real_durable_pipeline(
    tmp_path, monkeypatch
) -> None:
    """Only external source/model/cost seams are deterministic.

    Handler routing, submit_multi_angle, SQLite handoff, all five real stages,
    synthesis, and GET projection remain production implementations.
    """
    db_path = tmp_path / "public-multi-angle.sqlite3"
    monkeypatch.setattr(
        web.rate_limit_store,
        "try_increment",
        lambda *_args, **_kwargs: True,
    )
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
    ledger_records = []
    def append_ledger(record):
        record.setdefault("run_id", uuid.uuid4().hex)
        ledger_records.append(dict(record))
        return True
    monkeypatch.setattr(
        analysis_flow, "append_run", append_ledger,
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    daemon = None
    try:
        request = Request(
            f"{base_url}/api/multi-angle",
            data=json.dumps({"coin": "BTC", "locale": "zh-Hant"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": "public-e2e-run",
            },
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
        job_ids = tuple(submitted["data"]["job_ids"].values())
        stage_rows = _wait_for_completed_stages(daemon, job_ids)
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
        with daemon._atomic_store()._connect() as authority:
            assert authority.execute(
                "SELECT count(*) FROM atomic_batches"
            ).fetchone()[0] == 1
            assert authority.execute(
                "SELECT count(*) FROM atomic_allocations"
            ).fetchone()[0] == 5
            assert authority.execute(
                "SELECT count(*) FROM atomic_jobs"
            ).fetchone()[0] == 5
            assert authority.execute(
                "SELECT count(*) FROM atomic_call_costs"
            ).fetchone()[0] == 10
            assert authority.execute(
                "SELECT count(*) FROM atomic_job_outcomes"
            ).fetchone()[0] == 5
            assert authority.execute(
                "SELECT count(*) FROM atomic_settlements"
            ).fetchone()[0] == 1
            assert authority.execute(
                """SELECT count(*) FROM atomic_synthesis_claims
                   WHERE state='completed'"""
            ).fetchone()[0] == 1
            remaining, reserved = authority.execute(
                "SELECT remaining_usd,reserved_total FROM atomic_budget"
            ).fetchone()
            assert reserved == "0.000000"
            assert remaining == "1000.000000"
        assert len(ledger_records) == 10
        assert {
            record["accounting_outcome"] for record in ledger_records
        } == {"cancelled_offline"}
    finally:
        if daemon is not None:
            daemon.stop()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


@pytest.mark.serial
def test_same_key_concurrent_double_click_admits_one_batch(shared_public_server):
    base_url, db_path = shared_public_server
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: _post(base_url, "same-double"), range(2)))
    assert {code for code, _ in responses} <= {200, 202}
    identities = [
        payload["data"].get("snapshot_id") or payload["data"].get("request_id")
        for _, payload in responses
    ]
    assert all(identities)
    counts = _counts(db_path)
    assert (
        counts["batches"], counts["allocations"], counts["authority_jobs"],
        counts["local_jobs"], counts["projections"],
    ) == (1, 5, 5, 5, 0)


@pytest.mark.serial
def test_same_key_refresh_tabs_and_direct_http_replay_same_identity(
    shared_public_server,
):
    base_url, db_path = shared_public_server
    responses = [_post(base_url, "same-replay") for _ in range(4)]
    assert {code for code, _ in responses} == {200}
    snapshots = {payload["data"]["snapshot_id"] for _, payload in responses}
    jobs = {
        json.dumps(payload["data"]["job_ids"], sort_keys=True)
        for _, payload in responses
    }
    assert len(snapshots) == len(jobs) == 1
    counts = _counts(db_path)
    assert (counts["batches"], counts["authority_jobs"], counts["local_jobs"]) == (
        1, 5, 5
    )


@pytest.mark.serial
def test_different_keys_budget_allows_only_one_batch(
    shared_public_server, monkeypatch,
):
    base_url, db_path = shared_public_server
    exact_batch_budget = (
        analysis_flow.budget_guard.multi_angle_angle_max_cost_usd() * len(MODES)
    )
    monkeypatch.setenv(
        "TRUSTFORGE_ATOMIC_BATCH_LOCAL_REMAINING_USD",
        str(exact_batch_budget),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda key: _post(base_url, key), ("budget-a", "budget-b")
        ))
    assert sorted(code for code, _ in responses) == [200, 409]
    counts = _counts(db_path)
    assert (counts["batches"], counts["allocations"], counts["local_jobs"]) == (
        1, 5, 5
    )


@pytest.mark.serial
def test_budget_below_five_rejects_without_queue_mutation(
    shared_public_server, monkeypatch,
):
    base_url, db_path = shared_public_server
    monkeypatch.setenv(
        "TRUSTFORGE_ATOMIC_BATCH_LOCAL_REMAINING_USD", "0"
    )
    code, payload = _post(base_url, "budget-none")
    assert code == 409
    assert payload["error"]["code"] == "multi_angle_budget_unavailable"
    counts = _counts(db_path)
    assert (
        counts["batches"], counts["allocations"], counts["authority_jobs"],
        counts["local_jobs"], counts["projections"],
    ) == (0, 0, 0, 0, 0)
    assert counts["snapshots"] >= 0


@pytest.mark.serial
def test_shared_rate_limit_rejects_second_http_without_new_batch(
    shared_public_server, monkeypatch,
):
    base_url, db_path = shared_public_server
    calls = 0

    def limited(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return calls == 1

    monkeypatch.setattr(web.rate_limit_store, "try_increment", limited)
    assert _post(base_url, "rate-first")[0] == 200
    code, payload = _post(base_url, "rate-second")
    assert code == 429
    assert payload["error"]["code"] == "rate_limited"
    assert _counts(db_path)["batches"] == 1


@pytest.mark.serial
def test_shared_authority_failure_has_no_queue_or_reservation(
    shared_public_server, monkeypatch,
):
    base_url, db_path = shared_public_server

    def unavailable(_self):
        raise analysis_flow.MultiAngleAuthorityError("authority unavailable")

    monkeypatch.setattr(AnalysisFlow, "_atomic_store", unavailable)
    code, payload = _post(base_url, "authority-down")
    assert code == 503
    assert payload["error"]["code"] == "multi_angle_authority_unavailable"
    counts = _counts(db_path)
    assert (
        counts["batches"], counts["allocations"], counts["authority_jobs"],
        counts["local_jobs"], counts["projections"],
    ) == (0, 0, 0, 0, 0)


@pytest.mark.serial
def test_post_then_fresh_daemon_restart_completes_once(
    shared_public_server, monkeypatch,
):
    base_url, db_path = shared_public_server
    ledger = []

    def append(record):
        record.setdefault("run_id", uuid.uuid4().hex)
        ledger.append(dict(record))
        return True

    monkeypatch.setattr(analysis_flow, "append_run", append)
    code, submitted = _post(base_url, "restart-once")
    assert code == 200
    snapshot_id = submitted["data"]["snapshot_id"]
    with AnalysisFlow(path=db_path) as crashed:
        crashed.recover()
    daemon = AnalysisFlow(path=db_path)
    daemon.start()
    try:
        result = _wait_until(
            lambda: daemon.multi_angle_status("BTC", snapshot_id), timeout=15
        )
        assert result["snapshot_id"] == snapshot_id
    finally:
        daemon.stop()
    counts = _counts(db_path)
    assert (counts["batches"], counts["authority_jobs"]) == (1, 5)
    assert len(ledger) == 10


@pytest.mark.serial
def test_live_timeout_consumed_without_receipt_stays_uncertain_no_release(
    shared_public_server,
):
    base_url, db_path = shared_public_server
    code, submitted = _post(base_url, "uncertain-timeout")
    assert code == 200
    mode = "risk"
    job_id = submitted["data"]["job_ids"][mode]
    with AnalysisFlow(path=db_path) as flow:
        store = flow._atomic_store()
        batch_id = flow._conn().execute(
            "SELECT atomic_batch_id FROM analysis_jobs WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        amount = (
            analysis_flow.budget_guard.multi_angle_angle_max_cost_usd()
        )
        store.claim_allocation(
            batch_id=batch_id, mode=mode, job_id=job_id,
            owner_token="timeout-owner", config_version="local-v1",
            expected_amount_usd=analysis_flow.Decimal(str(amount)),
        )
        store.consume_call_slot(
            batch_id=batch_id, mode=mode, job_id=job_id,
            owner_token="timeout-owner", config_version="local-v1",
            expected_amount_usd=analysis_flow.Decimal(str(amount)),
            slot="claim_extraction",
        )
        report = store.reconcile_stale_batches(
            stale_before=int(time.time()) + 1, apply=True
        )
        assert report["uncertain"] == [batch_id]
        with store._connect() as conn:
            remaining, reserved = conn.execute(
                "SELECT remaining_usd,reserved_total FROM atomic_budget"
            ).fetchone()
            assert analysis_flow.Decimal(reserved) > 0
            assert analysis_flow.Decimal(remaining) < analysis_flow.Decimal("1000")


@pytest.mark.serial
def test_real_stage_claim_crash_before_projection_recovers_and_releases(
    shared_public_server, monkeypatch,
):
    base_url, db_path = shared_public_server
    code, submitted = _post(base_url, "claim-projection-crash")
    assert code == 200
    mode = "risk"
    job_id = submitted["data"]["job_ids"][mode]
    with AnalysisFlow(path=db_path) as flow:
        job = flow._job(job_id)
        store = flow._atomic_store()
        real_claim = store.claim_allocation

        def claim_then_process_dies(**kwargs):
            real_claim(**kwargs)
            raise KeyboardInterrupt("fault after authority claim")

        monkeypatch.setattr(store, "claim_allocation", claim_then_process_dies)
        with pytest.raises(KeyboardInterrupt, match="fault after authority claim"):
            flow._stage_claim_extraction({
                "job": job,
                "log": analysis_flow.ExecutionLog(run_id=job_id),
            })
        owner_token = analysis_flow._atomic_owner_token(
            job["atomic_batch_id"], mode, job_id
        )
        assert flow._conn().execute(
            "SELECT 1 FROM analysis_atomic_owners WHERE job_id=?", (job_id,)
        ).fetchone() is None
        with store._connect() as authority:
            assert tuple(authority.execute(
                """SELECT owner_token,state FROM atomic_allocations
                   WHERE batch_id=? AND mode=?""",
                (job["atomic_batch_id"], mode),
            ).fetchone()) == (owner_token, "claimed")

        # The stale-job reaper durably records the interrupted worker. A fresh
        # process must reconstruct the exact original owner from immutable ids.
        flow._conn().execute(
            "UPDATE analysis_jobs SET state='failed' WHERE job_id=?",
            (job_id,),
        )
        flow._conn().execute(
            "INSERT INTO analysis_dead_letters VALUES(?,?,?,?,?,?,?,?,?)",
            (
                job_id, "claim_extraction", job["coin"], job["mode"],
                job["question"], job["snapshot_id"], 3,
                "provider timeout before call", time.time(),
            ),
        )
    with AnalysisFlow(path=db_path) as recovered:
        assert recovered._recover_atomic_terminals() == 1
        projection = recovered._conn().execute(
            """SELECT batch_id,mode,owner_token FROM analysis_atomic_owners
               WHERE job_id=?""",
            (job_id,),
        ).fetchone()
        assert tuple(projection) == (
            job["atomic_batch_id"], mode, owner_token
        )
        statuses = recovered._atomic_store().call_accounting_state(
            batch_id=job["atomic_batch_id"], mode=mode, job_id=job_id,
            owner_token=owner_token,
        )
        assert set(statuses.values()) == {"receipted"}
        with recovered._atomic_store()._connect() as authority:
            assert authority.execute(
                """SELECT count(*) FROM atomic_call_costs
                   WHERE job_id=? AND ledger_receipt
                     LIKE 'cancelled-before-call:%'""",
                (job_id,),
            ).fetchone()[0] == 2


@pytest.mark.serial
def test_final_timeout_before_calls_cancels_slots_settles_without_synthesis(
    shared_public_server, monkeypatch,
):
    base_url, db_path = shared_public_server
    code, submitted = _post(base_url, "final-timeout")
    assert code == 200
    monkeypatch.setattr(
        AnalysisFlow, "_stage_claim_extraction",
        lambda _self, _package: (_ for _ in ()).throw(
            TimeoutError("provider timeout before call")
        ),
    )
    daemon = AnalysisFlow(path=db_path)
    daemon.start()
    try:
        try:
            _wait_until(
                lambda: daemon._conn().execute(
                    "SELECT count(*) FROM analysis_dead_letters"
                ).fetchone()[0] == 5,
                timeout=15,
            )
        except AssertionError as exc:
            diagnostics = {
                "jobs": [tuple(row) for row in daemon._conn().execute(
                    """SELECT job_id,state,current_stage,retry_count
                       FROM analysis_jobs ORDER BY job_id"""
                )],
                "retry_queue": [tuple(row) for row in daemon._conn().execute(
                    "SELECT job_id,stage,attempt FROM analysis_retry_queue"
                )],
                "dead_letters": [tuple(row) for row in daemon._conn().execute(
                    "SELECT job_id,stage,attempts FROM analysis_dead_letters"
                )],
                "attempts": [tuple(row) for row in daemon._conn().execute(
                    """SELECT job_id,stage,attempt,state,retryable
                       FROM analysis_stage_attempts ORDER BY job_id,attempt"""
                )],
                "owners": [tuple(row) for row in daemon._conn().execute(
                    "SELECT job_id,batch_id,mode FROM analysis_atomic_owners"
                )],
                "allocations": [tuple(row) for row in daemon._conn().execute(
                    """SELECT job_id,state,claim_extraction_slot,
                              evidence_narrative_slot
                       FROM atomic_allocations ORDER BY job_id"""
                )],
            }
            raise AssertionError(f"{exc}; diagnostics={diagnostics}") from exc
    finally:
        daemon.stop()
    batch_id = daemon._conn().execute(
        "SELECT atomic_batch_id FROM analysis_jobs LIMIT 1"
    ).fetchone()[0]
    attempts = daemon._conn().execute(
        """SELECT job_id,group_concat(attempt, ',') AS attempts
           FROM analysis_stage_attempts GROUP BY job_id ORDER BY job_id"""
    ).fetchall()
    assert len(attempts) == 5
    assert {row["attempts"] for row in attempts} == {"1,2,3"}
    with daemon._atomic_store()._connect() as authority:
        assert authority.execute(
            """SELECT count(*) FROM atomic_job_outcomes
               WHERE state='timeout'"""
        ).fetchone()[0] == 5
        assert authority.execute(
            "SELECT count(*) FROM atomic_call_costs"
        ).fetchone()[0] == 10
        assert authority.execute(
            "SELECT count(*) FROM atomic_settlements WHERE batch_id=?",
            (batch_id,),
        ).fetchone()[0] == 1
        assert authority.execute(
            "SELECT count(*) FROM atomic_synthesis_claims"
        ).fetchone()[0] == 0
