from datetime import datetime, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

from trustforge import budget_guard
from trustforge.formal_budget_reservation import (
    DynamoDbFormalBudgetAuthority,
    SqliteFormalBudgetAuthority,
)
from trustforge.formal_run_coordinator import FormalBudgetReservation
from trustforge.formal_run_idempotency import IdempotencyUnavailable
import pytest

NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


def test_sqlite_budget_tokens_are_bounded_and_release_is_idempotent(tmp_path):
    authority = SqliteFormalBudgetAuthority(
        tmp_path / "budget.db", environment="test"
    )
    first = authority.reserve(
        reservation_id="br_test_1",
        spent=Decimal("0"), cost=Decimal("0.6"), cap=Decimal("1"), now=NOW
    )
    assert first is not None
    assert (
        authority.reserve(
            reservation_id="br_test_2",
            spent=Decimal("0"), cost=Decimal("0.6"), cap=Decimal("1"), now=NOW
        )
        is None
    )
    assert authority.release(first) is True
    assert authority.release(first) is False
    second = authority.reserve(
        reservation_id="br_test_3",
        spent=Decimal("0"), cost=Decimal("0.6"), cap=Decimal("1"), now=NOW
    )
    assert second is not None
    assert authority.settle(second, Decimal("0.4")) is True
    assert authority.settle(second, Decimal("0.4")) is True
    assert authority.settle(second, Decimal("0.3")) is False
    # Settled actual spend remains counted, while unused reservation headroom
    # is released exactly once.
    assert authority.reserve(
        reservation_id="br_test_4",
        spent=Decimal("0"), cost=Decimal("0.6"), cap=Decimal("1"), now=NOW
    ) is not None


def test_sqlite_actual_cost_above_reservation_is_rejected_without_mutation(tmp_path):
    authority = SqliteFormalBudgetAuthority(
        tmp_path / "budget.db", environment="test"
    )
    token = authority.reserve(
        reservation_id="br_overrun",
        spent=Decimal("0"),
        cost=Decimal("0.6"),
        cap=Decimal("1"),
        now=NOW,
    )
    assert token is not None
    with pytest.raises(IdempotencyUnavailable, match="actual cost is invalid"):
        authority.settle(token, Decimal("0.8"))
    assert authority.lookup(token.reservation_id) == token
    with sqlite3.connect(tmp_path / "budget.db") as db:
        assert db.execute(
            "SELECT reserved_total,spent_total FROM formal_budget_day"
        ).fetchone() == ("0.6", "0")


def test_dynamodb_actual_cost_above_reservation_is_rejected_before_io():
    class NoIoClient:
        def get_item(self, **_kwargs):
            raise AssertionError("overrun validation must happen before DynamoDB I/O")

    authority = DynamoDbFormalBudgetAuthority(NoIoClient(), table_name="formal")
    token = FormalBudgetReservation("br_overrun", "0.6")
    with pytest.raises(IdempotencyUnavailable, match="actual cost is invalid"):
        authority.settle(token, Decimal("0.8"))


def test_uncertain_token_release_is_restart_idempotent(tmp_path):
    path = tmp_path / "uncertain-release.db"
    authority = SqliteFormalBudgetAuthority(path, environment="test")
    token = authority.reserve(
        reservation_id="br_uncertain_release",
        spent=Decimal("0"), cost=Decimal("0.6"), cap=Decimal("1"), now=NOW,
    )
    assert token is not None
    assert authority.mark_uncertain(token) is True

    restarted = SqliteFormalBudgetAuthority(path, environment="test")
    assert restarted.release(token) is True
    assert restarted.release(token) is False
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT reserved_total,spent_total FROM formal_budget_day"
        ).fetchone() == ("0.0", "0")


def test_uncertain_token_settle_is_restart_idempotent(tmp_path):
    path = tmp_path / "uncertain-settle.db"
    authority = SqliteFormalBudgetAuthority(path, environment="test")
    token = authority.reserve(
        reservation_id="br_uncertain_settle",
        spent=Decimal("0"), cost=Decimal("0.6"), cap=Decimal("1"), now=NOW,
    )
    assert token is not None
    assert authority.mark_uncertain(token) is True

    restarted = SqliteFormalBudgetAuthority(path, environment="test")
    assert restarted.settle(token, Decimal("0.4")) is True
    assert restarted.settle(token, Decimal("0.4")) is True
    assert restarted.settle(token, Decimal("0.3")) is False
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT reserved_total,spent_total FROM formal_budget_day"
        ).fetchone() == ("0.0", "0.4")


def test_uncertain_release_vs_settle_has_exactly_one_terminal_winner(tmp_path):
    path = tmp_path / "uncertain-race.db"
    authority = SqliteFormalBudgetAuthority(path, environment="test")
    token = authority.reserve(
        reservation_id="br_uncertain_race",
        spent=Decimal("0"), cost=Decimal("0.6"), cap=Decimal("1"), now=NOW,
    )
    assert token is not None
    assert authority.mark_uncertain(token) is True
    barrier = threading.Barrier(2)

    def release():
        barrier.wait()
        return SqliteFormalBudgetAuthority(path, environment="test").release(token)

    def settle():
        barrier.wait()
        return SqliteFormalBudgetAuthority(path, environment="test").settle(
            token, Decimal("0.4")
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result() for future in (pool.submit(release), pool.submit(settle))]
    assert sorted(outcomes) == [False, True]
    with sqlite3.connect(path) as db:
        reserved, spent = db.execute(
            "SELECT reserved_total,spent_total FROM formal_budget_day"
        ).fetchone()
        state = db.execute(
            "SELECT state FROM formal_budget_reservation WHERE reservation_id=?",
            (token.reservation_id,),
        ).fetchone()[0]
    assert reserved == "0.0"
    assert (state, spent) in {("released", "0"), ("settled", "0.4")}


def test_legacy_spend_and_formal_reservations_share_one_daily_cap(tmp_path):
    class Ledger:
        def read_all(self):
            return [
                {
                    "ts": "2026-07-30T01:00:00Z",
                    "total_cost_usd": 0.7,
                    "accounting_authority": "legacy",
                },
                {
                    "ts": "2026-07-30T02:00:00Z",
                    "total_cost_usd": 0.2,
                    "accounting_authority": "formal",
                },
            ]

    nonformal = Decimal(
        str(
            budget_guard.daily_nonformal_cost_usd(
                Ledger(), day="2026-07-30"
            )
        )
    )
    authority = SqliteFormalBudgetAuthority(
        tmp_path / "shared-cap.db", environment="test"
    )
    assert nonformal == Decimal("0.7")
    assert authority.reserve(
        reservation_id="br_cross_path",
        spent=nonformal,
        cost=Decimal("0.4"),
        cap=Decimal("1"),
        now=NOW,
    ) is None


def test_formal_and_legacy_inflight_share_one_sqlite_barrier(
    tmp_path, monkeypatch
):
    path = tmp_path / "unified.db"
    monkeypatch.setenv("TRUSTFORGE_ENV", "test")
    monkeypatch.setenv("TRUSTFORGE_FORMAL_BUDGET_SQLITE_PATH", str(path))
    monkeypatch.setattr(budget_guard, "daily_cap_usd", lambda: 1.0)
    monkeypatch.setattr(budget_guard, "request_max_cost_usd", lambda: 0.6)
    monkeypatch.setattr(
        budget_guard, "daily_nonformal_cost_usd", lambda *_args, **_kwargs: 0.0
    )
    authority = SqliteFormalBudgetAuthority(path, environment="test")
    barrier = threading.Barrier(2)

    def legacy():
        barrier.wait()
        return budget_guard.try_reserve_request_budget(now_fn=lambda: NOW.timestamp())

    def formal():
        barrier.wait()
        return authority.reserve(
            reservation_id="br-concurrent-formal",
            spent=Decimal("0"),
            cost=Decimal("0.6"),
            cap=Decimal("1"),
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        legacy_result, formal_result = [
            future.result()
            for future in (pool.submit(legacy), pool.submit(formal))
        ]
    assert sum(result is not None for result in (legacy_result, formal_result)) == 1


def test_dynamodb_runtime_without_formal_authority_fails_closed(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_ENV", raising=False)
    monkeypatch.setenv("CACHE_BACKEND", "dynamodb")
    monkeypatch.delenv("TRUSTFORGE_FORMAL_RUN_DYNAMODB_TABLE", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.setattr(budget_guard, "daily_cap_usd", lambda: 1.0)
    monkeypatch.setattr(budget_guard, "request_max_cost_usd", lambda: 0.1)

    assert budget_guard.try_reserve_request_budget() is None
