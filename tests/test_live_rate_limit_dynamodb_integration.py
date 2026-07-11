"""issue #1（CISO High）整合測試：`web._check_live_rate_limit` 與
`rate_limit_store` 的接線是否正確——DynamoDB 可用時真的走共享計數器（不動到
process-local `_rate_buckets`），DynamoDB 不可用時正確 fallback 回
process-local 滑動視窗（`tests/conftest.py` 的 `_isolate_rate_limit_store`
autouse fixture 預設就是模擬「不可用」，全套件其他測試都在間接驗證這條
fallback 路徑；本檔案額外把它的行為明確斷言出來）。"""
from __future__ import annotations

import pytest

from trustforge import rate_limit_store
from trustforge import web

from tests.test_rate_limit_store import _FakeDynamoDBTable, _make_store


def test_check_live_rate_limit_uses_dynamodb_when_available(monkeypatch):
    """DynamoDB 共享計數器可用時：限流由 DynamoDB 側裁決，process-local
    `_rate_buckets` 完全不被觸碰（證明真的走了新路徑，不是巧合地跟 fallback
    結果一樣）。"""
    table = _FakeDynamoDBTable()
    healthy_store = _make_store(table)
    monkeypatch.setattr(rate_limit_store, "_default_store_instance", healthy_store)
    web._rate_buckets.clear()

    ip = "203.0.113.5"
    for _ in range(web._RATE_MAX):
        web._check_live_rate_limit(ip)

    with pytest.raises(web.TooManyRequests):
        web._check_live_rate_limit(ip)

    assert web._rate_buckets == {}, "DynamoDB 路徑不應該碰 process-local bucket"
    assert table.call_count == web._RATE_MAX + 1


def test_check_live_rate_limit_shared_across_simulated_lambda_instances(monkeypatch):
    """核心不變量：兩個各自獨立的 `RateLimitStore`（模擬兩個 Lambda 執行環境）
    共用同一張（假）DynamoDB 表時，`web._check_live_rate_limit` 在其中一個
    「實例」用掉的額度，另一個「實例」也看得到——不會因為多實例部署被放大。"""
    table = _FakeDynamoDBTable()
    instance_a_store = _make_store(table)
    instance_b_store = _make_store(table)
    web._rate_buckets.clear()
    ip = "203.0.113.9"

    monkeypatch.setattr(rate_limit_store, "_default_store_instance", instance_a_store)
    for _ in range(web._RATE_MAX - 1):
        web._check_live_rate_limit(ip)

    # 切到「另一個 Lambda 實例」（獨立 store，但共用同一張底層表）。
    monkeypatch.setattr(rate_limit_store, "_default_store_instance", instance_b_store)
    web._check_live_rate_limit(ip)  # 用掉最後一格額度
    with pytest.raises(web.TooManyRequests):
        web._check_live_rate_limit(ip)  # 兩實例合計已達上限

    assert web._rate_buckets == {}


def test_check_live_rate_limit_falls_back_to_local_bucket_on_backend_error(monkeypatch):
    """DynamoDB 不可用（conftest 預設模擬）時，`_check_live_rate_limit` 必須
    fallback 回 process-local `_rate_buckets`，而不是靜默放行（fail-open）。"""
    web._rate_buckets.clear()
    ip = "203.0.113.7"

    web._check_live_rate_limit(ip)

    assert ip in web._rate_buckets, "backend 不可用時應該 fallback 記進 process-local bucket"
    assert len(web._rate_buckets[ip]) == 1


def test_check_live_rate_limit_local_fallback_still_enforces_max(monkeypatch):
    """backend 不可用時，fallback 邏輯本身仍要正確擋到 `_RATE_MAX`（不是
    fallback 就等於不限流）。"""
    web._rate_buckets.clear()
    ip = "203.0.113.11"

    for _ in range(web._RATE_MAX):
        web._check_live_rate_limit(ip)
    with pytest.raises(web.TooManyRequests):
        web._check_live_rate_limit(ip)
