from __future__ import annotations

import gc
import hashlib
import sqlite3
import time
import warnings
from decimal import Decimal

import pytest

from trustforge.analysis_flow import (
    AnalysisFlow,
    MultiAngleAuthorityError,
    _bedrock_live_attempt,
)
from trustforge.execlog import ExecutionLog
from trustforge.multi_angle_batch_store import (
    ANGLE_MODES,
    AtomicBatchRequest,
    BatchStoreIntegrityError,
    DynamoDBAtomicMultiAngleBatchStore,
    SQLiteAtomicMultiAngleBatchStore,
    _job_ids,
)


def _accounting_token(mode, slot):
    return hashlib.sha256(f"{mode}:{slot}".encode()).hexdigest()


def _prepared_store(tmp_path, *, terminal_count=5):
    store = SQLiteAtomicMultiAngleBatchStore(str(tmp_path / "settle.db"))
    store.bootstrap_budget(
        day="2026-07-29", remaining_usd=Decimal(1), config_version="v1"
    )
    request = AtomicBatchRequest(
        batch_id="batch-settle", caller_hash="a" * 64,
        idempotency_key_hash="b" * 64, request_fingerprint="c" * 64,
        coin="BTC", snapshot_id="snap-settle", day="2026-07-29",
        batch_cost_usd=Decimal("0.50"), config_version="v1",
        created_at=1_785_283_200,
    )
    result = store.create_batch(request)
    for index, (mode, job_id) in enumerate(
        zip(ANGLE_MODES, result.job_ids, strict=True)
    ):
        owner = f"owner-{mode}"
        store.claim_allocation(
            batch_id=request.batch_id, mode=mode, job_id=job_id,
            owner_token=owner, config_version="v1",
            expected_amount_usd=Decimal("0.10"),
        )
        for slot, cost in (
            ("claim_extraction", Decimal("0.02")),
            ("evidence_narrative", Decimal("0.03")),
        ):
            store.consume_call_slot(
                batch_id=request.batch_id, mode=mode, job_id=job_id,
                owner_token=owner, config_version="v1",
                expected_amount_usd=Decimal("0.10"), slot=slot,
            )
            store.record_call_cost(
                batch_id=request.batch_id, mode=mode, job_id=job_id,
                owner_token=owner, slot=slot,
                accounting_token=_accounting_token(mode, slot),
                ledger_receipt=f"ledger-{mode}-{slot}",
                actual_cost_usd=cost, tokens_in=50, tokens_out=10,
            )
        if index < terminal_count:
            store.record_job_terminal(
                batch_id=request.batch_id, mode=mode, job_id=job_id,
                owner_token=owner, state="completed",
            )
    return store, request, result


def test_sqlite_five_terminal_jobs_settle_once_and_release_unused(tmp_path):
    store, request, _result = _prepared_store(tmp_path)
    settled = store.settle_batch(batch_id=request.batch_id)
    assert settled.settled and not settled.replayed
    assert settled.actual_cost_usd == Decimal("0.25")
    assert settled.released_usd == Decimal("0.25")
    replay = store.settle_batch(batch_id=request.batch_id)
    assert replay.replayed
    assert replay.released_usd == Decimal("0.25")


def test_call_receipt_replay_is_idempotent_but_payload_change_conflicts(tmp_path):
    store, request, result = _prepared_store(tmp_path, terminal_count=0)
    job_id, mode = result.job_ids[0], ANGLE_MODES[0]
    kwargs = {
        "batch_id": request.batch_id, "mode": mode, "job_id": job_id,
        "owner_token": f"owner-{mode}", "slot": "claim_extraction",
        "accounting_token": _accounting_token(mode, "claim_extraction"),
        "ledger_receipt": f"ledger-{mode}-claim_extraction",
        "actual_cost_usd": Decimal("0.02"), "tokens_in": 50, "tokens_out": 10,
    }
    assert store.record_call_cost(**kwargs)
    with pytest.raises(BatchStoreIntegrityError):
        store.record_call_cost(**{**kwargs, "actual_cost_usd": Decimal("0.021")})


def test_uncertain_consumed_slot_is_never_released_by_reconciler(tmp_path):
    store, request, _result = _prepared_store(tmp_path, terminal_count=0)
    with sqlite3.connect(tmp_path / "settle.db") as conn:
        conn.execute(
            """DELETE FROM atomic_call_costs
               WHERE batch_id=? AND slot='claim_extraction'""",
            (request.batch_id,),
        )
        before = conn.execute(
            "SELECT remaining_usd,reserved_total FROM atomic_budget"
        ).fetchone()
    report = store.reconcile_stale_batches(stale_before=int(time.time()), apply=True)
    assert report["uncertain"] == [request.batch_id]
    assert report["settled"] == []
    with sqlite3.connect(tmp_path / "settle.db") as conn:
        after = conn.execute(
            "SELECT remaining_usd,reserved_total FROM atomic_budget"
        ).fetchone()
    assert after == before


def test_reconcile_apply_is_idempotent_and_dry_run_does_not_write(tmp_path):
    store, request, _result = _prepared_store(tmp_path)
    dry = store.reconcile_stale_batches(stale_before=int(time.time()), apply=False)
    assert dry["ready"] == [request.batch_id]
    with sqlite3.connect(tmp_path / "settle.db") as conn:
        assert conn.execute("SELECT count(*) FROM atomic_settlements").fetchone()[0] == 0
    applied = store.reconcile_stale_batches(stale_before=int(time.time()), apply=True)
    assert applied["settled"] == [request.batch_id]
    replay = store.reconcile_stale_batches(stale_before=int(time.time()), apply=True)
    assert replay["settled"] == []


def test_settlement_to_synthesis_crash_has_stale_lease_takeover(tmp_path):
    store, request, _result = _prepared_store(tmp_path)
    store.settle_batch(batch_id=request.batch_id)
    assert store.claim_synthesis(
        batch_id=request.batch_id, owner_token="synth-owner-one",
        stale_before=int(time.time()) - 60,
    )
    assert not store.claim_synthesis(
        batch_id=request.batch_id, owner_token="synth-owner-two",
        stale_before=int(time.time()) - 60,
    )
    with sqlite3.connect(tmp_path / "settle.db") as conn:
        conn.execute(
            "UPDATE atomic_synthesis_claims SET claimed_at=0 WHERE batch_id=?",
            (request.batch_id,),
        )
    assert store.claim_synthesis(
        batch_id=request.batch_id, owner_token="synth-owner-two",
        stale_before=int(time.time()) - 60,
    )
    assert store.complete_synthesis(
        batch_id=request.batch_id, owner_token="synth-owner-two"
    )


def test_local_result_commit_before_terminal_is_replayed_on_restart(tmp_path):
    store, request, result = _prepared_store(tmp_path, terminal_count=4)
    job_id, mode = result.job_ids[-1], ANGLE_MODES[-1]
    with AnalysisFlow(
        tmp_path / "settle.db", atomic_batch_store=store
    ) as flow:
        now = time.time()
        flow._conn().execute(
            """INSERT INTO analysis_jobs(
                 job_id,snapshot_id,coin,mode,question,question_type,state,
                 current_stage,retry_count,error,created_at,updated_at,
                 atomic_batch_id,atomic_mode
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id, request.snapshot_id, request.coin, mode, "question",
                "multi_source", "completed", "report_delivery", 0, None,
                now, now, request.batch_id, mode,
            ),
        )
        flow._conn().execute(
            "INSERT INTO analysis_results VALUES(?,?,?,?,?,?,?,?)",
            (
                f"result-{job_id}", job_id, request.snapshot_id, request.coin,
                mode, "question", '{"report":{}}', now,
            ),
        )
        flow._conn().execute(
            """INSERT INTO analysis_atomic_owners
               (job_id,batch_id,mode,owner_token,claimed_at)
               VALUES(?,?,?,?,?)""",
            (
                job_id, request.batch_id, mode, f"owner-{mode}", now,
            ),
        )
        assert flow._recover_atomic_terminals() == 1
    replay = store.settle_batch(batch_id=request.batch_id)
    assert replay.settled and replay.replayed


def test_atomic_unledgered_fallback_fails_closed_without_receipt(monkeypatch):
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda: True)
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.narrative_model_priced",
        lambda: True,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.daily_cap_usd", lambda: 1.0
    )
    monkeypatch.setattr("trustforge.analysis_flow.append_run", lambda _record: False)
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.record_unledgered_spend",
        lambda _cost: None,
    )
    receipts = []
    log = ExecutionLog()
    with (
        pytest.raises(MultiAngleAuthorityError, match="accounting"),
        _bedrock_live_attempt(
            log, batch_allocation=True, on_accounted=receipts.append
        ) as live,
    ):
        assert live
        log.record_llm_cost("model", 10, 5, 0.01)
    assert receipts == []


def test_recovery_keeps_owner_during_live_synthesis_lease_then_takes_over(
    tmp_path,
):
    store, request, result = _prepared_store(tmp_path)
    assert store.settle_batch(batch_id=request.batch_id).settled
    assert store.claim_synthesis(
        batch_id=request.batch_id, owner_token="other-live-owner",
        stale_before=int(time.time()) - 60,
    )
    job_id, mode = result.job_ids[-1], ANGLE_MODES[-1]
    with AnalysisFlow(
        tmp_path / "settle.db", atomic_batch_store=store
    ) as flow:
        now = time.time()
        flow._conn().execute(
            """INSERT INTO analysis_jobs(
                 job_id,snapshot_id,coin,mode,question,question_type,state,
                 current_stage,retry_count,error,created_at,updated_at,
                 atomic_batch_id,atomic_mode
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id, request.snapshot_id, request.coin, mode, "question",
                "multi_source", "completed", "report_delivery", 0, None,
                now, now, request.batch_id, mode,
            ),
        )
        flow._conn().execute(
            "INSERT INTO analysis_results VALUES(?,?,?,?,?,?,?,?)",
            (
                f"result-{job_id}", job_id, request.snapshot_id, request.coin,
                mode, "question", '{"report":{}}', now,
            ),
        )
        flow._conn().execute(
            """INSERT INTO analysis_atomic_owners
               (job_id,batch_id,mode,owner_token,claimed_at)
               VALUES(?,?,?,?,?)""",
            (job_id, request.batch_id, mode, f"owner-{mode}", now),
        )
        assert flow._recover_atomic_terminals() == 1
        assert flow._conn().execute(
            "SELECT count(*) FROM analysis_atomic_owners WHERE batch_id=?",
            (request.batch_id,),
        ).fetchone()[0] == 1
        with sqlite3.connect(tmp_path / "settle.db") as conn:
            conn.execute(
                """UPDATE atomic_synthesis_claims SET claimed_at=0
                   WHERE batch_id=?""",
                (request.batch_id,),
            )
        assert flow._recover_atomic_terminals() == 1
        assert flow._conn().execute(
            "SELECT count(*) FROM analysis_atomic_owners WHERE batch_id=?",
            (request.batch_id,),
        ).fetchone()[0] == 0
    with sqlite3.connect(tmp_path / "settle.db") as conn:
        assert conn.execute(
            "SELECT state FROM atomic_synthesis_claims WHERE batch_id=?",
            (request.batch_id,),
        ).fetchone()[0] == "completed"


def test_sqlite_store_context_connections_do_not_emit_resource_warning(tmp_path):
    # Do not attribute connections made collectible by earlier tests to the
    # store instance exercised inside this warning boundary.
    gc.collect()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        store = SQLiteAtomicMultiAngleBatchStore(str(tmp_path / "warnings.db"))
        store.bootstrap_budget(
            day="2026-07-29", remaining_usd=Decimal(1), config_version="v1"
        )
        del store
        gc.collect()
    assert not [item for item in caught if issubclass(item.category, ResourceWarning)]


class _PagedDynamoClient:
    def __init__(self):
        self.scans = []

    def scan(self, **kwargs):
        self.scans.append(kwargs)
        if len(self.scans) == 1:
            return {"Items": [], "LastEvaluatedKey": {"pk": {"S": "next"}}}
        return {"Items": []}


def test_dynamodb_reconciler_consumes_every_scan_page_without_writes():
    client = _PagedDynamoClient()
    report = DynamoDBAtomicMultiAngleBatchStore(
        client=client, table_name="sandbox"
    ).reconcile_stale_batches(stale_before=123, apply=False)
    assert report["dry_run"] is True
    assert len(client.scans) == 2
    assert client.scans[1]["ExclusiveStartKey"] == {"pk": {"S": "next"}}


def _terminal_last(store, request, result, state):
    mode, job_id = ANGLE_MODES[-1], result.job_ids[-1]
    store.record_job_terminal(
        batch_id=request.batch_id, mode=mode, job_id=job_id,
        owner_token=f"owner-{mode}", state=state,
    )


def test_failed_terminal_batch_settles_without_synthesis(tmp_path):
    store, request, result = _prepared_store(tmp_path, terminal_count=4)
    _terminal_last(store, request, result, "failed")
    settled = store.settle_batch(batch_id=request.batch_id)
    assert settled.settled
    assert settled.synthesis_claimed is False
    with sqlite3.connect(tmp_path / "settle.db") as conn:
        assert conn.execute(
            "SELECT count(*) FROM atomic_synthesis_claims"
        ).fetchone()[0] == 0


def test_timeout_terminal_batch_settles_without_synthesis(tmp_path):
    store, request, result = _prepared_store(tmp_path, terminal_count=4)
    _terminal_last(store, request, result, "timeout")
    settled = store.settle_batch(batch_id=request.batch_id)
    assert settled.settled
    assert settled.synthesis_claimed is False


def test_accounting_state_reports_two_receipted_slots(tmp_path):
    store, request, result = _prepared_store(tmp_path, terminal_count=0)
    assert set(store.call_accounting_state(
        batch_id=request.batch_id, mode=ANGLE_MODES[0],
        job_id=result.job_ids[0], owner_token=f"owner-{ANGLE_MODES[0]}",
    ).values()) == {"receipted"}


def test_accounting_state_reports_consumed_without_receipt_uncertain(tmp_path):
    store, request, result = _prepared_store(tmp_path, terminal_count=0)
    with sqlite3.connect(tmp_path / "settle.db") as conn:
        conn.execute(
            "DELETE FROM atomic_call_costs WHERE job_id=? AND slot=?",
            (result.job_ids[0], "claim_extraction"),
        )
    states = store.call_accounting_state(
        batch_id=request.batch_id, mode=ANGLE_MODES[0],
        job_id=result.job_ids[0], owner_token=f"owner-{ANGLE_MODES[0]}",
    )
    assert states["claim_extraction"] == "uncertain"


def test_accounting_state_reports_never_consumed_slot_available(tmp_path):
    store, request, result = _prepared_store(tmp_path, terminal_count=0)
    with sqlite3.connect(tmp_path / "settle.db") as conn:
        conn.execute(
            """UPDATE atomic_allocations SET claim_extraction_slot='available'
               WHERE batch_id=? AND mode=?""",
            (request.batch_id, ANGLE_MODES[0]),
        )
        conn.execute(
            "DELETE FROM atomic_call_costs WHERE job_id=? AND slot=?",
            (result.job_ids[0], "claim_extraction"),
        )
    states = store.call_accounting_state(
        batch_id=request.batch_id, mode=ANGLE_MODES[0],
        job_id=result.job_ids[0], owner_token=f"owner-{ANGLE_MODES[0]}",
    )
    assert states["claim_extraction"] == "available"


def test_failed_terminal_replay_is_idempotent(tmp_path):
    store, request, result = _prepared_store(tmp_path, terminal_count=4)
    _terminal_last(store, request, result, "failed")
    _terminal_last(store, request, result, "failed")
    first = store.settle_batch(batch_id=request.batch_id)
    replay = store.settle_batch(batch_id=request.batch_id)
    assert first.settled and replay.replayed
    assert not replay.synthesis_claimed


def test_atomic_terminalized_dead_letter_cannot_be_requeued(tmp_path):
    with AnalysisFlow(tmp_path / "flow.db") as flow:
        now = time.time()
        job_id = "atomic-dead-letter"
        flow._conn().execute(
            """INSERT INTO analysis_jobs(
                 job_id,snapshot_id,coin,mode,question,question_type,state,
                 current_stage,retry_count,error,created_at,updated_at,
                 atomic_batch_id,atomic_mode
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id, "snapshot", "BTC", "risk", "q", "multi_source",
                "failed", "claim_extraction", 3, "failed", now, now,
                "batch-dead-letter", "risk",
            ),
        )
        flow._conn().execute(
            "INSERT INTO analysis_dead_letters VALUES(?,?,?,?,?,?,?,?,?)",
            (
                job_id, "claim_extraction", "BTC", "risk", "q",
                "snapshot", 3, "failed", now,
            ),
        )
        flow._conn().execute(
            "INSERT INTO analysis_atomic_terminal_failures VALUES(?,?,?)",
            (job_id, "failed", now),
        )
        assert flow.requeue_dead_letter(job_id) is False


def test_legacy_dead_letter_remains_requeueable(tmp_path):
    with AnalysisFlow(tmp_path / "legacy-flow.db") as flow:
        now = time.time()
        job_id = "legacy-dead-letter"
        flow._conn().execute(
            """INSERT INTO analysis_jobs(
                 job_id,snapshot_id,coin,mode,question,question_type,state,
                 current_stage,retry_count,error,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id, "snapshot", "BTC", "risk", "q", "multi_source",
                "failed", "claim_extraction", 3, "failed", now, now,
            ),
        )
        flow._conn().execute(
            "INSERT INTO analysis_dead_letters VALUES(?,?,?,?,?,?,?,?,?)",
            (
                job_id, "claim_extraction", "BTC", "risk", "q",
                "snapshot", 3, "failed", now,
            ),
        )
        assert flow.requeue_dead_letter(job_id) is True
        job = flow._conn().execute(
            "SELECT state,retry_count FROM analysis_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        assert tuple(job) == ("queued", 0)
        assert flow._conn().execute(
            "SELECT count(*) FROM analysis_dead_letters WHERE job_id=?",
            (job_id,),
        ).fetchone()[0] == 0


def test_recovery_replays_durable_failed_dead_letter_without_synthesis(tmp_path):
    store, request, result = _prepared_store(tmp_path, terminal_count=4)
    job_id, mode = result.job_ids[-1], ANGLE_MODES[-1]
    with AnalysisFlow(tmp_path / "settle.db", atomic_batch_store=store) as flow:
        now = time.time()
        flow._conn().execute(
            """INSERT INTO analysis_jobs(
                 job_id,snapshot_id,coin,mode,question,question_type,state,
                 current_stage,retry_count,error,created_at,updated_at,
                 atomic_batch_id,atomic_mode
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id, request.snapshot_id, request.coin, mode, "q",
                "multi_source", "failed", "evidence_assembly", 3, "failed",
                now, now, request.batch_id, mode,
            ),
        )
        flow._conn().execute(
            "INSERT INTO analysis_dead_letters VALUES(?,?,?,?,?,?,?,?,?)",
            (
                job_id, "evidence_assembly", request.coin, mode, "q",
                request.snapshot_id, 3, "failed", now,
            ),
        )
        flow._conn().execute(
            """INSERT INTO analysis_atomic_owners VALUES(?,?,?,?,?)""",
            (job_id, request.batch_id, mode, f"owner-{mode}", now),
        )
        assert flow._recover_atomic_terminals() == 1
    settled = store.settle_batch(batch_id=request.batch_id)
    assert settled.replayed and not settled.synthesis_claimed


def test_cancel_call_slot_is_atomic_and_idempotent(tmp_path):
    store, request, result = _prepared_store(tmp_path, terminal_count=0)
    mode, job_id = ANGLE_MODES[0], result.job_ids[0]
    with sqlite3.connect(tmp_path / "settle.db") as conn:
        conn.execute(
            """UPDATE atomic_allocations SET claim_extraction_slot='available'
               WHERE batch_id=? AND mode=?""",
            (request.batch_id, mode),
        )
        conn.execute(
            "DELETE FROM atomic_call_costs WHERE job_id=? AND slot=?",
            (job_id, "claim_extraction"),
        )
    kwargs = {
        "batch_id": request.batch_id, "mode": mode, "job_id": job_id,
        "owner_token": f"owner-{mode}", "slot": "claim_extraction",
    }
    assert store.cancel_call_slot(**kwargs)
    assert store.cancel_call_slot(**kwargs)
    assert store.call_accounting_state(**{k: v for k, v in kwargs.items() if k != "slot"})[
        "claim_extraction"
    ] == "receipted"


def test_cancel_call_slot_owner_conflict_is_fail_closed(tmp_path):
    store, request, result = _prepared_store(tmp_path, terminal_count=0)
    with pytest.raises(BatchStoreIntegrityError):
        store.cancel_call_slot(
            batch_id=request.batch_id, mode=ANGLE_MODES[0],
            job_id=result.job_ids[0], owner_token="wrong-owner",
            slot="claim_extraction",
        )


def test_dynamodb_cancel_call_slot_is_one_conditional_authority_write():
    class Client:
        def __init__(self):
            self.calls = []

        def update_item(self, **kwargs):
            self.calls.append(kwargs)

    client = Client()
    request = AtomicBatchRequest(
        batch_id="ddb-cancel", caller_hash="a" * 64,
        idempotency_key_hash="b" * 64, request_fingerprint="c" * 64,
        coin="BTC", snapshot_id="snap-ddb-cancel", day="2026-07-29",
        batch_cost_usd=Decimal("0.50"), config_version="v1", created_at=1,
    )
    job_id = _job_ids(request.batch_id)[0]
    store = DynamoDBAtomicMultiAngleBatchStore(
        client=client, table_name="sandbox"
    )
    assert store.cancel_call_slot(
        batch_id=request.batch_id, mode=ANGLE_MODES[0], job_id=job_id,
        owner_token="owner-risk", slot="claim_extraction",
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert "claim_extraction_ledger_receipt=:receipt" in call["UpdateExpression"]
    assert "#slot=:available" in call["ConditionExpression"]
    assert "#slot=:consumed" in call["ConditionExpression"]


@pytest.mark.parametrize("backend", ["sqlite", "dynamodb"])
def test_cancel_call_slot_rejects_malformed_identity_parity(tmp_path, backend):
    if backend == "sqlite":
        store = SQLiteAtomicMultiAngleBatchStore(str(tmp_path / "bad.db"))
    else:
        store = DynamoDBAtomicMultiAngleBatchStore(
            client=object(), table_name="sandbox"
        )
    with pytest.raises(ValueError):
        store.cancel_call_slot(
            batch_id="../bad", mode="risk", job_id="wrong",
            owner_token="bad owner", slot="claim_extraction",
        )


def test_dynamodb_record_call_cost_transacts_global_token_binding():
    class Client:
        def __init__(self):
            self.calls = []

        def transact_write_items(self, **kwargs):
            self.calls.append(kwargs)

    client = Client()
    batch_id, mode = "ddb-accounting", ANGLE_MODES[0]
    job_id = _job_ids(batch_id)[0]
    token = hashlib.sha256(b"ledger-payload").hexdigest()
    store = DynamoDBAtomicMultiAngleBatchStore(
        client=client, table_name="sandbox"
    )
    assert store.record_call_cost(
        batch_id=batch_id, mode=mode, job_id=job_id,
        owner_token="owner-risk", slot="claim_extraction",
        accounting_token=token, ledger_receipt="ledger-receipt",
        actual_cost_usd=Decimal("0.01"), tokens_in=10, tokens_out=2,
    )
    tx = client.calls[0]["TransactItems"]
    assert len(tx) == 2
    assert tx[1]["Put"]["Item"]["pk"] == {"S": f"ACCOUNTING#{token}"}
    assert tx[1]["Put"]["ConditionExpression"] == "attribute_not_exists(pk)"


def test_dynamodb_reconcile_treats_timeout_as_terminal_ready():
    class Client:
        def scan(self, **_kwargs):
            return {"Items": [{
                "pk": {"S": "BATCH#timeout-batch"}, "sk": {"S": "META"},
                "state": {"S": "reserved"}, "created_at": {"N": "1"},
            }]}

        def get_item(self, **kwargs):
            mode = kwargs["Key"]["sk"]["S"].removeprefix("ALLOCATION#")
            return {"Item": {
                **kwargs["Key"], "state": {"S": "timeout"},
                "job_id": {"S": _job_ids("timeout-batch")[
                    ANGLE_MODES.index(mode)
                ]},
            }}

    report = DynamoDBAtomicMultiAngleBatchStore(
        client=Client(), table_name="sandbox"
    ).reconcile_stale_batches(stale_before=2, apply=False)
    assert report["ready"] == ["timeout-batch"]


def test_dynamodb_accounting_exact_replay_passes_but_mutation_conflicts():
    batch_id, mode, slot = "ddb-replay", ANGLE_MODES[0], "claim_extraction"
    job_id = _job_ids(batch_id)[0]
    token = hashlib.sha256(b"immutable-ledger-payload").hexdigest()

    class Cancelled(Exception):
        def __init__(self):
            self.response = {
                "Error": {"Code": "TransactionCanceledException"}
            }

    class Client:
        def transact_write_items(self, **_kwargs):
            raise Cancelled()

        def get_item(self, **kwargs):
            if kwargs["Key"]["pk"]["S"].startswith("ACCOUNTING#"):
                return {"Item": {
                    **kwargs["Key"], "batch_id": {"S": batch_id},
                    "mode": {"S": mode}, "job_id": {"S": job_id},
                    "slot": {"S": slot}, "owner_token": {"S": "owner-risk"},
                    "ledger_receipt": {"S": "receipt"}, "actual_cost_usd": {"N": "0.01"},
                    "tokens_in": {"N": "10"}, "tokens_out": {"N": "2"},
                }}
            return {"Item": {
                **kwargs["Key"], f"{slot}_accounting_token": {"S": token},
                f"{slot}_ledger_receipt": {"S": "receipt"},
                f"{slot}_actual_cost_usd": {"N": "0.01"},
                f"{slot}_tokens_in": {"N": "10"},
                f"{slot}_tokens_out": {"N": "2"},
            }}

    store = DynamoDBAtomicMultiAngleBatchStore(
        client=Client(), table_name="sandbox"
    )
    kwargs = {
        "batch_id": batch_id, "mode": mode, "job_id": job_id,
        "owner_token": "owner-risk", "slot": slot,
        "accounting_token": token, "ledger_receipt": "receipt",
        "actual_cost_usd": Decimal("0.01"), "tokens_in": 10, "tokens_out": 2,
    }
    assert store.record_call_cost(**kwargs)
    with pytest.raises(BatchStoreIntegrityError):
        store.record_call_cost(**{**kwargs, "actual_cost_usd": Decimal("0.02")})


def test_terminal_marker_restart_retries_settlement_without_synthesis(tmp_path):
    store, request, result = _prepared_store(tmp_path, terminal_count=4)
    _terminal_last(store, request, result, "failed")
    job_id, mode = result.job_ids[-1], ANGLE_MODES[-1]
    with AnalysisFlow(tmp_path / "settle.db", atomic_batch_store=store) as flow:
        now = time.time()
        flow._conn().execute(
            """INSERT INTO analysis_atomic_owners VALUES(?,?,?,?,?)""",
            (job_id, request.batch_id, mode, f"owner-{mode}", now),
        )
        flow._conn().execute(
            "INSERT INTO analysis_atomic_terminal_failures VALUES(?,?,?)",
            (job_id, "failed", now),
        )
        flow._recover_atomic_terminals()
    settled = store.settle_batch(batch_id=request.batch_id)
    assert settled.replayed and not settled.synthesis_claimed
