from __future__ import annotations

import json
from contextlib import contextmanager
from decimal import Decimal

import pytest

from trustforge import budget_guard
from trustforge.analysis_flow import (
    AnalysisFlow,
    MultiAngleAuthorityError,
    MultiAngleBudgetError,
    _bedrock_live_attempt,
)
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.multi_angle_batch_store import (
    AtomicBatchResult,
    BatchStoreBackendError,
    DynamoDBAtomicMultiAngleBatchStore,
    _job_ids,
)


class _DeniedStore:
    def find_replay(self, request):
        return None

    def create_batch(self, request):
        return AtomicBatchResult(False, False, request.batch_id, ())


class _BrokenStore(_DeniedStore):
    def create_batch(self, request):
        raise BatchStoreBackendError("ddb unavailable")


class _RecordingStore(_DeniedStore):
    def __init__(self):
        self.request = None

    def create_batch(self, request):
        self.request = request
        return AtomicBatchResult(False, False, request.batch_id, ())


class _ExternalAuthority:
    """Dynamo-like durable state intentionally independent of flow SQLite."""

    def __init__(self):
        self.request = None
        self.result = None

    def find_replay(self, request):
        if self.request is None:
            return None
        if (
            request.caller_hash == self.request.caller_hash
            and request.idempotency_key_hash == self.request.idempotency_key_hash
            and request.request_fingerprint == self.request.request_fingerprint
        ):
            return self.result
        return None

    def create_batch(self, request):
        self.request = request
        self.result = AtomicBatchResult(
            True, False, request.batch_id, _job_ids(request.batch_id),
            request.snapshot_id,
        )
        return self.result


def test_production_authority_never_falls_back_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_ENV", "production")
    monkeypatch.delenv("TRUSTFORGE_ATOMIC_BATCH_TABLE", raising=False)
    monkeypatch.delenv("TRUSTFORGE_ATOMIC_BATCH_CONFIG_VERSION", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    with (
        AnalysisFlow(tmp_path / "flow.db") as flow,
        pytest.raises(
            MultiAngleAuthorityError,
            match="production atomic batch authority/exclusive shared",
        ),
    ):
        flow._atomic_store()


def test_production_authority_factory_builds_only_dynamodb(
    tmp_path, monkeypatch
):
    client = object()
    monkeypatch.setenv("TRUSTFORGE_ENV", "production")
    monkeypatch.setenv("TRUSTFORGE_ATOMIC_BATCH_TABLE", "atomic-authority")
    monkeypatch.setenv("TRUSTFORGE_ATOMIC_BATCH_CONFIG_VERSION", "v1")
    monkeypatch.setenv("TRUSTFORGE_ATOMIC_BATCH_EXCLUSIVE", "1")
    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: client)
    db_path = tmp_path / "flow.db"
    monkeypatch.setenv("TRUSTFORGE_SHARED_ANALYSIS_DB_PATH", str(db_path))
    with AnalysisFlow(db_path) as flow:
        store = flow._atomic_store()
        assert isinstance(store, DynamoDBAtomicMultiAngleBatchStore)


@pytest.mark.parametrize(
    ("store", "error"),
    [(_DeniedStore(), MultiAngleBudgetError), (_BrokenStore(), MultiAngleAuthorityError)],
)
def test_admission_failure_leaves_zero_orphan_snapshots(tmp_path, store, error):
    with AnalysisFlow(tmp_path / "flow.db", atomic_batch_store=store) as flow:
        before = flow._conn().execute(
            "SELECT count(*) FROM analysis_snapshots"
        ).fetchone()[0]
        with pytest.raises(error):
            flow.submit_multi_angle(
                "BTC", "atomic failure", caller_id="caller", idempotency_key="key"
            )
        assert flow._conn().execute(
            "SELECT count(*) FROM analysis_snapshots"
        ).fetchone()[0] == before
        assert flow._conn().execute(
            "SELECT count(*) FROM analysis_jobs"
        ).fetchone()[0] == 0


def test_batch_reservation_is_exactly_five_request_upper_bounds(
    tmp_path, monkeypatch
):
    store = _RecordingStore()
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.multi_angle_angle_max_cost_usd",
        lambda: 0.1234567,
    )
    with (
        AnalysisFlow(tmp_path / "flow.db", atomic_batch_store=store) as flow,
        pytest.raises(MultiAngleBudgetError),
    ):
        flow.submit_multi_angle(
            "BTC", "five allocations", caller_id="caller",
            idempotency_key="five-cost",
        )
    assert store.request.batch_cost_usd == Decimal("0.617284")


@pytest.mark.parametrize("configured", ["0", "-1", "not-an-int", "8193"])
def test_invalid_bedrock_max_tokens_fails_submission_closed(
    tmp_path, monkeypatch, configured
):
    monkeypatch.setenv("BEDROCK_MAX_TOKENS", configured)
    with (
        AnalysisFlow(tmp_path / f"{configured}.db", atomic_batch_store=_DeniedStore()) as flow,
        pytest.raises(MultiAngleAuthorityError),
    ):
        flow.submit_multi_angle(
            "BTC", "invalid token config", caller_id="caller",
            idempotency_key=f"tokens-{configured}",
        )


@pytest.mark.parametrize("configured", ["0", "-1", "not-an-int", "7999", "1000001"])
def test_invalid_narrative_input_bound_fails_submission_closed(
    tmp_path, monkeypatch, configured
):
    monkeypatch.setenv("TRUSTFORGE_WC_NARRATIVE_INPUT_TOKENS", configured)
    with (
        AnalysisFlow(tmp_path / f"narrative-{configured}.db",
                     atomic_batch_store=_DeniedStore()) as flow,
        pytest.raises(MultiAngleAuthorityError),
    ):
        flow.submit_multi_angle(
            "BTC", "invalid narrative bound", caller_id="caller",
            idempotency_key=f"narrative-{configured}",
        )


def test_multi_angle_upper_bound_respects_operator_request_max(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.75")
    assert budget_guard.multi_angle_angle_max_cost_usd() >= 0.75


def test_authority_failure_emits_sanitized_telemetry(tmp_path, caplog):
    with (
        AnalysisFlow(tmp_path / "flow.db", atomic_batch_store=_BrokenStore()) as flow,
        caplog.at_level("ERROR"),
        pytest.raises(MultiAngleAuthorityError),
    ):
        flow.submit_multi_angle(
            "BTC", "telemetry", caller_id="caller", idempotency_key="telemetry"
        )
    assert "multi_angle_authority_unavailable" in caplog.text
    assert "phase=create_batch" in caplog.text
    assert "ddb unavailable" not in caplog.text


def test_projection_failure_is_all_or_zero_and_daemon_reconcile_repairs(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "flow.db"
    authority = _ExternalAuthority()
    with AnalysisFlow(db_path, atomic_batch_store=authority) as flow:
        original = flow._checkpoint
        calls = 0

        def fail_third(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("projection fault")
            return original(*args, **kwargs)

        monkeypatch.setattr(flow, "_checkpoint", fail_third)
        with pytest.raises(RuntimeError, match="projection fault"):
            flow.submit_multi_angle(
                "BTC", "repair projection", caller_id="caller",
                idempotency_key="repair-key",
            )
        assert flow._conn().execute(
            "SELECT count(*) FROM analysis_jobs"
        ).fetchone()[0] == 0
        assert len(authority.result.job_ids) == 5
        snapshot_count = flow._conn().execute(
            "SELECT count(*) FROM analysis_snapshots"
        ).fetchone()[0]
        assert flow._conn().execute(
            "SELECT state FROM analysis_atomic_projection_queue"
        ).fetchone()[0] == "admitted"
        # Simulate Dynamo success followed by a crash before the local
        # admitted-state update became durable.
        flow._conn().execute(
            """UPDATE analysis_atomic_projection_queue
               SET state='pending_authority',result_json=NULL"""
        )

    # No client retry: a new daemon process discovers the durable projection
    # queue and materializes all five authority jobs.
    with AnalysisFlow(db_path, atomic_batch_store=authority) as restarted:
        monkeypatch.setattr(restarted, "_spawn_worker", lambda *_args: None)
        snapshot_payload = json.loads(
            restarted._conn().execute(
                "SELECT snapshot_json FROM analysis_atomic_projection_queue"
            ).fetchone()[0]
        )
        restarted._conn().execute(
            """UPDATE analysis_snapshots SET source_revision='tampered'
               WHERE snapshot_id=?""",
            (snapshot_payload["snapshot_id"],),
        )
        blocked = restarted.reconcile_runtime()
        assert blocked["atomic_projections"] == 0
        assert restarted._conn().execute(
            "SELECT count(*) FROM analysis_atomic_projection_queue"
        ).fetchone()[0] == 1
        restarted._conn().execute(
            """UPDATE analysis_snapshots
               SET coin=?,source_revision=?,docs_json=?,document_count=?
               WHERE snapshot_id=?""",
            (
                snapshot_payload["coin"], snapshot_payload["source_revision"],
                snapshot_payload["docs_json"], snapshot_payload["document_count"],
                snapshot_payload["snapshot_id"],
            ),
        )
        repaired = restarted.reconcile_runtime()
        assert repaired["atomic_projections"] == 1
        assert restarted._conn().execute(
            "SELECT count(*) FROM analysis_jobs"
        ).fetchone()[0] == 5
        assert restarted._conn().execute(
            "SELECT count(*) FROM analysis_snapshots"
        ).fetchone()[0] == snapshot_count
        assert restarted._conn().execute(
            "SELECT count(*) FROM analysis_atomic_projection_queue"
        ).fetchone()[0] == 0


def test_batch_allocation_skips_legacy_reserve_release_but_ledgers(
    monkeypatch,
):
    reserve_calls = []
    release_calls = []
    ledger = []
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda: True)
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.narrative_model_priced",
        lambda: True,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.try_reserve_request_budget",
        lambda: reserve_calls.append(True),
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.release_request_budget",
        lambda value: release_calls.append(value),
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.append_run",
        lambda payload: ledger.append(payload) or True,
    )
    log = ExecutionLog(run_id="authority-job")
    with _bedrock_live_attempt(log, batch_allocation=True) as live:
        assert live
        log.record(
            "llm.cost",
            params={
                "model": "test-model", "tokens_in": 10, "tokens_out": 5,
                "cost_usd": 0.01,
            },
            summary="test",
        )
    assert reserve_calls == []
    assert release_calls == []
    assert ledger[0]["total_cost_usd"] == 0.01


def _allow_legacy_live_attempt(monkeypatch, reservation=0.25):
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda: True)
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.narrative_model_priced",
        lambda: True,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.daily_cap_exceeded",
        lambda: False,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.try_reserve_request_budget",
        lambda **_kwargs: reservation,
    )


def test_live_timeout_without_usage_charges_reservation_before_release(
    monkeypatch,
):
    order = []
    _allow_legacy_live_attempt(monkeypatch, reservation=0.25)
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.record_unledgered_spend",
        lambda amount: order.append(("unledgered", amount)),
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.release_request_budget",
        lambda amount, **_kwargs: order.append(("release", amount)),
    )
    log = ExecutionLog(run_id="narration-timeout")

    with pytest.raises(TimeoutError):
        with _bedrock_live_attempt(log) as live:
            assert live is True
            raise TimeoutError("provider accepted request but response timed out")

    assert order == [("unledgered", 0.25), ("release", 0.25)]
    assert any(
        event["tool"] == "llm.accounting_uncertain" for event in log.events
    )


def test_live_timeout_retains_reservation_when_unledgered_fallback_fails(
    monkeypatch,
):
    released = []
    _allow_legacy_live_attempt(monkeypatch, reservation=0.25)
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.record_unledgered_spend",
        lambda _amount: (_ for _ in ()).throw(RuntimeError("counter unavailable")),
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.release_request_budget",
        lambda amount, **_kwargs: released.append(amount),
    )

    with pytest.raises(TimeoutError):
        with _bedrock_live_attempt(ExecutionLog(run_id="timeout-no-fallback")) as live:
            assert live is True
            raise TimeoutError("provider timeout")

    assert released == []


def test_shared_unknown_usage_retains_authority_across_instance_and_restart(
    monkeypatch,
):
    state = {"reserved": 0.0, "release_calls": 0}

    class SharedAuthority:
        def __init__(self, durable_state):
            self.state = durable_state

        def reserve(self, **_kwargs):
            if self.state["reserved"] + 0.25 > 0.25:
                return None
            self.state["reserved"] += 0.25
            return 0.25

        def release(self, amount, **_kwargs):
            self.state["release_calls"] += 1
            self.state["reserved"] -= amount

    instance_a = SharedAuthority(state)
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda: True)
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.narrative_model_priced",
        lambda: True,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.daily_cap_exceeded",
        lambda: False,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.budget_reservation_backend",
        lambda: "dynamodb",
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.try_reserve_request_budget",
        instance_a.reserve,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.release_request_budget",
        instance_a.release,
    )
    # This succeeds only process-locally and therefore is not a durable shared
    # receipt authorizing release of the authority reservation.
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.record_unledgered_spend",
        lambda _amount: None,
    )

    with pytest.raises(TimeoutError):
        with _bedrock_live_attempt(ExecutionLog(run_id="shared-timeout")) as live:
            assert live is True
            raise TimeoutError("provider timeout")

    assert state == {"reserved": 0.25, "release_calls": 0}
    assert SharedAuthority(state).reserve() is None  # instance B
    assert SharedAuthority(state).reserve() is None  # restarted instance


def test_live_usage_ledger_failure_charges_actual_before_release(monkeypatch):
    order = []
    _allow_legacy_live_attempt(monkeypatch, reservation=0.25)
    monkeypatch.setattr(
        "trustforge.analysis_flow.append_run",
        lambda _record: (_ for _ in ()).throw(RuntimeError("ledger down")),
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.record_unledgered_spend",
        lambda amount: order.append(("unledgered", amount)),
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.release_request_budget",
        lambda amount, **_kwargs: order.append(("release", amount)),
    )
    log = ExecutionLog(run_id="narration-ledger-failure")

    with _bedrock_live_attempt(log) as live:
        assert live is True
        log.record(
            "llm.cost",
            params={
                "model": "test-model",
                "tokens_in": 10,
                "tokens_out": 5,
                "cost_usd": 0.04,
            },
            summary="usage",
        )

    assert order == [("unledgered", 0.04), ("release", 0.25)]


def test_shared_usage_ledger_failure_retains_reservation_despite_local_fallback(
    monkeypatch,
):
    released = []
    _allow_legacy_live_attempt(monkeypatch, reservation=0.25)
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.budget_reservation_backend",
        lambda: "dynamodb",
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.append_run",
        lambda _record: False,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.record_unledgered_spend",
        lambda _amount: None,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.release_request_budget",
        lambda amount, **_kwargs: released.append(amount),
    )
    log = ExecutionLog(run_id="shared-ledger-failure")

    with pytest.raises(MultiAngleAuthorityError, match="retained"):
        with _bedrock_live_attempt(log) as live:
            assert live is True
            log.record(
                "llm.cost",
                params={
                    "model": "test-model",
                    "tokens_in": 10,
                    "tokens_out": 5,
                    "cost_usd": 0.04,
                },
                summary="usage",
            )

    assert released == []


def test_live_usage_retains_reservation_when_ledger_and_fallback_both_fail(
    monkeypatch,
):
    released = []
    _allow_legacy_live_attempt(monkeypatch, reservation=0.25)
    monkeypatch.setattr(
        "trustforge.analysis_flow.append_run",
        lambda _record: (_ for _ in ()).throw(RuntimeError("ledger down")),
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.record_unledgered_spend",
        lambda _amount: (_ for _ in ()).throw(RuntimeError("counter down")),
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.release_request_budget",
        lambda amount, **_kwargs: released.append(amount),
    )
    log = ExecutionLog(run_id="usage-no-accounting")

    with pytest.raises(MultiAngleAuthorityError, match="reservation retained"):
        with _bedrock_live_attempt(log) as live:
            assert live is True
            log.record(
                "llm.cost",
                params={
                    "model": "test-model",
                    "tokens_in": 10,
                    "tokens_out": 5,
                    "cost_usd": 0.04,
                },
                summary="usage",
            )

    assert released == []


def test_live_success_with_usage_ledgers_actual_without_worst_case(monkeypatch):
    order = []
    ledger = []
    _allow_legacy_live_attempt(monkeypatch, reservation=0.25)

    def append(record):
        ledger.append(record)
        order.append(("ledger", record["total_cost_usd"]))
        return True

    monkeypatch.setattr("trustforge.analysis_flow.append_run", append)
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.record_unledgered_spend",
        lambda amount: order.append(("unledgered", amount)),
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.release_request_budget",
        lambda amount, **_kwargs: order.append(("release", amount)),
    )
    log = ExecutionLog(run_id="narration-success")

    with _bedrock_live_attempt(log) as live:
        assert live is True
        log.record(
            "llm.cost",
            params={
                "model": "test-model",
                "tokens_in": 10,
                "tokens_out": 5,
                "cost_usd": 0.04,
            },
            summary="usage",
        )

    assert order == [("ledger", 0.04), ("release", 0.25)]
    assert ledger[0]["total_cost_usd"] == 0.04


def test_narration_attempt_binds_reserve_and_release_to_captured_provenance(
    monkeypatch,
):
    backend_reads = []
    reserve_backends = []
    release_backends = []
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda: True)
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.narrative_model_priced",
        lambda: True,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.daily_cap_exceeded",
        lambda: False,
    )

    def resolve_backend():
        backend_reads.append(True)
        return "local" if len(backend_reads) == 1 else "dynamodb"

    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.budget_reservation_backend",
        resolve_backend,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.try_reserve_request_budget",
        lambda **kwargs: reserve_backends.append(kwargs["backend"]) or 0.25,
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.release_request_budget",
        lambda _amount, **kwargs: release_backends.append(kwargs["backend"]),
    )
    monkeypatch.setattr("trustforge.analysis_flow.append_run", lambda _record: True)
    log = ExecutionLog(run_id="bound-provenance")

    with _bedrock_live_attempt(log) as live:
        assert live is True
        log.record(
            "llm.cost",
            params={
                "model": "test-model",
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": 0.01,
            },
            summary="usage",
        )

    assert len(backend_reads) == 1
    assert reserve_backends == ["local"]
    assert release_backends == ["local"]


def test_exclusive_mode_disables_every_legacy_admission(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_ATOMIC_BATCH_EXCLUSIVE", "1")
    from trustforge import budget_guard

    assert budget_guard.try_reserve_request_budget() is None
    assert budget_guard.request_budget_available(1) is False


def test_batch_allocation_zero_cap_consumes_slot_and_cannot_later_go_live(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda: True)
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.narrative_model_priced",
        lambda: True,
    )
    cap = {"value": 0}
    monkeypatch.setattr(
        "trustforge.analysis_flow.budget_guard.daily_cap_usd",
        lambda: cap["value"],
    )
    with AnalysisFlow(tmp_path / "flow.db") as flow:
        result = flow.submit_multi_angle(
            "BTC", "cap sequence", caller_id="caller", idempotency_key="cap-key"
        )
        package = flow._stage_source_ingestion(
            {"job_id": result["job_ids"]["risk"]}
        )
        flow._stage_claim_extraction(package)
        assert package["client"].offline is True
        cap["value"] = 3
        with pytest.raises(MultiAngleAuthorityError):
            flow._stage_claim_extraction(package)


def test_restart_reuses_deterministic_owner_but_cannot_reconsume_slot(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda: False)
    with AnalysisFlow(tmp_path / "flow.db") as flow:
        result = flow.submit_multi_angle(
            "BTC", "owner binding", caller_id="caller", idempotency_key="owner-key"
        )
        job_id = result["job_ids"]["risk"]
        first = flow._stage_source_ingestion({"job_id": job_id})
        flow._stage_claim_extraction(first)
        assert first["allocation_owner_token"].startswith("allocation-")
        restarted = flow._stage_source_ingestion({"job_id": job_id})
        with pytest.raises(MultiAngleAuthorityError):
            flow._stage_claim_extraction(restarted)
        assert restarted["allocation_owner_token"] == first["allocation_owner_token"]


def test_same_package_step1_retry_cannot_consume_twice(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda: False)
    with AnalysisFlow(tmp_path / "flow.db") as flow:
        result = flow.submit_multi_angle(
            "BTC", "step1 once", caller_id="caller", idempotency_key="step1-key"
        )
        package = flow._stage_source_ingestion(
            {"job_id": result["job_ids"]["risk"]}
        )
        flow._stage_claim_extraction(package)
        with pytest.raises(MultiAngleAuthorityError):
            flow._stage_claim_extraction(package)


def test_same_package_step2_retry_cannot_consume_twice(tmp_path, monkeypatch):
    @contextmanager
    def live_gate(_log, *, batch_allocation=False):
        assert batch_allocation
        yield True

    monkeypatch.setattr("trustforge.analysis_flow._bedrock_live_attempt", live_gate)
    monkeypatch.setattr(
        "trustforge.analysis_flow.build_report", lambda *_args, **_kwargs: ("report", [])
    )
    with AnalysisFlow(tmp_path / "flow.db") as flow:
        result = flow.submit_multi_angle(
            "BTC", "step2 once", caller_id="caller", idempotency_key="step2-key"
        )
        package = flow._stage_source_ingestion(
            {"job_id": result["job_ids"]["risk"]}
        )
        flow._stage_claim_extraction(package)
        package.update(
            brief=None, stance=None, scored=[], kernel_judgment=None,
        )
        flow._stage_evidence_assembly(package)
        with pytest.raises(MultiAngleAuthorityError):
            flow._stage_evidence_assembly(package)


def test_atomic_claim_extraction_caps_prompt_documents(tmp_path, monkeypatch):
    monkeypatch.setattr("trustforge.web._bedrock_allowed", lambda: False)
    observed = []

    class FakeClient:
        offline = True

        class Config:
            model_id = None

        config = Config()

        def __init__(self, *, offline):
            self.offline = offline

        def extract_claims_with_llm(
            self, docs, log=None, *, mode=None, question=None,
        ):
            observed.append(list(docs))
            return []

    monkeypatch.setattr("trustforge.analysis_flow.BedrockClient", FakeClient)
    with AnalysisFlow(tmp_path / "flow.db") as flow:
        result = flow.submit_multi_angle(
            "BTC", "doc bound", caller_id="caller", idempotency_key="doc-key"
        )
        job = flow._job(result["job_ids"]["risk"])
        package = {
            "job": job,
            "job_id": job["job_id"],
            "docs": [
                Document(
                    id="識" * 500,
                    source="🚀" * 500,
                    kind="類" * 500,
                    text=("文🚀" * 2000),
                    meta={"untrusted": "🔥" * 100_000},
                )
                for _ in range(51)
            ],
            "log": ExecutionLog(run_id=job["job_id"]),
        }
        flow._stage_claim_extraction(package)
    assert 0 < len(observed[0]) <= 50
    assert all(len(doc.id) <= 128 for doc in observed[0])
    assert all(len(doc.source) <= 128 for doc in observed[0])
    assert all(len(doc.kind) <= 32 for doc in observed[0])
    assert all(len(doc.text) <= 300 for doc in observed[0])
    assert all(
        len(field.encode()) <= 1200
        for doc in observed[0]
        for field in (doc.id, doc.source, doc.kind, doc.text)
    )
    serialized = "\n".join(
        f"[{doc.id}] kind={doc.kind} source={doc.source}: {doc.text[:300]}"
        for doc in observed[0]
    )
    assert len(serialized.encode("utf-8")) <= 28_000


def test_synthesis_narration_uses_legacy_guard_not_batch_allocation(
    monkeypatch, tmp_path
):
    observed = []

    @contextmanager
    def gate(_log, *, batch_allocation=False):
        observed.append(batch_allocation)
        yield False

    monkeypatch.setenv("TRUSTFORGE_MULTI_ANGLE_NARRATION", "1")
    monkeypatch.setattr("trustforge.analysis_flow._bedrock_live_attempt", gate)
    with AnalysisFlow(tmp_path / "flow.db") as flow:
        # The synthesis helper itself is exercised elsewhere; this assertion
        # pins the only narration call site's allocation boundary.
        source = __import__("inspect").getsource(flow._complete_claimed_synthesis)
        assert "_bedrock_live_attempt(narration_log)" in source
    assert observed == []
