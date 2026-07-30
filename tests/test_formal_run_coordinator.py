from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trustforge.analysis_flow import AnalysisFlow
from trustforge.formal_run_coordinator import (
    FormalBudgetReservation,
    FormalRunCoordinator,
    FormalRunSecrets,
)
from trustforge.formal_run_idempotency import IdempotencyUnavailable
from trustforge.formal_run_idempotency_sqlite import SqliteFormalRunIdempotencyStore
from trustforge.formal_budget_reservation import SqliteFormalBudgetAuthority

NOW = datetime(2026, 7, 30, 8, tzinfo=timezone.utc)


def idempotency_key(byte=9):
    encoded = base64.urlsafe_b64encode(bytes([byte]) * 16).decode().rstrip("=")
    return f"tf1.202607.{encoded}"


def secrets():
    return FormalRunSecrets(
        caller_secret=b"c" * 32,
        caller_key_id="caller-v1",
        idempotency_secret=b"k" * 32,
        idempotency_key_id="key-v1",
        retention_locator_secret=b"l" * 32,
        fingerprint_secret=b"f" * 32,
        fingerprint_key_id="fingerprint-v1",
        content_secret=b"n" * 32,
        content_key_id="content-v1",
    )


class TimeoutAfterCommitStore(SqliteFormalRunIdempotencyStore):
    def bind_with_content_decision(self, **kwargs):
        super().bind_with_content_decision(**kwargs)
        raise IdempotencyUnavailable("simulated response timeout after commit")


def test_unknown_bind_outcome_holds_reservation_and_replays_durable_receipt(tmp_path):
    store = TimeoutAfterCommitStore(tmp_path / "authority.db", environment="test")
    released = []
    with AnalysisFlow(tmp_path / "flow.db") as flow:
        coordinator = FormalRunCoordinator(
            store=store,
            flow=flow,
            secrets=secrets(),
            reserve_budget=lambda _operation: FormalBudgetReservation(
                "res-real-1", "0.050000"
            ),
            release_budget=released.append,
            now=lambda: NOW,
        )
        with pytest.raises(IdempotencyUnavailable, match="response timeout"):
            coordinator.submit(
                idempotency_keys=idempotency_key(),
                caller_scope="tenant-a",
                coin="BTC",
                mode="risk",
                question="Assess risk",
                locale="zh-Hant",
                fresh=False,
            )
        assert released == []
        assert flow._conn().execute(
            "SELECT state FROM formal_analysis_projection_queue"
        ).fetchone()["state"] == "pending_authority"
        assert flow.reconcile_staged_formal_projections(store) == 1
        assert flow._conn().execute(
            "SELECT state FROM formal_analysis_projection_queue"
        ).fetchone()["state"] == "pending"
        replay = coordinator.submit(
            idempotency_keys=idempotency_key(),
            caller_scope="tenant-a",
            coin="BTC",
            mode="risk",
            question="Assess risk",
            locale="zh-Hant",
            fresh=False,
            admit_owner=lambda: (_ for _ in ()).throw(
                AssertionError("durable replay must bypass write limiter")
            ),
        )
    assert replay.status == 202
    assert replay.replayed is True
    assert replay.body["disposition"] == "created"


def test_owner_rate_limit_is_terminal_and_replays_without_reacquire(tmp_path):
    store = SqliteFormalRunIdempotencyStore(
        tmp_path / "authority.db", environment="test"
    )
    coordinator = FormalRunCoordinator(
        store=store,
        flow=AnalysisFlow(tmp_path / "flow.db"),
        secrets=secrets(),
        reserve_budget=lambda _operation: (_ for _ in ()).throw(
            AssertionError("rate-limited owner must not reserve")
        ),
        release_budget=lambda _reservation: None,
        now=lambda: NOW,
    )
    first = coordinator.submit(
        idempotency_keys=idempotency_key(3),
        caller_scope="browser-stable",
        coin="BTC",
        mode="risk",
        question="Assess risk",
        locale="zh-Hant",
        fresh=False,
        admit_owner=lambda: (_ for _ in ()).throw(RuntimeError("limited")),
    )
    assert first.status == 429
    replay = coordinator.submit(
        idempotency_keys=idempotency_key(3),
        caller_scope="browser-stable",
        coin="BTC",
        mode="risk",
        question="Assess risk",
        locale="zh-Hant",
        fresh=False,
        admit_owner=lambda: (_ for _ in ()).throw(
            AssertionError("terminal replay bypasses limiter")
        ),
    )
    assert replay.status == 429
    assert replay.replayed


def test_unknown_reserve_commit_is_restart_reconciled_before_dispatch(tmp_path):
    store = SqliteFormalRunIdempotencyStore(
        tmp_path / "authority.db", environment="test"
    )
    budget = SqliteFormalBudgetAuthority(
        tmp_path / "budget.db", environment="test"
    )
    flow = AnalysisFlow(tmp_path / "flow.db")

    def reserve_then_timeout(operation_id):
        reservation_id = "br_" + __import__("hashlib").sha256(
            operation_id.encode()
        ).hexdigest()[:32]
        assert budget.reserve(
            reservation_id=reservation_id,
            spent=Decimal("0"),
            cost=Decimal("0.2"),
            cap=Decimal("1"),
            now=NOW,
        )
        raise TimeoutError("reserve outcome unknown")

    coordinator = FormalRunCoordinator(
        store=store,
        flow=flow,
        secrets=secrets(),
        reserve_budget=reserve_then_timeout,
        release_budget=budget.release,
        now=lambda: NOW,
    )
    with pytest.raises(TimeoutError, match="unknown"):
        coordinator.submit(
            idempotency_keys=idempotency_key(4),
            caller_scope="browser-stable",
            coin="BTC",
            mode="risk",
            question="Assess risk",
            locale="zh-Hant",
            fresh=False,
        )
    flow._conn().execute(
        "UPDATE formal_analysis_projection_queue SET created_at=created_at-120"
    )
    restarted = AnalysisFlow(tmp_path / "flow.db")
    assert restarted.reconcile_staged_formal_projections(store, budget) == 0
    assert restarted._conn().execute(
        "SELECT 1 FROM formal_analysis_projection_queue"
    ).fetchone() is None
