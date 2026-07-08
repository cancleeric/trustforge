"""`rate_limit_store`（issue #1 CISO High：Lambda 跨實例限流）單元測試。

不打真 DynamoDB：`_FakeDynamoDBTable` 用真的
`boto3.dynamodb.conditions.ConditionExpressionBuilder`（跟 boto3 Table
resource 實際送出請求前用的是同一顆 builder）把 `try_increment()` 送出的
`ConditionExpression` 物件轉成表達式字串/佔位符，再套用「本模組唯一會送出
的條件形狀」（`attribute_not_exists(request_count) OR request_count < :max`）
語意本身，不是憑空自己另訂一套規則。"""
from __future__ import annotations

import pytest

from trustforge import rate_limit_store


class _FakeDynamoDBTable:
    """極簡假 DynamoDB Table，只支援 `RateLimitStore.try_increment` 實際會
    送出的 `update_item` 呼叫形狀（ADD request_count + SET ttl
    if_not_exists，條件式 = not_exists(request_count) OR request_count <
    max）。"""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict] = {}
        self.call_count = 0

    def update_item(
        self,
        *,
        Key,
        UpdateExpression,
        ConditionExpression,
        ExpressionAttributeNames,
        ExpressionAttributeValues,
    ):
        self.call_count += 1
        from boto3.dynamodb.conditions import ConditionExpressionBuilder
        from botocore.exceptions import ClientError

        key_tuple = (Key["source_id"], Key["coin"])
        builder = ConditionExpressionBuilder()
        _expr, _cond_names, cond_values = builder.build_expression(
            ConditionExpression, is_key_condition=False
        )
        # 本模組唯一會送出的條件形狀只有一個數值佔位符（threshold）。
        threshold = list(cond_values.values())[0]

        existing = self._items.get(key_tuple)
        item_exists = existing is not None and "request_count" in existing
        current_count = existing.get("request_count", 0) if existing else 0

        condition_ok = (not item_exists) or (current_count < threshold)
        if not condition_ok:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "The conditional request failed",
                    }
                },
                "UpdateItem",
            )

        new_item = dict(existing) if existing else dict(Key)
        new_item["request_count"] = current_count + ExpressionAttributeValues[":incr"]
        if "ttl" not in new_item:
            new_item["ttl"] = ExpressionAttributeValues[":ttl_val"]
        self._items[key_tuple] = new_item
        return {}


class _AlwaysThrottledTable:
    """模擬 DynamoDB 真的連不上/被 throttle（非「條件不成立」）。"""

    def update_item(self, **kwargs):
        from botocore.exceptions import ClientError

        raise ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "boom"}},
            "UpdateItem",
        )


class _AlwaysBrokenTable:
    """模擬非 ClientError 的底層失敗（例如連線逾時的原始例外）。"""

    def update_item(self, **kwargs):
        raise TimeoutError("connect timeout")


def _make_store(table) -> rate_limit_store.RateLimitStore:
    store = rate_limit_store.RateLimitStore()
    store._table = table
    return store


def test_allows_up_to_max_then_blocks_without_further_incrementing():
    table = _FakeDynamoDBTable()
    store = _make_store(table)
    now = 1_000_000.0

    for _ in range(5):
        assert store.try_increment("live", "1.2.3.4", 60, 5, now=now) is True

    assert store.try_increment("live", "1.2.3.4", 60, 5, now=now) is False
    # 第 6 次被拒絕，不應該把計數器繼續往上加。
    key = ("__ratelimit_live__", f"1.2.3.4:{int(now // 60) * 60}")
    assert table._items[key]["request_count"] == 5


def test_window_rollover_resets_counter():
    table = _FakeDynamoDBTable()
    store = _make_store(table)
    window = 60

    for _ in range(5):
        assert store.try_increment("live", "9.9.9.9", window, 5, now=0.0) is True
    assert store.try_increment("live", "9.9.9.9", window, 5, now=0.0) is False

    # 下一個視窗（window_start 前進 60 秒）應該重新可用。
    assert store.try_increment("live", "9.9.9.9", window, 5, now=float(window)) is True


def test_shared_across_two_store_instances_same_backing_table():
    """模擬「多個 Lambda 執行環境」：兩個獨立 RateLimitStore 實例，但共用同一張
    （假）DynamoDB 表——這是本修復要證明的核心不變量：不會因為多實例部署，
    同一個 IP 的實際配額被放大。"""
    table = _FakeDynamoDBTable()
    store_a = _make_store(table)  # 模擬 Lambda 執行環境 A
    store_b = _make_store(table)  # 模擬 Lambda 執行環境 B（獨立 process）
    now = 500_000.0

    for _ in range(3):
        assert store_a.try_increment("live", "5.5.5.5", 60, 5, now=now) is True
    for _ in range(2):
        assert store_b.try_increment("live", "5.5.5.5", 60, 5, now=now) is True

    # A、B 加起來已經 5 次，第 6 次（不管從哪個實例來）都該被擋。
    assert store_a.try_increment("live", "5.5.5.5", 60, 5, now=now) is False
    assert store_b.try_increment("live", "5.5.5.5", 60, 5, now=now) is False


def test_different_keys_do_not_share_counter():
    table = _FakeDynamoDBTable()
    store = _make_store(table)
    now = 42.0

    for _ in range(5):
        assert store.try_increment("live", "1.1.1.1", 60, 5, now=now) is True
    # 不同 IP 是獨立計數器，不受剛剛那個 IP 用滿的影響。
    assert store.try_increment("live", "2.2.2.2", 60, 5, now=now) is True


def test_empty_key_uses_sentinel_not_empty_string():
    """DynamoDB SK 不可為空字串，key 為空時要落到 sentinel，不能直接把空字串
    當 SK 送出去（那會被 DynamoDB 拒絕）。"""
    table = _FakeDynamoDBTable()
    store = _make_store(table)
    now = 10.0

    assert store.try_increment("live", "", 60, 5, now=now) is True
    stored_keys = list(table._items.keys())
    assert len(stored_keys) == 1
    _source_id, coin = stored_keys[0]
    assert coin.startswith("unknown:")


def test_conditional_check_failure_returns_false_not_raise():
    table = _FakeDynamoDBTable()
    store = _make_store(table)
    now = 1.0
    for _ in range(2):
        store.try_increment("live", "1.2.3.4", 60, 2, now=now)
    # 不應該 raise，只是回 False。
    assert store.try_increment("live", "1.2.3.4", 60, 2, now=now) is False


def test_client_error_other_than_conditional_check_raises_backend_error():
    store = _make_store(_AlwaysThrottledTable())
    with pytest.raises(rate_limit_store.RateLimitBackendError):
        store.try_increment("live", "1.2.3.4", 60, 5, now=1.0)


def test_generic_exception_wrapped_as_backend_error():
    store = _make_store(_AlwaysBrokenTable())
    with pytest.raises(rate_limit_store.RateLimitBackendError):
        store.try_increment("live", "1.2.3.4", 60, 5, now=1.0)


def test_module_level_try_increment_uses_injected_store():
    table = _FakeDynamoDBTable()
    store = _make_store(table)
    assert rate_limit_store.try_increment(
        "live", "7.7.7.7", 60, 5, store=store, now=1.0
    ) is True
    assert table.call_count == 1


def test_module_level_try_increment_defaults_to_default_store(monkeypatch):
    table = _FakeDynamoDBTable()
    default_store = _make_store(table)
    monkeypatch.setattr(rate_limit_store, "_default_store_instance", default_store)

    assert rate_limit_store.try_increment("live", "8.8.8.8", 60, 5, now=1.0) is True
    assert table.call_count == 1
