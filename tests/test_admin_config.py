"""admin console PR-1：`admin_config.py` 設定儲存層測試。

⛔ 全程不打真 AWS：`AdminConfigStore` 一律注入 mock `_table`（比照
`test_connector_cache.py` 的 `DynamoDBCache` mock 慣例，不引入 moto 或任何
新測試依賴）。驗收項（計劃 §9-1/§9-5/§9-6 儲存層子集）：

  1. item 不存在 → 空 config（全 None，舊部署相容）。
  2. 壞欄（NaN/Inf/非法字串/錯型別）→ 逐欄 fallback（None），不炸、
     不拖垮其他好欄。
  3. 讀取失敗（網路/憑證）→ raise `AdminConfigReadError`。
  4. CAS 寫入：ConditionExpression 帶 expected_version；衝突 → 專屬
     `VersionConflictError`。
  5. 審計：成功 PUT 產生 `__admin_audit__` item；token 欄位 old/new 全程
     遮罩，明文/hash 絕不出現在審計 item。
  6. 審計寫失敗不鎖死 put（設定成功 + `audit_warning`）。
  7. process 內 TTL 快取：命中不重打 DynamoDB、過期重讀、put 後
     write-through。
  8. 金額字串 roundtrip：寫 1.0 → 存 "1.0" → 讀回 float 1.0。
  9. `verify_live_token`：正確明文 ↔ hash 通過、錯誤/未設定 fail-closed。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from trustforge.admin_config import (
    ADMIN_AUDIT_SOURCE,
    ADMIN_CONFIG_COIN,
    ADMIN_CONFIG_SOURCE,
    CACHE_TTL_SECONDS,
    AdminConfig,
    AdminConfigReadError,
    AdminConfigStore,
    AdminConfigWriteError,
    VersionConflictError,
    get_config,
    get_config_cached,
    hash_live_token,
    list_audit,
    put_config,
    verify_live_token,
)


def _store_with_mock_table() -> tuple[AdminConfigStore, MagicMock]:
    store = AdminConfigStore()
    mock_table = MagicMock()
    store._table = mock_table  # 繞過 boto3，模擬已建好的 Table，確保不打真 AWS
    return store, mock_table


def _conditional_check_failed() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "conflict"}},
        "PutItem",
    )


GOOD_ITEM = {
    "source_id": ADMIN_CONFIG_SOURCE,
    "coin": ADMIN_CONFIG_COIN,
    "version": Decimal("7"),  # DynamoDB N 型別回 Decimal
    "daily_cap_usd": "1.0",  # 金額字串存（計劃 §1.3）
    "bedrock_enabled": True,
    "live_token_hash": hashlib.sha256(b"x" * 32).hexdigest(),
    "live_token_last4": "xxxx",
    "updated_at": "2026-07-07T03:00:00+00:00",
    "updated_by": "admin@203.0.113.5",
}


# ---------------------------------------------------------------------------
# 建構 / 讀取
# ---------------------------------------------------------------------------
def test_store_construction_does_not_touch_aws():
    store = AdminConfigStore()
    assert store._table is None  # 尚未真的碰 AWS SDK（比照 DynamoDBCache 慣例）


def test_get_config_missing_item_returns_empty_config():
    """item 不存在（舊部署/全新環境）→ 全 None 空 config，不 raise。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {}  # 無 "Item" key（DynamoDB 未命中標準回應）

    config = get_config(store)

    assert config == AdminConfig()
    assert config.daily_cap_usd is None
    assert config.bedrock_enabled is None
    assert config.live_token_hash is None
    assert config.version is None
    mock_table.get_item.assert_called_once_with(
        Key={"source_id": ADMIN_CONFIG_SOURCE, "coin": ADMIN_CONFIG_COIN},
        ConsistentRead=False,
    )


def test_get_config_parses_good_item():
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {"Item": dict(GOOD_ITEM)}

    config = get_config(store, consistent=True)

    assert config.daily_cap_usd == 1.0  # 字串 "1.0" → float roundtrip
    assert config.bedrock_enabled is True
    assert config.live_token_hash == GOOD_ITEM["live_token_hash"]
    assert config.live_token_last4 == "xxxx"
    assert config.version == 7
    assert config.updated_by == "admin@203.0.113.5"
    assert mock_table.get_item.call_args.kwargs["ConsistentRead"] is True


@pytest.mark.parametrize(
    "bad_cap", ["NaN", "inf", "-inf", "abc", "", True, {"n": 1}, ["1.0"]]
)
def test_get_config_bad_cap_falls_back_to_none_without_breaking_others(bad_cap):
    """壞 cap 欄 → 該欄 None；其他好欄（version/bedrock_enabled）不受影響。"""
    store, mock_table = _store_with_mock_table()
    item = dict(GOOD_ITEM)
    item["daily_cap_usd"] = bad_cap
    mock_table.get_item.return_value = {"Item": item}

    config = get_config(store)

    assert config.daily_cap_usd is None  # 壞欄逐欄 fallback
    assert config.bedrock_enabled is True  # 不拖垮整包
    assert config.version == 7


@pytest.mark.parametrize("bad_enabled", ["true", 1, Decimal("1"), "yes"])
def test_get_config_non_bool_bedrock_enabled_falls_back(bad_enabled):
    """`bedrock_enabled` 嚴格 bool——字串/數字都視為未設定（讓 PR-3 落
    fail-closed 預設），不得被 truthy 值靜默放行。"""
    store, mock_table = _store_with_mock_table()
    item = dict(GOOD_ITEM)
    item["bedrock_enabled"] = bad_enabled
    mock_table.get_item.return_value = {"Item": item}

    config = get_config(store)

    assert config.bedrock_enabled is None
    assert config.daily_cap_usd == 1.0  # 其他欄不受影響


def test_get_config_bad_version_falls_back_to_none():
    store, mock_table = _store_with_mock_table()
    item = dict(GOOD_ITEM)
    item["version"] = "garbage"
    mock_table.get_item.return_value = {"Item": item}

    config = get_config(store)

    assert config.version is None
    assert config.daily_cap_usd == 1.0


def test_get_config_read_failure_raises_dedicated_error():
    """讀取失敗（網路/憑證/throttle）≠「沒資料」：必須 raise 讓呼叫端
    fail-safe，不得靜默回空 config（否則 PR-3 會把 outage 當『未設定』）。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "throttle"}},
        "GetItem",
    )

    with pytest.raises(AdminConfigReadError):
        get_config(store)


# ---------------------------------------------------------------------------
# 寫入（CAS）
# ---------------------------------------------------------------------------
def test_put_config_writes_cas_item_with_incremented_version():
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {"Item": dict(GOOD_ITEM)}

    result = put_config(
        {"daily_cap_usd": 2.5}, expected_version=7, actor="admin@1.2.3.4", store=store
    )

    # 第一個 put_item = config item（帶 CAS 條件）
    config_call = mock_table.put_item.call_args_list[0]
    item = config_call.kwargs["Item"]
    assert item["source_id"] == ADMIN_CONFIG_SOURCE
    assert item["coin"] == ADMIN_CONFIG_COIN
    assert item["version"] == 8  # expected+1
    assert item["daily_cap_usd"] == "2.5"  # 金額字串存
    assert "ttl" not in item  # 表級 TTL 不得回收 config item（計劃 §1.3）
    assert "ConditionExpression" in config_call.kwargs  # CAS 條件必在
    # 未變更欄位沿用當前值（部分更新不清掉其他欄）
    assert item["bedrock_enabled"] is True
    assert item["live_token_hash"] == GOOD_ITEM["live_token_hash"]

    assert result.config.daily_cap_usd == 2.5
    assert result.config.version == 8
    assert result.audit_warning is None


def test_put_config_first_write_on_empty_store():
    """item 不存在（全新環境）：expected_version=0 → version 1。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {}

    result = put_config(
        {"bedrock_enabled": False}, expected_version=0, actor="admin@1.2.3.4", store=store
    )

    item = mock_table.put_item.call_args_list[0].kwargs["Item"]
    assert item["version"] == 1
    assert item["bedrock_enabled"] is False
    assert "daily_cap_usd" not in item  # 未設定欄位不寫入
    assert result.config.version == 1


def test_put_config_cas_conflict_raises_dedicated_error():
    """CAS 衝突（別人先改了）→ 專屬例外（PR-2 據此回 409），不是一般錯誤。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {"Item": dict(GOOD_ITEM)}
    mock_table.put_item.side_effect = _conditional_check_failed()

    with pytest.raises(VersionConflictError) as excinfo:
        put_config({"daily_cap_usd": 2.0}, expected_version=6, actor="a@b", store=store)
    assert excinfo.value.expected_version == 6


def test_put_config_non_cas_write_failure_raises_write_error():
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {"Item": dict(GOOD_ITEM)}
    mock_table.put_item.side_effect = ClientError(
        {"Error": {"Code": "InternalServerError", "Message": "boom"}}, "PutItem"
    )

    with pytest.raises(AdminConfigWriteError):
        put_config({"daily_cap_usd": 2.0}, expected_version=7, actor="a@b", store=store)


@pytest.mark.parametrize(
    "changes",
    [
        {},  # 空 changes
        {"unknown_field": 1},  # 不支援欄位
        {"daily_cap_usd": float("nan")},  # 非有限數
        {"daily_cap_usd": float("inf")},
        {"daily_cap_usd": "1.0"},  # 儲存層收 float，不收字串（字串化是落庫格式）
        {"daily_cap_usd": True},  # bool 不是數字
        {"bedrock_enabled": "true"},  # 嚴格 bool
        {"live_token": ""},  # 空 token（清除要用 None）
        {"live_token": 123},
    ],
)
def test_put_config_rejects_invalid_changes(changes):
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {"Item": dict(GOOD_ITEM)}

    with pytest.raises(ValueError):
        put_config(changes, expected_version=7, actor="a@b", store=store)
    mock_table.put_item.assert_not_called()  # 驗證失敗不得碰庫


def test_put_config_clears_field_with_none():
    """值 None＝清除該欄（回落 env/DEFAULT 層）：item 不再帶該屬性。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {"Item": dict(GOOD_ITEM)}

    result = put_config(
        {"daily_cap_usd": None, "live_token": None},
        expected_version=7,
        actor="a@b",
        store=store,
    )

    item = mock_table.put_item.call_args_list[0].kwargs["Item"]
    assert "daily_cap_usd" not in item
    assert "live_token_hash" not in item
    assert "live_token_last4" not in item
    assert item["bedrock_enabled"] is True  # 未動的欄位保留
    assert result.config.daily_cap_usd is None
    assert result.config.live_token_hash is None


def test_put_config_cap_string_roundtrip():
    """金額 roundtrip：寫 float 1.0 → 落庫字串 "1.0" → 讀回 float 1.0。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {}

    put_config({"daily_cap_usd": 1.0}, expected_version=0, actor="a@b", store=store)
    written_item = mock_table.put_item.call_args_list[0].kwargs["Item"]
    assert written_item["daily_cap_usd"] == "1.0"
    assert isinstance(written_item["daily_cap_usd"], str)

    # 把剛寫入的 item 原樣餵回讀取端 → 同一個 float
    store2, mock_table2 = _store_with_mock_table()
    mock_table2.get_item.return_value = {"Item": written_item}
    assert get_config(store2).daily_cap_usd == 1.0


# ---------------------------------------------------------------------------
# live token：只存 hash，明文絕不落庫
# ---------------------------------------------------------------------------
def test_put_config_stores_token_hash_never_plaintext():
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {}
    plaintext = "s3cr3t-live-token-abcdef0123456789"

    result = put_config(
        {"live_token": plaintext}, expected_version=0, actor="a@b", store=store
    )

    config_item = mock_table.put_item.call_args_list[0].kwargs["Item"]
    assert config_item["live_token_hash"] == hashlib.sha256(plaintext.encode()).hexdigest()
    assert config_item["live_token_last4"] == "6789"
    # 明文不得出現在 config item 任何值裡
    assert plaintext not in json.dumps(
        {k: str(v) for k, v in config_item.items()}, ensure_ascii=False
    )
    assert result.config.live_token_hash == config_item["live_token_hash"]


def test_verify_live_token():
    plaintext = "another-live-token-0123456789abcdef"
    stored = hash_live_token(plaintext)
    assert verify_live_token(plaintext, stored) is True
    assert verify_live_token("wrong-token", stored) is False
    assert verify_live_token("", stored) is False  # fail-closed
    assert verify_live_token(None, stored) is False
    assert verify_live_token(plaintext, None) is False
    assert verify_live_token(None, None) is False


# ---------------------------------------------------------------------------
# 審計
# ---------------------------------------------------------------------------
def test_put_config_writes_audit_item_with_masked_token():
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {"Item": dict(GOOD_ITEM)}
    new_plaintext = "rotated-live-token-9876543210fedcba"

    put_config(
        {"daily_cap_usd": 2.0, "live_token": new_plaintext},
        expected_version=7,
        actor="admin@203.0.113.5",
        store=store,
        user_agent="Mozilla/5.0",
    )

    assert mock_table.put_item.call_count == 2  # config + audit
    audit_item = mock_table.put_item.call_args_list[1].kwargs["Item"]
    assert audit_item["source_id"] == ADMIN_AUDIT_SOURCE
    # SK = "{ISO ts}#{uuid8}"（字典序＝時間序）
    ts_part, _, uuid_part = audit_item["coin"].partition("#")
    assert ts_part == audit_item["ts"]
    assert len(uuid_part) == 8
    assert audit_item["actor"] == "admin@203.0.113.5"
    assert audit_item["version_from"] == 7
    assert audit_item["version_to"] == 8
    assert audit_item["user_agent"] == "Mozilla/5.0"
    assert "ttl" not in audit_item  # 審計不得被表級 TTL 回收

    changes = json.loads(audit_item["changes_json"])
    by_field = {c["field"]: c for c in changes}
    assert by_field["daily_cap_usd"] == {"field": "daily_cap_usd", "old": 1.0, "new": 2.0}
    # token 遮罩：舊值已設 → "<set>"，新值 → "<rotated last4=...>"
    assert by_field["live_token"]["old"] == "<set>"
    assert by_field["live_token"]["new"] == "<rotated last4=dcba>"
    # 明文與 hash 都絕不出現在審計 item
    serialized = json.dumps({k: str(v) for k, v in audit_item.items()}, ensure_ascii=False)
    assert new_plaintext not in serialized
    assert hash_live_token(new_plaintext) not in serialized
    assert GOOD_ITEM["live_token_hash"] not in serialized


def test_put_config_audit_token_set_and_cleared_masks():
    """token 首設 → old "<unset>" / new "<set last4=...>"；清除 → "<cleared>"。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {}  # 尚無 config → 無舊 token
    plaintext = "first-ever-live-token-abcdefgh1234"

    put_config({"live_token": plaintext}, expected_version=0, actor="a@b", store=store)
    changes = json.loads(mock_table.put_item.call_args_list[1].kwargs["Item"]["changes_json"])
    assert changes == [{"field": "live_token", "old": "<unset>", "new": "<set last4=1234>"}]

    # 清除：舊 token 已設
    store2, mock_table2 = _store_with_mock_table()
    mock_table2.get_item.return_value = {"Item": dict(GOOD_ITEM)}
    put_config({"live_token": None}, expected_version=7, actor="a@b", store=store2)
    changes2 = json.loads(
        mock_table2.put_item.call_args_list[1].kwargs["Item"]["changes_json"]
    )
    assert changes2 == [{"field": "live_token", "old": "<set>", "new": "<cleared>"}]


def test_put_config_audit_failure_does_not_block_put(caplog):
    """審計側路故障不鎖死管理操作：設定寫入成功 + `audit_warning` 帶出 +
    ERROR log 留痕（計劃 §6-2）。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {"Item": dict(GOOD_ITEM)}
    # 第一次 put_item（config）成功，第二次（audit）炸
    mock_table.put_item.side_effect = [
        None,
        ClientError({"Error": {"Code": "InternalServerError", "Message": "boom"}}, "PutItem"),
    ]

    with caplog.at_level("ERROR", logger="trustforge.admin_config"):
        result = put_config(
            {"daily_cap_usd": 2.0}, expected_version=7, actor="a@b", store=store
        )

    assert result.config.daily_cap_usd == 2.0  # 設定本身成功
    assert result.audit_warning is not None
    assert any("審計" in rec.message for rec in caplog.records)


def test_list_audit_queries_pk_descending():
    store, mock_table = _store_with_mock_table()
    mock_table.query.return_value = {
        "Items": [
            {
                "source_id": ADMIN_AUDIT_SOURCE,
                "coin": "2026-07-07T04:00:00+00:00#deadbeef",
                "ts": "2026-07-07T04:00:00+00:00",
                "actor": "admin@1.2.3.4",
                "changes_json": '[{"field": "daily_cap_usd", "old": 3.0, "new": 1.0}]',
                "version_from": Decimal("1"),
                "version_to": Decimal("2"),
            },
            {
                "source_id": ADMIN_AUDIT_SOURCE,
                "coin": "2026-07-07T03:00:00+00:00#cafebabe",
                "ts": "2026-07-07T03:00:00+00:00",
                "actor": "admin@1.2.3.4",
                "changes_json": "not json",  # 壞資料容錯 → changes []
                "version_from": Decimal("0"),
                "version_to": Decimal("1"),
            },
        ]
    }

    records = list_audit(limit=10, store=store)

    kwargs = mock_table.query.call_args.kwargs
    assert kwargs["ScanIndexForward"] is False  # SK 降序＝時間降序
    assert kwargs["Limit"] == 10
    assert len(records) == 2
    assert records[0]["changes"] == [{"field": "daily_cap_usd", "old": 3.0, "new": 1.0}]
    assert records[0]["version_from"] == 1
    assert records[0]["version_to"] == 2
    assert records[1]["changes"] == []  # 壞 changes_json 不炸


def test_list_audit_read_failure_raises():
    store, mock_table = _store_with_mock_table()
    mock_table.query.side_effect = ClientError(
        {"Error": {"Code": "InternalServerError", "Message": "boom"}}, "Query"
    )
    with pytest.raises(AdminConfigReadError):
        list_audit(store=store)


def test_list_audit_nonpositive_limit_returns_empty_without_query():
    store, mock_table = _store_with_mock_table()
    assert list_audit(limit=0, store=store) == []
    mock_table.query.assert_not_called()


# ---------------------------------------------------------------------------
# process 內 TTL 快取
# ---------------------------------------------------------------------------
def test_get_config_cached_hits_within_ttl_and_expires_after():
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {"Item": dict(GOOD_ITEM)}
    fake_now = [1000.0]

    def now_fn():
        return fake_now[0]

    c1 = get_config_cached(store, now_fn=now_fn)
    c2 = get_config_cached(store, now_fn=now_fn)  # TTL 內：命中，不重打
    assert c1 == c2
    assert mock_table.get_item.call_count == 1

    fake_now[0] += CACHE_TTL_SECONDS + 0.1  # 過期
    get_config_cached(store, now_fn=now_fn)
    assert mock_table.get_item.call_count == 2  # 過期後重讀


def test_put_config_write_through_updates_cache():
    """put 成功後 write-through：本 process 下一次 cached 讀直接看到新值，
    不需再打 DynamoDB。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {"Item": dict(GOOD_ITEM)}
    fake_now = [1000.0]

    def now_fn():
        return fake_now[0]

    old = get_config_cached(store, now_fn=now_fn)
    assert old.daily_cap_usd == 1.0
    reads_before = mock_table.get_item.call_count

    result = put_config(
        {"daily_cap_usd": 2.0},
        expected_version=7,
        actor="a@b",
        store=store,
        now_fn=now_fn,
    )
    # put 內部有一次 ConsistentRead（審計 old 值），之後 cached 讀不再打庫
    reads_after_put = mock_table.get_item.call_count

    cached = get_config_cached(store, now_fn=now_fn)
    assert cached.daily_cap_usd == 2.0  # 立即看到新值（write-through）
    assert cached == result.config
    assert mock_table.get_item.call_count == reads_after_put  # 沒有額外庫讀
    assert reads_after_put == reads_before + 1  # put 只多了那一次 ConsistentRead


def test_cached_read_failure_raises_and_does_not_cache():
    """讀失敗 raise（不做負面快取）：下一次呼叫會再試（可能已恢復）。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.side_effect = [
        ClientError({"Error": {"Code": "InternalServerError", "Message": "x"}}, "GetItem"),
        {"Item": dict(GOOD_ITEM)},
    ]
    fake_now = [1000.0]

    def now_fn():
        return fake_now[0]

    with pytest.raises(AdminConfigReadError):
        get_config_cached(store, now_fn=now_fn)
    # 失敗不進快取 → 同一時刻重試會真的再打一次庫並成功
    config = get_config_cached(store, now_fn=now_fn)
    assert config.daily_cap_usd == 1.0
    assert mock_table.get_item.call_count == 2
