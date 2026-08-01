from __future__ import annotations

import importlib.util
import json
import threading
from decimal import Decimal
from pathlib import Path

import pytest

from trustforge import ledger as ledger_module
from trustforge.bedrock import LLMResult

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _load_reviewer():
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "review_hermes_upgrades.py"
    )
    spec = importlib.util.spec_from_file_location(
        "review_hermes_upgrades_budget_test", script
    )
    assert spec and spec.loader
    reviewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reviewer)
    return reviewer


def _diagnostic() -> dict:
    return {
        "proposals": [
            {
                "id": "p1",
                "area": "reviewer",
                "severity": "high",
                "evidence": {"missed_budget_gate": 1},
                "proposed_experiment": "reuse atomic reservation",
                "success_metric": "zero unreserved calls",
            }
        ]
    }


class _FakeTable:
    def __init__(self, order: list[str]):
        self.order = order
        self.items: list[dict] = []

    def put_item(self, *, Item):
        self.order.append("ledger_append")
        self.items.append(Item)
        return {}


class _Client:
    def __init__(self, order: list[str]):
        self.order = order
        self.calls = 0

    def complete(self, *, system: str, prompt: str) -> LLMResult:
        self.calls += 1
        self.order.append("bedrock")
        assert "hostile change reviewer" in system
        assert "missed_budget_gate" in prompt
        return LLMResult(
            text=json.dumps(
                {
                    "reviews": [
                        {
                            "proposal_id": "p1",
                            "verdict": "sandbox_ready",
                            "reasons": ["guard is measurable"],
                            "required_checks": ["cap zero"],
                        }
                    ]
                }
            ),
            input_tokens=100,
            output_tokens=20,
            model_id=MODEL_ID,
        )


def test_review_reserves_then_appends_dynamodb_ledger_before_release(monkeypatch):
    reviewer = _load_reviewer()
    order: list[str] = []
    table = _FakeTable(order)
    dynamodb_ledger = ledger_module.DynamoDBLedger()
    dynamodb_ledger._table = table

    monkeypatch.setattr(reviewer.budget_guard, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(
        reviewer.budget_guard,
        "budget_reservation_backend",
        lambda: "dynamodb",
    )

    def reserve(*, backend):
        assert backend == "dynamodb"
        order.append("reserve")
        return 0.05

    monkeypatch.setattr(reviewer.budget_guard, "try_reserve_request_budget", reserve)
    monkeypatch.setattr(
        reviewer.budget_guard,
        "release_request_budget",
        lambda amount, *, backend: order.append(f"release:{backend}:{amount}"),
    )
    monkeypatch.setattr(
        reviewer.budget_guard,
        "settle_request_budget",
        lambda amount, actual_cost, *, backend: order.append(
            f"settle:{backend}:{amount}:{actual_cost}"
        )
        or True,
    )
    monkeypatch.setattr(ledger_module, "get_ledger", lambda: dynamodb_ledger)

    client = _Client(order)
    result = reviewer._review_with_budget(
        _diagnostic(), client, now_fn=lambda: 1_700_000_000
    )

    assert result["status"] == "reviewed"
    assert client.calls == 1
    assert order == [
        "reserve",
        "bedrock",
        "ledger_append",
        "settle:dynamodb:0.05:0.0002",
    ]
    assert len(table.items) == 1
    item = table.items[0]
    assert item["question_type"] == "hermes_upgrade_review"
    assert item["calls"] == [
        {
            "model": MODEL_ID,
            "tokens_in": 100,
            "tokens_out": 20,
            "cost_usd": Decimal("0.0002"),
        }
    ]
    assert item["total_cost_usd"] == Decimal("0.0002")
    assert item["run_id"]


def test_shared_counter_retains_capacity_when_atomic_settlement_is_unavailable(
    monkeypatch,
):
    reviewer = _load_reviewer()
    order: list[str] = []
    table = _FakeTable(order)
    dynamodb_ledger = ledger_module.DynamoDBLedger()
    dynamodb_ledger._table = table
    released: list[float] = []

    monkeypatch.setattr(reviewer.budget_guard, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(
        reviewer.budget_guard, "budget_reservation_backend", lambda: "dynamodb"
    )
    monkeypatch.setattr(
        reviewer.budget_guard, "try_reserve_request_budget", lambda **_kwargs: 0.05
    )
    monkeypatch.setattr(
        reviewer.budget_guard, "settle_request_budget", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        reviewer.budget_guard,
        "release_request_budget",
        lambda amount, **_kwargs: released.append(amount),
    )
    monkeypatch.setattr(ledger_module, "get_ledger", lambda: dynamodb_ledger)

    result = reviewer._review_with_budget(_diagnostic(), _Client(order))

    assert result["status"] == "reviewed"
    assert order == ["bedrock", "ledger_append"]
    assert released == []


def test_cap_zero_is_a_hard_kill_switch(monkeypatch):
    reviewer = _load_reviewer()
    monkeypatch.setenv("BEDROCK_MODEL_ID", MODEL_ID)
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "0")
    monkeypatch.setenv("TRUSTFORGE_BUDGET_GUARD_BACKEND", "local")
    client = _Client([])

    result = reviewer._review_with_budget(_diagnostic(), client)

    assert result == {
        "status": "budget_denied",
        "reason": "budget_reservation_denied",
        "reviews": [],
        "can_activate": False,
    }
    assert client.calls == 0


def test_denied_reservation_never_calls_bedrock(monkeypatch):
    reviewer = _load_reviewer()
    monkeypatch.setattr(reviewer.budget_guard, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(
        reviewer.budget_guard, "budget_reservation_backend", lambda: "local"
    )
    monkeypatch.setattr(
        reviewer.budget_guard,
        "try_reserve_request_budget",
        lambda **_kwargs: None,
    )
    client = _Client([])

    result = reviewer._review_with_budget(_diagnostic(), client)

    assert result["status"] == "budget_denied"
    assert client.calls == 0


def test_zero_reservation_never_calls_bedrock(monkeypatch):
    reviewer = _load_reviewer()
    monkeypatch.setattr(reviewer.budget_guard, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(reviewer.budget_guard, "budget_reservation_backend", lambda: "local")
    monkeypatch.setattr(
        reviewer.budget_guard, "try_reserve_request_budget", lambda **_kwargs: 0.0
    )
    client = _Client([])

    result = reviewer._review_with_budget(_diagnostic(), client)

    assert result["status"] == "budget_denied"
    assert client.calls == 0


def test_oversized_review_input_never_reserves_or_calls_bedrock(monkeypatch):
    reviewer = _load_reviewer()
    reserved: list[bool] = []
    monkeypatch.setattr(
        reviewer.budget_guard,
        "try_reserve_request_budget",
        lambda **_kwargs: reserved.append(True) or 0.05,
    )
    diagnostic = _diagnostic()
    diagnostic["proposals"][0]["evidence"] = {"blob": "x" * 4_097}
    client = _Client([])

    result = reviewer._review_with_budget(diagnostic, client)

    assert result["status"] == "input_too_large"
    assert reserved == []
    assert client.calls == 0


def test_reservation_failure_is_fail_closed(monkeypatch):
    reviewer = _load_reviewer()
    monkeypatch.setattr(reviewer.budget_guard, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(
        reviewer.budget_guard, "budget_reservation_backend", lambda: "local"
    )

    def fail_reservation(**_kwargs):
        raise RuntimeError("counter unavailable")

    monkeypatch.setattr(
        reviewer.budget_guard,
        "try_reserve_request_budget",
        fail_reservation,
    )
    client = _Client([])

    result = reviewer._review_with_budget(_diagnostic(), client)

    assert result["status"] == "budget_unavailable"
    assert result["reason"] == "budget_reservation_failed"
    assert client.calls == 0


def test_missing_durable_receipt_retains_shared_reservation(monkeypatch):
    reviewer = _load_reviewer()
    released: list[float] = []
    unledgered: list[float] = []
    monkeypatch.setattr(reviewer.budget_guard, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(
        reviewer.budget_guard,
        "budget_reservation_backend",
        lambda: "dynamodb",
    )
    monkeypatch.setattr(
        reviewer.budget_guard,
        "try_reserve_request_budget",
        lambda **_kwargs: 0.05,
    )
    monkeypatch.setattr(reviewer.ledger_module, "append_run", lambda _record: False)
    monkeypatch.setattr(
        reviewer.budget_guard,
        "record_unledgered_spend",
        unledgered.append,
    )
    monkeypatch.setattr(
        reviewer.budget_guard,
        "release_request_budget",
        lambda amount, **_kwargs: released.append(amount),
    )
    client = _Client([])

    result = reviewer._review_with_budget(_diagnostic(), client)

    assert result["status"] == "accounting_failed"
    assert unledgered == [0.0002]
    assert released == []


def test_shared_primary_failure_local_fallback_cannot_release_reservation(monkeypatch):
    reviewer = _load_reviewer()
    released: list[float] = []
    local_fallback: list[dict] = []
    monkeypatch.setattr(reviewer.budget_guard, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(reviewer.budget_guard, "budget_reservation_backend", lambda: "dynamodb")
    monkeypatch.setattr(
        reviewer.budget_guard, "try_reserve_request_budget", lambda **_kwargs: 0.05
    )
    monkeypatch.setattr(reviewer.budget_guard, "reservation_is_durable_shared", lambda _r: True)

    class FailingSharedLedger(reviewer.ledger_module.DynamoDBLedger):
        def append(self, _record):
            raise RuntimeError("shared ledger unavailable")

    monkeypatch.setattr(reviewer.ledger_module, "get_ledger", FailingSharedLedger)
    monkeypatch.setattr(
        reviewer.ledger_module,
        "append_run",
        lambda record: local_fallback.append(record) or True,
    )
    monkeypatch.setattr(
        reviewer.budget_guard,
        "release_request_budget",
        lambda amount, **_kwargs: released.append(amount),
    )

    result = reviewer._review_with_budget(_diagnostic(), _Client([]))

    assert result["status"] == "accounting_failed"
    assert local_fallback == []
    assert released == []


@pytest.mark.parametrize(
    "status",
    [
        "reviewed",
        "budget_denied",
        "budget_unavailable",
        "review_failed",
        "accounting_failed",
    ],
)
def test_each_review_outcome_is_append_only_and_fsynced(tmp_path, status):
    reviewer = _load_reviewer()
    path = tmp_path / "review-runs.jsonl"

    first = reviewer._persist_review_outcome(
        {"status": status, "reviews": [], "can_activate": False}, path
    )
    second = reviewer._persist_review_outcome(
        {"status": status, "reviews": [], "can_activate": False}, path
    )

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["run_id"] == first["run_id"]
    assert rows[1]["run_id"] == second["run_id"]
    assert rows[0]["run_id"] != rows[1]["run_id"]
    assert all(row["status"] == status and row["ts"] for row in rows)


@pytest.mark.parametrize("shared", [False, True])
def test_missing_usage_retains_or_conservatively_charges_reservation(
    monkeypatch, shared
):
    reviewer = _load_reviewer()
    released: list[float] = []
    unledgered: list[float] = []
    uncertain: list[float] = []
    backend = "dynamodb" if shared else "local"
    monkeypatch.setattr(reviewer.budget_guard, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(
        reviewer.budget_guard, "budget_reservation_backend", lambda: backend
    )
    monkeypatch.setattr(
        reviewer.budget_guard, "try_reserve_request_budget", lambda **_kwargs: 0.05
    )
    monkeypatch.setattr(
        reviewer.budget_guard, "record_unledgered_spend", unledgered.append
    )
    monkeypatch.setattr(
        reviewer.budget_guard,
        "mark_reservation_accounting_uncertain",
        uncertain.append,
    )
    monkeypatch.setattr(
        reviewer.budget_guard,
        "release_request_budget",
        lambda amount, **_kwargs: released.append(amount),
    )

    class MissingUsageClient:
        def complete(self, **_kwargs):
            return LLMResult(
                text='{"reviews": []}',
                input_tokens=0,
                output_tokens=0,
                model_id=MODEL_ID,
            )

    result = reviewer._review_with_budget(_diagnostic(), MissingUsageClient())

    assert result["status"] == "accounting_failed"
    assert result["reason"] == "bedrock_usage_metadata_ambiguous"
    assert unledgered == [0.05]
    assert uncertain == ([0.05] if shared else [])
    assert released == ([] if shared else [0.05])


def test_bedrock_exception_is_durable_failure_candidate_and_never_releases_shared(
    monkeypatch,
):
    reviewer = _load_reviewer()
    released: list[float] = []
    monkeypatch.setattr(reviewer.budget_guard, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(
        reviewer.budget_guard, "budget_reservation_backend", lambda: "dynamodb"
    )
    monkeypatch.setattr(
        reviewer.budget_guard, "try_reserve_request_budget", lambda **_kwargs: 0.05
    )
    monkeypatch.setattr(
        reviewer.budget_guard, "record_unledgered_spend", lambda _amount: None
    )
    monkeypatch.setattr(
        reviewer.budget_guard,
        "mark_reservation_accounting_uncertain",
        lambda _reservation: None,
    )
    monkeypatch.setattr(
        reviewer.budget_guard,
        "release_request_budget",
        lambda amount, **_kwargs: released.append(amount),
    )

    class FailingClient:
        def complete(self, **_kwargs):
            raise RuntimeError("provider timeout after acceptance")

    result = reviewer._review_with_budget(_diagnostic(), FailingClient())

    assert result["status"] == "review_failed"
    assert result["reason"] == "bedrock_call_failed"
    assert released == []


def test_concurrent_atomic_reservation_allows_only_one_bedrock_call(monkeypatch):
    reviewer = _load_reviewer()
    lock = threading.Lock()
    reserved = False
    calls: list[int] = []

    monkeypatch.setattr(reviewer.budget_guard, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(
        reviewer.budget_guard, "budget_reservation_backend", lambda: "dynamodb"
    )

    def reserve(**_kwargs):
        nonlocal reserved
        with lock:
            if reserved:
                return None
            reserved = True
            return 0.05

    monkeypatch.setattr(reviewer.budget_guard, "try_reserve_request_budget", reserve)
    monkeypatch.setattr(
        reviewer.budget_guard, "release_request_budget", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(reviewer.ledger_module, "get_ledger", lambda: None)

    class CountingClient:
        def complete(self, **_kwargs):
            calls.append(1)
            return LLMResult(
                text='{"reviews": []}',
                input_tokens=10,
                output_tokens=2,
                model_id=MODEL_ID,
            )

    results: list[dict] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(
                reviewer._review_with_budget(_diagnostic(), CountingClient())
            )
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert sorted(result["status"] for result in results) == [
        "accounting_failed",
        "budget_denied",
    ]
