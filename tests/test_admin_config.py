"""admin console PR-1：`admin_config.py` 設定儲存層測試。

⛔ 全程不打真 AWS：`AdminConfigStore` 一律注入 mock `_table`（比照
`test_connector_cache.py` 的 `DynamoDBCache` mock 慣例，不引入 moto 或任何
新測試依賴）。驗收項（計劃 §9-1/§9-5/§9-6 儲存層子集）：

  1. item 不存在 → 空 config（全 None，舊部署相容）。
  2. 壞欄（NaN/Inf/非法字串/錯型別）→ 逐欄 fallback（None），不炸、
     不拖垮其他好欄。
  3. 讀取失敗（網路/憑證）→ raise `AdminConfigReadError`。
  4. CAS 寫入：ConditionExpression **結構**比對（不只斷言 key 存在）；
     衝突 → 專屬 `VersionConflictError`；`expected_version` 型別/負值驗證。
  5. 審計：成功 PUT 產生 `__admin_audit__` item；token 欄位 old/new 全程
     遮罩，明文/hash 絕不出現在審計 item；短 token（< 16 字）不落 last4。
  6. 審計寫失敗不鎖死 put（設定成功 + `audit_warning`）。
  7. process 內 TTL 快取：命中不重打 DynamoDB、過期重讀、put 後
     write-through、cache-miss 鎖外讀不 clobber 併發 write-through
     （generation 計數）。
  8. 金額字串 roundtrip：寫 1.0 → 存 "1.0" → 讀回 float 1.0。
  9. `verify_live_token`：正確明文 ↔ hash 通過、錯誤/未設定/非 str
     fail-closed。
  10. `version` 欄位損毀（非 int）→ `AdminConfig.exists=True,
      version_corrupt=True`，`put_config()` 提早 raise
      `VersionCorruptError`；`consistent=True` 的 `ConsistentRead` 失敗
      正常傳播。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from boto3.dynamodb.conditions import Attr
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
    VersionCorruptError,
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
    assert config.exists is False
    assert config.version_corrupt is False
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
    assert config.exists is True
    assert config.version_corrupt is False
    assert mock_table.get_item.call_args.kwargs["ConsistentRead"] is True
    # to_public_dict()：PR-2 API 序列化用，絕不含 live_token_hash
    public = config.to_public_dict()
    assert "live_token_hash" not in public
    assert public["live_token_configured"] is True
    assert public["live_token_last4"] == "xxxx"


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
    """version 欄位損毀（非 int）：`version=None`，但**跟「item 不存在」
    不可區分**這個坑用 `exists`/`version_corrupt` 兩個訊號補（vp-eng review
    MEDIUM-3）——item 明明存在（`exists=True`），只是 version 損毀
    （`version_corrupt=True`）。"""
    store, mock_table = _store_with_mock_table()
    item = dict(GOOD_ITEM)
    item["version"] = "garbage"
    mock_table.get_item.return_value = {"Item": item}

    config = get_config(store)

    assert config.version is None
    assert config.daily_cap_usd == 1.0
    assert config.exists is True
    assert config.version_corrupt is True


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
    # CAS 條件**結構**比對（vp-eng review MEDIUM-4）：只斷言 key 存在
    # 擋不住「條件寫錯但形狀還在」的假覆蓋——expected_version=7（>0）必須
    # **只用** `version = :7`，不得用 OR 合併（見 MEDIUM-3/LOW-6）。
    assert config_call.kwargs["ConditionExpression"] == Attr("version").eq(7)
    # 未變更欄位沿用當前值（部分更新不清掉其他欄）
    assert item["bedrock_enabled"] is True
    assert item["live_token_hash"] == GOOD_ITEM["live_token_hash"]

    assert result.config.daily_cap_usd == 2.5
    assert result.config.version == 8
    assert result.audit_warning is None


def test_put_config_first_write_on_empty_store():
    """item 不存在（全新環境）：expected_version=0 → version 1，CAS 條件
    **只用** `attribute_not_exists(version)`（不得用 OR 合併——vp-eng
    review MEDIUM-3/LOW-6：對空表傳任意正數 version 不該被 not_exists()
    分支誤放行，所以 ==0 這支必須是純 not_exists，不含 eq 分支）。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {}

    result = put_config(
        {"bedrock_enabled": False}, expected_version=0, actor="admin@1.2.3.4", store=store
    )

    config_call = mock_table.put_item.call_args_list[0]
    item = config_call.kwargs["Item"]
    assert item["version"] == 1
    assert item["bedrock_enabled"] is False
    assert "daily_cap_usd" not in item  # 未設定欄位不寫入
    assert result.config.version == 1
    assert config_call.kwargs["ConditionExpression"] == Attr("version").not_exists()


def test_put_config_cas_conflict_raises_dedicated_error():
    """CAS 衝突（別人先改了）→ 專屬例外（PR-2 據此回 409），不是一般錯誤。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {"Item": dict(GOOD_ITEM)}
    mock_table.put_item.side_effect = _conditional_check_failed()

    with pytest.raises(VersionConflictError) as excinfo:
        put_config({"daily_cap_usd": 2.0}, expected_version=6, actor="a@b", store=store)
    assert excinfo.value.expected_version == 6


@pytest.mark.parametrize("bad_expected_version", [-1, 1.5, "7", True])
def test_put_config_rejects_invalid_expected_version(bad_expected_version):
    """`expected_version` 必須是非負 int（`bool` 是 int 子類但明確拒絕，
    避免 `True`/`False` 被誤當 1/0）。驗證失敗不得碰庫。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {"Item": dict(GOOD_ITEM)}

    with pytest.raises(ValueError):
        put_config(
            {"daily_cap_usd": 2.0},
            expected_version=bad_expected_version,
            actor="a@b",
            store=store,
        )
    mock_table.put_item.assert_not_called()


def test_put_config_version_corrupt_raises_dedicated_error_before_write():
    """item 存在但 `version` 損毀 → 提早 raise `VersionCorruptError`
    （vp-eng review MEDIUM-3），**不浪費**一次注定失敗的 CAS PutItem，
    也不會被誤判成一般 `VersionConflictError`（那會誤導 PR-2/前端以為
    重新讀取最新 version 再試就能解決）。"""
    store, mock_table = _store_with_mock_table()
    item = dict(GOOD_ITEM)
    item["version"] = "garbage"
    mock_table.get_item.return_value = {"Item": item}

    with pytest.raises(VersionCorruptError):
        put_config({"daily_cap_usd": 2.0}, expected_version=7, actor="a@b", store=store)
    mock_table.put_item.assert_not_called()


def test_put_config_consistent_read_failure_propagates():
    """put_config() 內部的 `ConsistentRead` 讀失敗（審計 old 值 + 未變更
    欄位保留來源）必須正常傳播，不可被吞掉當成『item 不存在』盲寫。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}},
        "GetItem",
    )

    with pytest.raises(AdminConfigReadError):
        put_config({"daily_cap_usd": 2.0}, expected_version=7, actor="a@b", store=store)
    mock_table.put_item.assert_not_called()


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
        {"daily_cap_usd": -1.0},  # 負值：儲存層防禦縱深（vp-eng review LOW-8）
        {"daily_cap_usd": -0.01},
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


def test_put_config_zero_cap_allowed_at_store_layer():
    """儲存層只擋**負值**（LOW-8 防禦縱深）；`0` 的業務語意（是否要當
    「緊急關閉」）由 PR-2 API 層的 0.1~50 上下界驗證決定，儲存層不擋。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {}

    result = put_config(
        {"daily_cap_usd": 0.0}, expected_version=0, actor="a@b", store=store
    )
    assert result.config.daily_cap_usd == 0.0


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


def test_put_config_short_token_omits_last4_in_storage_and_audit():
    """token 明文 < 16 字（vp-eng review MEDIUM-1）：`live_token_last4`
    不落庫、審計遮罩也不露 last4——否則短 token 的 last4 等於洩露過半
    甚至全部明文。"""
    store, mock_table = _store_with_mock_table()
    mock_table.get_item.return_value = {}
    short_token = "shorttok"  # 8 字，< _SHORT_TOKEN_LAST4_THRESHOLD (16)

    put_config({"live_token": short_token}, expected_version=0, actor="a@b", store=store)

    config_item = mock_table.put_item.call_args_list[0].kwargs["Item"]
    assert "live_token_last4" not in config_item
    assert config_item["live_token_hash"] == hash_live_token(short_token)

    changes = json.loads(mock_table.put_item.call_args_list[1].kwargs["Item"]["changes_json"])
    assert changes == [{"field": "live_token", "old": "<unset>", "new": "<set>"}]


def test_verify_live_token():
    plaintext = "another-live-token-0123456789abcdef"
    stored = hash_live_token(plaintext)
    assert verify_live_token(plaintext, stored) is True
    assert verify_live_token("wrong-token", stored) is False
    assert verify_live_token("", stored) is False  # fail-closed
    assert verify_live_token(None, stored) is False
    assert verify_live_token(plaintext, None) is False
    assert verify_live_token(None, None) is False


@pytest.mark.parametrize("bad_plaintext", [12345, b"bytes-token", ["a", "b"], {"t": 1}, 1.5])
def test_verify_live_token_rejects_non_str_plaintext(bad_plaintext):
    """vp-eng review LOW-7：`plaintext` 可能來自不受信任的 HTTP 輸入，
    非 str 不得讓 `.encode()` 直接 `AttributeError` 炸掉——一律 fail-closed
    回 `False`。"""
    stored = hash_live_token("some-long-enough-live-token-abcdef")
    assert verify_live_token(bad_plaintext, stored) is False


def test_verify_live_token_rejects_non_str_stored_hash():
    assert verify_live_token("some-long-enough-live-token-abcdef", 12345) is False
    assert verify_live_token("some-long-enough-live-token-abcdef", b"hash-bytes") is False


def test_mask_token_change_short_token_omits_last4():
    """vp-eng review MEDIUM-1：短 token（`new_last4=None`，呼叫端已依
    `_SHORT_TOKEN_LAST4_THRESHOLD` 判定）不得在遮罩值裡露出 last4。"""
    from trustforge.admin_config import _mask_token_change

    assert _mask_token_change(False, "shorttok", None) == ("<unset>", "<set>")
    assert _mask_token_change(True, "shorttok", None) == ("<set>", "<rotated>")
    assert _mask_token_change(True, None, None) == ("<set>", "<cleared>")
    assert _mask_token_change(True, "longenoughtoken1234", "1234") == (
        "<set>", "<rotated last4=1234>",
    )
    assert _mask_token_change(False, "longenoughtoken1234", "1234") == (
        "<unset>", "<set last4=1234>",
    )


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


def test_get_config_cached_does_not_clobber_concurrent_write_through(monkeypatch):
    """vp-eng review MEDIUM-2：`get_config_cached()` 的 cache-miss 鎖外讀
    若在「鎖外讀取中」被別的執行緒的 `put_config()` write-through 搶先
    寫入更新值，這次讀到的（可能已經是舊的）結果**不可回填**蓋掉新值。

    用 `monkeypatch` 讓 `get_config()` 在被呼叫的當下（模擬「鎖外慢速讀
    正在進行」）先模擬另一個執行緒完成 `_store_cache()` write-through，
    確定性重現這個 race（不依賴真的 threading 時序）。"""
    import trustforge.admin_config as admin_config

    admin_config._reset_admin_config_cache_for_tests()
    store, _mock_table = _store_with_mock_table()

    stale = AdminConfig(daily_cap_usd=1.0, version=7, exists=True)
    fresh = AdminConfig(daily_cap_usd=99.0, version=8, exists=True)
    calls = {"n": 0}

    def fake_get_config(_store, consistent=False):
        calls["n"] += 1
        # 模擬：正當這次「鎖外讀取」進行到一半，另一個執行緒的 put_config()
        # 已經 write-through 完成更新值。
        admin_config._store_cache(fresh, now_fn=lambda: 1000.0)
        return stale  # 這個 stale 結果之後絕不可回填蓋掉 fresh

    monkeypatch.setattr(admin_config, "get_config", fake_get_config)

    result = admin_config.get_config_cached(store, now_fn=lambda: 1000.0)

    assert calls["n"] == 1
    assert result == fresh  # 不是 stale——generation 偵測擋下了回填
    # 快取字典裡確實留著 fresh（write-through 寫的），不是被 stale 蓋掉
    assert admin_config._cache["config"] == fresh
