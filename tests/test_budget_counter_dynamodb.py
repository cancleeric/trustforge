"""#75：多實例安全的 budget 預留原子計數器測試。

不打真 AWS：
- `test_budget_counter_dynamodb_moto.py` 風格的 moto smoke 驗證 boto3 呼叫語法
  （UpdateExpression / ConditionExpression / ExpressionAttributeValues）合法。
- `_FakeDynamoDBTable`（帶鎖）驗證「多實例共用同一張表」時，跨實例預留不會
  超支、並行原子性無 fail-open——moto 的 DynamoDB backend 沒有內部鎖，無法
  可靠驗證併發原子性（同 test_rate_limit_store.py 的取捨）。
- `budget_guard.try_reserve_request_budget` / `release_request_budget` 在啟用
  DynamoDB 後端時的行為：多實例不超支、後端不可用時 fallback 回 process-local。
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from trustforge import budget_counter
from trustforge.budget_counter import BudgetBackendError, DynamoDBBudgetCounter
from trustforge.budget_guard import (
    _reset_reservation_for_tests,
    release_request_budget,
    try_reserve_request_budget,
)


# ---------------------------------------------------------------------------
# 1) moto smoke：驗證送給 DynamoDB 的呼叫語法合法（不含併發斷言）
# ---------------------------------------------------------------------------

mock_aws = pytest.importorskip("moto").mock_aws

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

_TABLE = "trustforge-budget-guard"


@pytest.fixture
def ddb_table():
    with mock_aws():
        import boto3

        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName=_TABLE,
            AttributeDefinitions=[
                {"AttributeName": "source_id", "AttributeType": "S"},
                {"AttributeName": "coin", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "source_id", "KeyType": "HASH"},
                {"AttributeName": "coin", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield table


def test_moto_smoke_reserve_admit_then_deny(ddb_table):
    counter = DynamoDBBudgetCounter(table_name=_TABLE, region="us-east-1")
    # cap=1.0, spent=0, cost=0.1 → 最多 10 次成功
    for _ in range(10):
        assert counter.try_reserve(spent_daily=0.0, cost=0.1, cap=1.0, now=1000.0) is True
    assert counter.try_reserve(spent_daily=0.0, cost=0.1, cap=1.0, now=1000.0) is False
    # 釋放一筆後應能再預留
    counter.release(0.1, now=1000.0)
    assert counter.try_reserve(spent_daily=0.0, cost=0.1, cap=1.0, now=1000.0) is True


def test_moto_smoke_spent_reduces_room(ddb_table):
    counter = DynamoDBBudgetCounter(table_name=_TABLE, region="us-east-1")
    # 已花 0.7 + 單次 0.1 → 只剩 0.3 空間，最多 3 次
    for _ in range(3):
        assert counter.try_reserve(spent_daily=0.7, cost=0.1, cap=1.0, now=2000.0) is True
    assert counter.try_reserve(spent_daily=0.7, cost=0.1, cap=1.0, now=2000.0) is False


def test_moto_smoke_zero_cap_denies(ddb_table):
    counter = DynamoDBBudgetCounter(table_name=_TABLE, region="us-east-1")
    assert counter.try_reserve(spent_daily=0.0, cost=0.1, cap=0.0, now=1.0) is False


# ---------------------------------------------------------------------------
# 2) 帶鎖 fake table：確定性驗證跨實例不超支 + 並行原子性
# ---------------------------------------------------------------------------


class _FakeDynamoDBTable:
    """極簡假 DynamoDB Table，只支援 `DynamoDBBudgetCounter` 實際會送出的
    `update_item` / `get_item` 呼叫形狀，並用 `self._lock` 正確模擬真實
    DynamoDB 對單一 item 寫入原子序列化的保證。"""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict] = {}
        self.call_count = 0
        self._lock = threading.Lock()

    def _build(self, cond):
        from boto3.dynamodb.conditions import ConditionExpressionBuilder

        return ConditionExpressionBuilder().build_expression(cond, is_key_condition=False)

    def update_item(
        self,
        *,
        Key,
        UpdateExpression,
        ConditionExpression=None,
        ExpressionAttributeNames=None,
        ExpressionAttributeValues=None,
    ):
        self.call_count += 1
        ExpressionAttributeValues = ExpressionAttributeValues or {}
        ExpressionAttributeNames = ExpressionAttributeNames or {}
        values = {
            (k if k not in ExpressionAttributeNames else ExpressionAttributeNames[k]): v
            for k, v in ExpressionAttributeValues.items()
        }

        with self._lock:
            key_tuple = (Key["source_id"], Key["coin"])
            existing = self._items.get(key_tuple)
            reserved = float(existing.get("reserved_total", 0)) if existing else 0.0

            condition_ok = True
            if ConditionExpression is not None:
                _expr, _names, cond_values = self._build(ConditionExpression)
                cond_value_list = list(cond_values.values())
                if "<=" in _expr:
                    threshold = float(cond_value_list[0])
                    condition_ok = (existing is None) or ("reserved_total" not in existing) or (reserved <= threshold)
                elif ">=" in _expr:
                    amount = float(cond_value_list[0])
                    condition_ok = (existing is not None) and ("reserved_total" in existing) and (reserved >= amount)

            if not condition_ok:
                from botocore.exceptions import ClientError

                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "boom"}},
                    "UpdateItem",
                )

            new_item = dict(existing) if existing else dict(Key)
            if ":cost" in values:
                new_item["reserved_total"] = reserved + float(values[":cost"])
            elif ":neg_amount" in values:
                # ADD 語法傳負值＝代數相減（moto 不支援 SET 減法）
                new_item["reserved_total"] = reserved + float(values[":neg_amount"])
            else:
                # clamp 到 0
                new_item["reserved_total"] = 0.0
            self._items[key_tuple] = new_item
        return {}

    def get_item(self, *, Key):
        key_tuple = (Key["source_id"], Key["coin"])
        item = self._items.get(key_tuple)
        if item is None:
            return {}
        return {"Item": dict(item)}


class _AlwaysThrottledTable:
    def update_item(self, **kwargs):
        from botocore.exceptions import ClientError

        raise ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "boom"}},
            "UpdateItem",
        )

    def get_item(self, *, Key):
        return {}


def _counter_with_table(table) -> DynamoDBBudgetCounter:
    c = DynamoDBBudgetCounter()
    c._table = table
    return c


def test_shared_across_two_instances_no_overspend():
    """#75 核心不變量：兩個獨立 process（各自 `DynamoDBBudgetCounter` 實例）共用
    同一張表，預留總額跨實例不能超過 cap——修復「多實例各自 process-local 計數
    互不見、每日上限被並行撐爆」的 race。"""
    table = _FakeDynamoDBTable()
    counter_a = _counter_with_table(table)  # 實例 A（process 1）
    counter_b = _counter_with_table(table)  # 實例 B（process 2，獨立記憶體）

    cap, cost = 1.0, 0.1
    # A 先預留 6 次（占用 0.6），B 再預留 4 次（占用 0.4）→ 剛好達 cap
    for _ in range(6):
        assert counter_a.try_reserve(spent_daily=0.0, cost=cost, cap=cap, now=1.0) is True
    for _ in range(4):
        assert counter_b.try_reserve(spent_daily=0.0, cost=cost, cap=cap, now=1.0) is True

    # 第 11 次（不管從哪個實例來）都必須被擋，不能超過 cap。
    assert counter_a.try_reserve(spent_daily=0.0, cost=cost, cap=cap, now=1.0) is False
    assert counter_b.try_reserve(spent_daily=0.0, cost=cost, cap=cap, now=1.0) is False
    # 共享計數器總額恰好等於 cap（核心安全不變量）
    assert table._items[("__budget_reserved__", "1970-01-01")]["reserved_total"] == pytest.approx(1.0)


def test_concurrent_atomicity_no_fail_open():
    """50 個執行緒同時預留（cap=1.0, cost=0.1）：帶鎖 fake table 模擬 DynamoDB
    對單一 item 原子序列化，恰好 10 個成功、40 個被拒，無 fail-open。"""
    table = _FakeDynamoDBTable()
    counter = _counter_with_table(table)
    n_workers = 50
    barrier = threading.Barrier(n_workers)

    def _call() -> bool:
        barrier.wait()
        return counter.try_reserve(spent_daily=0.0, cost=0.1, cap=1.0, now=10.0)

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        results = [f.result() for f in [executor.submit(_call) for _ in range(n_workers)]]

    admitted = [r for r in results if r is True]
    denied = [r for r in results if r is False]
    assert len(admitted) == 10
    assert len(denied) == 40


def test_release_frees_capacity_for_next_request():
    table = _FakeDynamoDBTable()
    counter = _counter_with_table(table)
    assert counter.try_reserve(spent_daily=0.0, cost=0.5, cap=0.5, now=1.0) is True
    assert counter.try_reserve(spent_daily=0.0, cost=0.5, cap=0.5, now=1.0) is False
    counter.release(0.5, now=1.0)
    assert counter.try_reserve(spent_daily=0.0, cost=0.5, cap=0.5, now=1.0) is True
    assert counter.current_reserved(now=1.0) == pytest.approx(0.5)


def test_release_clamps_when_partial_negative():
    """釋放金額大於已預留（異常/重複 release）→ clamp 回 0，不留負值污染下一輪。"""
    table = _FakeDynamoDBTable()
    counter = _counter_with_table(table)
    counter.try_reserve(spent_daily=0.0, cost=0.3, cap=1.0, now=1.0)
    counter.release(0.5, now=1.0)  # 多釋放
    assert counter.current_reserved(now=1.0) == pytest.approx(0.0)
    # 負值被清掉後，下一輪能正常重新預留到 cap
    for _ in range(10):
        assert counter.try_reserve(spent_daily=0.0, cost=0.1, cap=1.0, now=1.0) is True


def test_backend_error_wrapped():
    counter = _counter_with_table(_AlwaysThrottledTable())
    with pytest.raises(BudgetBackendError):
        counter.try_reserve(spent_daily=0.0, cost=0.1, cap=1.0, now=1.0)


# ---------------------------------------------------------------------------
# 3) budget_guard 整合：啟用 DynamoDB 後端的行為
# ---------------------------------------------------------------------------


@pytest.fixture
def _budget_ddb_env(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BUDGET_GUARD_BACKEND", "dynamodb")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "1.0")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.1")
    _reset_reservation_for_tests()
    yield
    _reset_reservation_for_tests()
    budget_counter.set_default_counter_for_tests(None)


def test_budget_guard_ddb_multi_instance_no_overspend(monkeypatch, _budget_ddb_env):
    """多實例（各自 `try_reserve_request_budget` 但共用同一張 fake 表）並行預留，
    全程不得超過 cap。這是 #75 要解決的真實場景。"""
    table = _FakeDynamoDBTable()

    def _make_counter():
        c = DynamoDBBudgetCounter()
        c._table = table
        return c

    # 每次 `_budget_counter_backend()` 都回新的 counter 實例（模擬不同 process
    # 各自建自己的 client），但全部指向同一張共享表。
    call_idx = {"n": 0}

    def _fake_default_counter():
        call_idx["n"] += 1
        return _make_counter()

    monkeypatch.setattr(budget_counter, "_default_counter", _fake_default_counter)

    n_threads = 40
    barrier = threading.Barrier(n_threads)
    results: list[float | None] = [None] * n_threads

    def _worker(idx: int) -> None:
        barrier.wait()
        results[idx] = try_reserve_request_budget()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    admitted = [r for r in results if r is not None]
    denied = [r for r in results if r is None]
    # 跨實例共享計數器：恰好 10 個成功（10 * 0.1 = cap）
    assert len(admitted) == 10, f"admitted={len(admitted)}"
    assert len(denied) == n_threads - 10
    # 共享 item 只有一個日期 key（今天），其 reserved_total 恰好等於 cap
    reserved_items = list(table._items.values())
    assert len(reserved_items) == 1
    assert reserved_items[0]["reserved_total"] == pytest.approx(1.0)


def test_budget_guard_ddb_backend_error_falls_back_to_local(monkeypatch, _budget_ddb_env):
    """後端不可用（BudgetBackendError）時，fallback 回 process-local 預留，
    不讓預留整個 fail-open。"""
    broken = DynamoDBBudgetCounter()
    broken._table = _AlwaysThrottledTable()

    def _fake_default_counter():
        return broken

    monkeypatch.setattr(budget_counter, "_default_counter", _fake_default_counter)

    cost = try_reserve_request_budget()
    # fallback 到 process-local：cap=1.0, cost=0.1 → 第一筆成功
    assert cost is not None
    assert cost == pytest.approx(0.1)
    # 釋放不會炸
    release_request_budget(cost)


def test_budget_guard_ddb_release_frees_shared_counter(monkeypatch, _budget_ddb_env):
    table = _FakeDynamoDBTable()
    c = DynamoDBBudgetCounter()
    c._table = table

    def _fake_default_counter():
        return c

    monkeypatch.setattr(budget_counter, "_default_counter", _fake_default_counter)

    cost = try_reserve_request_budget()
    assert cost is not None
    assert c.current_reserved() == pytest.approx(0.1)
    release_request_budget(cost)
    assert c.current_reserved() == pytest.approx(0.0)
