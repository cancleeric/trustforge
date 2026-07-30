from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trustforge.formal_run_worker import FormalRunWorker

NOW = datetime(2026, 7, 30, 8, tzinfo=timezone.utc)
ROW = {
    "namespace": "formal-analysis",
    "scope_locator": "a" * 64,
    "caller_key_id": "caller-v1",
    "caller_scope_hmac": "b" * 64,
    "key_key_id": "key-v1",
    "key_hmac": "c" * 64,
    "operation_id": "op_1",
    "fencing_token": 7,
}


class Queue:
    def __init__(self):
        self.row = dict(ROW)
        self.row["claim_token"] = "claim-1"
        self.transitions = []

    def claim_formal_projection(self, **_kwargs):
        return self.row

    def set_formal_projection_state(self, **kwargs):
        self.transitions.append(kwargs)


class Store:
    def __init__(self, resolution="pending"):
        self.resolution = resolution
        self.calls = []

    def dispatch_resolution(self, **kwargs):
        self.calls.append("resolution")
        return self.resolution

    def provider_operation(self, **kwargs):
        self.calls.append("provider")
        return "provider-op"

    def reservation_details(self, **kwargs):
        self.calls.append("reservation")
        return "res-real", "1"

    def claim_dispatch(self, **kwargs):
        self.calls.append("claim")
        return "provider-op"

    def mark_execution_uncertain(self, **kwargs):
        self.calls.append("uncertain")

    def complete_dispatch(self, **kwargs):
        self.calls.append("complete")


def test_worker_claims_authority_before_execute_and_completes():
    store, queue, observed = Store(), Queue(), []
    worker = FormalRunWorker(
        store=store,  # type: ignore[arg-type]
        queue=queue,
        execute=lambda _row, provider: (
            observed.append((provider, tuple(store.calls))) or Decimal("0.2")
        ),
        reconcile=lambda _provider: ("unknown", None),
        settle_budget=lambda *_args: store.calls.append("settle") or True,
        now=lambda: NOW,
    )
    assert worker.run_once() == "completed"
    assert observed == [("provider-op", ("resolution", "claim", "reservation"))]
    assert store.calls == [
        "resolution", "claim", "reservation", "reservation", "settle", "complete"
    ]
    assert [item["state"] for item in queue.transitions] == ["collecting", "completed"]


def test_recovery_after_shared_claim_never_resends():
    store, queue, executed = Store(resolution="claimed"), Queue(), []
    worker = FormalRunWorker(
        store=store,  # type: ignore[arg-type]
        queue=queue,
        execute=lambda *_args: executed.append(True),
        reconcile=lambda _provider: ("unknown", None),
        settle_budget=lambda *_args: True,
        now=lambda: NOW,
    )
    assert worker.run_once() == "execution_uncertain"
    assert executed == []
    assert store.calls == ["resolution", "provider", "uncertain"]
    assert queue.transitions[-1]["state"] == "execution_uncertain"


def test_executor_fault_marks_uncertain_and_never_completes():
    store, queue = Store(), Queue()

    def fail(*_args):
        raise TimeoutError("unknown provider outcome")

    worker = FormalRunWorker(
        store=store,  # type: ignore[arg-type]
        queue=queue,
        execute=fail,
        reconcile=lambda _provider: ("unknown", None),
        settle_budget=lambda *_args: True,
        now=lambda: NOW,
    )
    assert worker.run_once() == "execution_uncertain"
    assert store.calls == ["resolution", "claim", "reservation", "uncertain"]
    assert queue.transitions[-1]["state"] == "execution_uncertain"


def test_failed_budget_settlement_never_completes_dispatch():
    store, queue = Store(), Queue()
    worker = FormalRunWorker(
        store=store,  # type: ignore[arg-type]
        queue=queue,
        execute=lambda *_args: Decimal("0.2"),
        reconcile=lambda _provider: ("unknown", None),
        settle_budget=lambda *_args: False,
        now=lambda: NOW,
    )
    assert worker.run_once() == "execution_uncertain"
    assert "complete" not in store.calls
    assert store.calls[-1] == "uncertain"
    assert queue.transitions[-1]["state"] == "execution_uncertain"


def test_completed_authority_repairs_stale_local_claim_without_resend():
    store, queue, executed = Store(resolution="completed"), Queue(), []
    worker = FormalRunWorker(
        store=store,  # type: ignore[arg-type]
        queue=queue,
        execute=lambda *_args: executed.append(True),
        reconcile=lambda _provider: ("unknown", None),
        settle_budget=lambda *_args: True,
        now=lambda: NOW,
    )
    assert worker.run_once() == "completed"
    assert executed == []
    assert store.calls == ["resolution"]


def test_reconciler_confirms_claimed_success_without_resend():
    store, queue, executed = Store(resolution="claimed"), Queue(), []
    worker = FormalRunWorker(
        store=store,  # type: ignore[arg-type]
        queue=queue,
        execute=lambda *_args: executed.append(True),
        reconcile=lambda _provider: ("completed", Decimal("0.2")),
        settle_budget=lambda *_args: store.calls.append("settle") or True,
        now=lambda: NOW,
    )
    assert worker.run_once() == "completed"
    assert executed == []
    assert store.calls == [
        "resolution", "provider", "reservation", "settle", "complete"
    ]


def test_reconciler_pending_keeps_collecting_without_marking_uncertain():
    store, queue = Store(resolution="claimed"), Queue()
    queue.row["state"] = "collecting"
    worker = FormalRunWorker(
        store=store,  # type: ignore[arg-type]
        queue=queue,
        execute=lambda *_args: Decimal("99"),
        reconcile=lambda _provider: ("pending", None),
        settle_budget=lambda *_args: True,
        now=lambda: NOW,
    )
    assert worker.run_once() == "collecting"
    assert store.calls == ["resolution", "provider"]
    assert queue.transitions == []
