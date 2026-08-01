"""#1308: Lambda 分散式 Bedrock 1 RPS 全域鎖的行為契約。

覆蓋規範四項驗收：
- 用既有 competition budget 表 + 既有 IAM 動作（GetItem/UpdateItem）做全域鎖
- 每次真實 invoke 都被鎖包住，cooldown 持續到 invoke 起始後一秒
- owner-conditional release；DynamoDB / 競爭期限 / release 失敗一律 fail-closed
- 非 Lambda 的 flock 行為原封不動
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from trustforge import bedrock as bedrock_module
from trustforge.bedrock import BedrockClient, BedrockConfig, BedrockRpsLimiter
from trustforge.bedrock_rps_lock import (
    BedrockLockBackendError,
    BedrockLockContentionError,
    BedrockLockError,
    DynamoDBBedrockRpsLock,
)

_CONDITION_FAILED = ClientError(
    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}}, "UpdateItem"
)
_THROTTLED = ClientError(
    {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow"}},
    "UpdateItem",
)


class _Clock:
    def __init__(self) -> None:
        self.wall = 1_800_000_000.0
        self.mono = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.wall

    def monotonic(self) -> float:
        return self.mono

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.wall += seconds
        self.mono += seconds


class _FakeTable:
    """單一 item 的最小 DynamoDB 模擬，只支援本模組用到的條件式 UpdateItem。

    條件式判斷刻意在「表」這一側評估（而不是在被測程式碼裡），才能真的驗到
    「兩個呼叫者搶同一個 item 時，DynamoDB 會拒絕其中一個」的行為。
    """

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.item: dict[str, object] = {}
        self.calls: list[dict] = []
        self.fail_with: Exception | None = None

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_with is not None:
            raise self.fail_with
        values = kwargs["ExpressionAttributeValues"]
        if "lock_owner = :owner" in kwargs["UpdateExpression"]:
            return self._acquire(kwargs["ConditionExpression"], values)
        return self._release(kwargs["ConditionExpression"], values)

    def get_item(self, **kwargs):
        return {"Item": dict(self.item)}

    def _acquire(self, condition, values):
        available_at = self.item.get("available_at")
        # ConditionExpression: attribute_not_exists(available_at)
        #                      OR available_at <= :now
        comparison = condition.get_expression()["values"][1].get_expression()
        candidate_now = float(comparison["values"][1])
        if available_at is not None and float(available_at) > candidate_now:
            raise _CONDITION_FAILED
        self.item["lock_owner"] = values[":owner"]
        self.item["available_at"] = values[":guard"]
        return {}

    def _release(self, condition, values):
        # ConditionExpression: Attr("lock_owner").eq(:owner)
        expected_owner = condition.get_expression()["values"][1]
        if self.item.get("lock_owner") != expected_owner:
            raise _CONDITION_FAILED
        self.item["available_at"] = values[":next"]
        return {}


def _lock(table: _FakeTable, **kwargs) -> DynamoDBBedrockRpsLock:
    kwargs.setdefault("jitter", lambda low, high: high)
    lock = DynamoDBBedrockRpsLock(table_name="competition-trustforge-team11-budget", **kwargs)
    lock._table = table
    return lock


# --- 鎖語意 ----------------------------------------------------------------


def test_lock_uses_existing_budget_table_reserved_key() -> None:
    clock = _Clock()
    table = _FakeTable(clock)
    lock = _lock(table, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)

    with lock.slot():
        pass

    acquire_call = table.calls[0]
    assert acquire_call["Key"] == {"source_id": "__bedrock_rps_lock__", "coin": "global"}
    assert lock.table_name == "competition-trustforge-team11-budget"


def test_cooldown_is_held_until_one_second_after_invoke_start() -> None:
    clock = _Clock()
    table = _FakeTable(clock)
    lock = _lock(table, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)

    start = clock.wall
    with lock.slot() as invoke_start:
        assert invoke_start == start
        # 持有期間 available_at 被推到 guard，其他呼叫者一定擋得住
        assert float(table.item["available_at"]) == pytest.approx(start + lock.hold_seconds)

    assert float(table.item["available_at"]) == pytest.approx(start + 1.0)


def test_cooldown_starts_after_slow_acquire_returns() -> None:
    clock = _Clock()
    table = _FakeTable(clock)
    original_update = table.update_item

    def slow_update(**kwargs):
        clock.sleep(1.2)
        return original_update(**kwargs)

    table.update_item = slow_update
    lock = _lock(table, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)

    with lock.slot() as invoke_start:
        assert invoke_start == pytest.approx(1_800_000_001.2)

    assert float(table.item["available_at"]) == pytest.approx(invoke_start + 1.0)


def test_default_guard_covers_worst_case_narrative_timeout() -> None:
    lock = DynamoDBBedrockRpsLock()
    assert lock.hold_seconds >= 10 + 60 + 10


def test_min_interval_cannot_be_relaxed_below_one_second() -> None:
    lock = DynamoDBBedrockRpsLock(min_interval=0.01)
    assert lock.min_interval == 1.0
    # guard 也不得短於 cooldown，否則 release 失敗反而放寬節流
    assert lock.hold_seconds >= lock.min_interval


def test_second_caller_waits_until_cooldown_expires() -> None:
    clock = _Clock()
    table = _FakeTable(clock)
    lock = _lock(table, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)

    with lock.slot() as first_start:
        pass

    with lock.slot() as second_start:
        pass

    assert second_start - first_start >= 1.0
    assert clock.sleeps  # 真的等過，不是直接放行
    # available_at-aware backoff 不會產生 50ms conditional-write 風暴。
    # 全部含第一次 acquire/release 與第二次 release；第二個
    # caller 只有一次失敗 write，不是 20 次/second 忙等。
    assert len(table.calls) <= 5


def test_faster_second_wall_clock_cannot_shorten_real_cooldown() -> None:
    class SharedTimelineClock:
        def __init__(self, timeline: list[float], offset: float) -> None:
            self.timeline = timeline
            self.offset = offset

        def now(self) -> float:
            return 1_800_000_000.0 + self.timeline[0] + self.offset

        def monotonic(self) -> float:
            return self.timeline[0]

        def sleep(self, seconds: float) -> None:
            self.timeline[0] += seconds

    timeline = [0.0]
    owner_clock = SharedTimelineClock(timeline, offset=0.0)
    faster_clock = SharedTimelineClock(timeline, offset=2.0)
    table = _FakeTable(_Clock())
    owner = _lock(
        table,
        now=owner_clock.now,
        monotonic=owner_clock.monotonic,
        sleep=owner_clock.sleep,
    )
    next_caller = _lock(
        table,
        now=faster_clock.now,
        monotonic=faster_clock.monotonic,
        sleep=faster_clock.sleep,
    )

    with owner.slot():
        first_real_start = timeline[0]
    with next_caller.slot():
        second_real_start = timeline[0]

    assert second_real_start - first_real_start >= 1.0


def test_fractional_millisecond_cannot_relax_one_second_gap() -> None:
    clock = _Clock()
    clock.wall = 1_800_000_000.00049
    table = _FakeTable(clock)
    lock = _lock(table, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)

    with lock.slot() as first_start:
        pass

    # 舊的毫秒四捨五入會讓這個時點的 condition 提前成立。
    clock.sleep(0.99952)
    with lock.slot() as second_start:
        pass

    assert second_start - first_start >= 1.0


def test_contention_has_a_hard_operation_bound() -> None:
    clock = _Clock()
    table = _FakeTable(clock)
    table.item = {"lock_owner": "someone-else", "available_at": clock.wall + 3600}
    lock = _lock(
        table,
        contention_deadline_seconds=100,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    with pytest.raises(BedrockLockContentionError):
        with lock.slot():
            pass

    assert len(table.calls) == 25


# --- fail-closed -----------------------------------------------------------


def test_backend_failure_on_acquire_fails_closed() -> None:
    clock = _Clock()
    table = _FakeTable(clock)
    table.fail_with = _THROTTLED
    lock = _lock(table, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)

    with pytest.raises(BedrockLockBackendError):
        with lock.slot():
            pytest.fail("must not reach the Bedrock invoke")


def test_contention_deadline_fails_closed() -> None:
    clock = _Clock()
    table = _FakeTable(clock)
    table.item = {"lock_owner": "someone-else", "available_at": clock.wall + 3600}
    lock = _lock(
        table,
        contention_deadline_seconds=0.2,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    with pytest.raises(BedrockLockContentionError):
        with lock.slot():
            pytest.fail("must not reach the Bedrock invoke")


def test_release_is_owner_conditional_and_fails_closed_when_stolen() -> None:
    clock = _Clock()
    table = _FakeTable(clock)
    lock = _lock(table, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)

    with pytest.raises(BedrockLockError):
        with lock.slot():
            # 模擬 guard 過期後被別人接手：release 的 owner 條件必須擋下
            table.item["lock_owner"] = "another-lambda"

    # 沒有把別人的鎖改掉
    assert table.item["lock_owner"] == "another-lambda"


def test_release_backend_failure_fails_closed_and_leaves_guard() -> None:
    clock = _Clock()
    table = _FakeTable(clock)
    lock = _lock(table, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)
    start = clock.wall

    with pytest.raises(BedrockLockBackendError):
        with lock.slot():
            table.fail_with = _THROTTLED

    # available_at 仍停在 guard（不是被放寬的 start+1s）
    assert float(table.item["available_at"]) == pytest.approx(start + lock.hold_seconds)


def test_body_exception_is_not_masked_by_release_failure() -> None:
    clock = _Clock()
    table = _FakeTable(clock)
    lock = _lock(table, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)

    with pytest.raises(ZeroDivisionError):
        with lock.slot():
            table.fail_with = _THROTTLED
            raise ZeroDivisionError("invoke blew up")


# --- 與 BedrockClient 的接線 ------------------------------------------------


class _RecordingRuntime:
    def __init__(self, holder: list[str]) -> None:
        self.holder = holder

    def converse(self, **kwargs):
        self.holder.append("invoked")
        return {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "usage": {"inputTokens": 1, "outputTokens": 1},
        }


class _SpyLock:
    def __init__(self) -> None:
        self.events: list[str] = []

    def slot(self):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            self.events.append("acquire")
            try:
                yield 0.0
            finally:
                self.events.append("release")

        return _cm()


def test_lambda_invoke_is_wrapped_by_the_distributed_lock(monkeypatch) -> None:
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "competition-trustforge-team11-live")
    spy = _SpyLock()
    client = BedrockClient(
        config=BedrockConfig(region="us-east-1", model_id="fake-model"),
        offline=False,
        rps_limiter=BedrockRpsLimiter(distributed_lock=spy),
    )
    invoked: list[str] = []
    client._client = _RecordingRuntime(invoked)

    client.complete("system", "prompt")

    assert invoked == ["invoked"]
    # invoke 必須發生在 acquire 與 release 之間
    assert spy.events == ["acquire", "release"]


def test_lambda_without_distributed_lock_still_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "competition-trustforge-team11-live")
    limiter = BedrockRpsLimiter(lock_path=tmp_path / "rps.lock")
    with pytest.raises(RuntimeError, match="distributed limiter"):
        with limiter.slot():
            pytest.fail("must not reach the Bedrock invoke")


def test_non_lambda_still_uses_host_local_flock(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    spy = _SpyLock()
    limiter = BedrockRpsLimiter(lock_path=tmp_path / "rps.lock", distributed_lock=spy)

    with limiter.slot():
        pass

    assert spy.events == []  # 非 Lambda 一律不碰分散式鎖
    assert (tmp_path / "rps.lock").exists()


def test_default_limiter_gets_a_distributed_lock_only_on_lambda(monkeypatch) -> None:
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    assert bedrock_module._default_distributed_lock() is None

    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "competition-trustforge-team11-live")
    lock = bedrock_module._default_distributed_lock()
    assert isinstance(lock, DynamoDBBedrockRpsLock)
    assert lock.min_interval == 1.0


# --- contract：不得偷加 IAM/資源 -------------------------------------------


def test_lock_needs_no_new_iam_action_or_table() -> None:
    contract = json.loads(
        Path("deploy/competition-lambda-live-contract.json").read_text(encoding="utf-8")
    )
    dynamo_statements = [
        s for s in contract["execution_role"]["statements"]
        if any(a.startswith("dynamodb:") for a in s["actions"])
    ]
    budget = next(
        s for s in dynamo_statements
        if any("team11-budget" in r for r in s["resources"])
    )
    assert sorted(budget["actions"]) == ["dynamodb:GetItem", "dynamodb:UpdateItem"]

    source = inspect.getsource(DynamoDBBedrockRpsLock)
    # 只用 update_item（含在既有 IAM 內），不得引入 put_item/query/scan/新表
    for forbidden in ("put_item", "query(", "scan(", "create_table", "TransactWrite"):
        assert forbidden not in source

    assert contract["reserved_concurrency"] == 1
    assert contract["daily_usd_cap"] == 10
