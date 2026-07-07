"""管理控制台運行時設定儲存層（admin console PR-1，計劃 §1/§6）。

存放位置：**既有** `trustforge-connector-cache` DynamoDB 表（PK=`source_id`、
SK=`coin`）的保留字 item——比照 `ingestion/cache.py` 既有保留字慣例
（`__trust_snapshot__`/`__trust_snapshot_history__`，同一張表、零建表、
零 IAM 變更，不是 schema/表異動）：

  - 設定 item：`source_id = "__admin_config__"`、`coin = "_"`（沿用
    `cache_key()` 的 `source:coin` 兩段式慣例；SK 不可空字串，用 `_`
    sentinel，同 `web._STATUS_PROBE_COIN` 慣例）。**單一 item 整包讀寫**，
    `version` 欄位做樂觀鎖（CAS，見 `put_config()`）。
  - 審計 item：`source_id = "__admin_audit__"`、`coin = "{ISO ts}#{uuid8}"`
    （SK 字典序＝時間序，`list_audit()` 用 Query 降序讀回）。
  - ⚠️ 兩種 item 都**不寫 `ttl` 屬性**——表級 TTL 只作用於帶 `ttl` 屬性的
    item，不寫即永存，不會被背景清除（計劃 §1.3）。

設計要點（計劃 §1.3/§1.4/§6）：

  - **金額用字串存**（如 `"1.0"`）避免 DynamoDB Decimal/float 轉換歧義；
    讀取端 `float()` + `math.isfinite` 驗證（比照 `budget_guard.daily_cap_usd()`
    既有容錯），壞欄（非法字串/NaN/Inf/錯型別）→ **該欄視為未設定（None）**
    落呼叫端 fallback，不讓單欄壞資料拖垮整包。
  - **item 不存在 → 回全 None 的空 config**（舊部署/全新環境相容，行為與
    v0.7.0 逐字相同）；**讀取失敗**（網路/憑證/throttle）→ raise
    `AdminConfigReadError` 給呼叫端各自 fail-safe（cap 落 env→DEFAULT、
    `bedrock_enabled` fail-closed——那是 PR-3 呼叫端的責任，本層只負責
    誠實區分「沒資料」vs「讀不到」）。
  - **live token 只存 sha256 hash + 末 4 碼**（`live_token_hash`/
    `live_token_last4`），明文絕不落庫；比對用 `verify_live_token()`
    （`hmac.compare_digest`，恆定時間）。審計裡 token 欄位 old/new 一律記
    `"<set>"/"<unset>"/"<cleared>"/"<rotated last4=xxxx>"` 遮罩值，
    **絕不記明文/完整 hash**。
  - **CAS 寫入**：`ConditionExpression = attribute_not_exists(version) OR
    version = :expected`（比照 `cache.py::DynamoDBCache.set_if_newer` /
    `scheduler_log.py::_update_latest_pointer` 既有條件式 PutItem 慣例），
    衝突 raise `VersionConflictError`（供 PR-2 API 層回 409）。
  - **審計 best-effort 雙寫**：systemd journal（`logging`）+ DynamoDB 審計
    item；審計側路故障**不鎖死**管理操作——PUT 仍成功，但回傳
    `audit_warning` 並記 ERROR log（計劃 §6-2）。
  - **process 內 TTL 快取（15s）**：`get_config_cached()` 給熱路徑用
    （PR-3 的 `daily_cap_usd()` 每請求呼叫，不能每次打 DynamoDB）。
    `threading.Lock` 只保護快取字典的讀寫，**cache-miss 的 DynamoDB 讀在
    鎖外執行**（不可在呼叫端如 `BudgetReservation.try_reserve` 的 lock 內
    做慢網路），且 store 自帶有界 timeout（沿用 `web.py`
    `_STATUS_CACHE_*` / `_PROBE_DYNAMODB_*` 慣例：connect/read 各 3s、
    max_attempts=2）。`put_config()` 成功後 write-through 更新本 process
    快取（其他 process，如 scheduler，最晚 15s 收斂——計劃明示可接受）。

⚠️ 本 repo 端（開發/CI）**不打真 AWS**：`AdminConfigStore.__init__` 只讀
env、不連線，boto3 Table lazy 建立（比照 `DynamoDBCache`）；測試一律注入
mock `_table`（比照 `test_connector_cache.py` 慣例）。

本 PR **只有儲存層**：不開任何 HTTP 端點（PR-2）、不改 `budget_guard`/
`web.py`（PR-3）、無前端（PR-4）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, NamedTuple

_log = logging.getLogger(__name__)

# 保留字 key（比照 cache.py `TRUST_SNAPSHOT_SOURCE` 等常數：單一事實來源，
# 讀寫兩端共用，避免各自手寫字串日後漂移）
ADMIN_CONFIG_SOURCE = "__admin_config__"
ADMIN_CONFIG_COIN = "_"
ADMIN_AUDIT_SOURCE = "__admin_audit__"

# 可透過本層寫入的設定欄位（`live_token` 收明文、落庫轉 hash+last4）
_ALLOWED_CHANGE_FIELDS = frozenset({"daily_cap_usd", "bedrock_enabled", "live_token"})

# process 內 TTL 快取窗（秒）——計劃 §1.4 定 15s
CACHE_TTL_SECONDS = 15.0

# DynamoDB 有界 timeout（沿用 `web.py::_STATUS_CACHE_*` 與
# `fetch_scheduler._PROBE_DYNAMODB_*` 慣例：同步請求路徑不可吃 SDK 預設
# 可達分鐘級的 timeout/重試）
_CONNECT_TIMEOUT_SECONDS = 3.0
_READ_TIMEOUT_SECONDS = 3.0
_MAX_ATTEMPTS = 2


class AdminConfigError(Exception):
    """admin config 儲存層錯誤基底。"""


class AdminConfigReadError(AdminConfigError):
    """設定/審計**讀取失敗**（網路/憑證/throttle/表不存在）——不是「item
    不存在」（那回空 config，是合法狀態）。呼叫端據此各自 fail-safe
    （PR-3：cap 落 env 層、`bedrock_enabled` fail-closed）。"""


class AdminConfigWriteError(AdminConfigError):
    """設定寫入失敗（CAS 衝突以外的錯誤：網路/憑證/throttle）。"""


class VersionConflictError(AdminConfigError):
    """CAS 衝突：item 的 `version` 不等於呼叫端帶的 `expected_version`
    ——寫入期間有別人先改了設定。供 PR-2 API 層回 409（帶最新 version
    讓前端重載再改）。"""

    def __init__(self, expected_version: int):
        super().__init__(
            f"admin config version conflict: expected version {expected_version} "
            "不是目前值（設定已被他人變更，請重新讀取後再改）"
        )
        self.expected_version = expected_version


@dataclass(frozen=True)
class AdminConfig:
    """讀出的設定快照。所有欄位 `None` ＝「未設定」（item 不存在、欄位
    不存在、或欄位存了壞資料被逐欄丟棄）——呼叫端據此落下一層 fallback
    （config → env → DEFAULT，PR-3）。`version=None` 表示 item 不存在
    （首次寫入時 `expected_version` 傳 0）。"""

    daily_cap_usd: float | None = None
    bedrock_enabled: bool | None = None
    live_token_hash: str | None = None
    live_token_last4: str | None = None
    version: int | None = None
    updated_at: str | None = None
    updated_by: str | None = None


class PutConfigResult(NamedTuple):
    """`put_config()` 回傳：新設定快照 + 審計側路警告（`None`＝審計雙寫
    皆成功；非 `None`＝設定**已寫入成功**，但 DynamoDB 審計 item 寫失敗，
    僅 journal 有留痕——PR-2 API 層應把它放進回應 `warning`）。"""

    config: AdminConfig
    audit_warning: str | None


class AdminConfigStore:
    """`trustforge-connector-cache` 表的保留字 item 存取（比照
    `DynamoDBCache`：`__init__` 只讀 env、不連 AWS；boto3 Table lazy 建立；
    測試直接注入 mock `_table`）。"""

    def __init__(self, table_name: str | None = None, region: str | None = None):
        self.table_name = table_name or os.getenv(
            "TRUSTFORGE_CACHE_TABLE", "trustforge-connector-cache"
        )
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._table: Any = None  # lazy：建構本身不連 AWS

    def _get_table(self) -> Any:
        if self._table is None:
            import boto3  # 延遲匯入：建構/測試（mock _table）不需要憑證
            from botocore.config import Config

            config = Config(
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                read_timeout=_READ_TIMEOUT_SECONDS,
                retries={"max_attempts": _MAX_ATTEMPTS, "mode": "standard"},
            )
            self._table = boto3.resource(
                "dynamodb", region_name=self.region, config=config
            ).Table(self.table_name)
        return self._table


# ---------------------------------------------------------------------------
# 預設 store 單例（模組級 lazy；測試用自建 store + mock _table 傳參注入，
# 不碰這個單例）
# ---------------------------------------------------------------------------
_default_store_lock = threading.Lock()
_default_store_instance: AdminConfigStore | None = None


def _default_store() -> AdminConfigStore:
    global _default_store_instance
    with _default_store_lock:
        if _default_store_instance is None:
            _default_store_instance = AdminConfigStore()
        return _default_store_instance


# ---------------------------------------------------------------------------
# live token helpers（明文絕不落庫）
# ---------------------------------------------------------------------------
def hash_live_token(plaintext: str) -> str:
    """token 明文 → sha256 hex（落庫用的唯一形式）。"""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def verify_live_token(plaintext: str | None, stored_hash: str | None) -> bool:
    """比對請求帶來的 token 明文與庫存 hash。恆定時間
    （`hmac.compare_digest`，比照 `web.py` live token 既有慣例）；任一邊
    未設定/空 → 一律 `False`（fail-closed）。"""
    if not plaintext or not stored_hash:
        return False
    return hmac.compare_digest(hash_live_token(plaintext), stored_hash)


# ---------------------------------------------------------------------------
# 讀取（逐欄容錯：壞欄 → None 落 fallback，不拖垮整包）
# ---------------------------------------------------------------------------
def _parse_cap(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool):  # bool 是 int 子類，float(True)=1.0 會靜默吞掉錯型別
        _log.warning("[admin_config] daily_cap_usd 存了 bool（%r），視為未設定", raw)
        return None
    try:
        val = float(str(raw))
    except (TypeError, ValueError):
        _log.warning("[admin_config] daily_cap_usd 無法解析（%r），視為未設定", raw)
        return None
    if not math.isfinite(val):
        _log.warning("[admin_config] daily_cap_usd 非有限值（%r），視為未設定", raw)
        return None
    return val


def _parse_bool(raw: Any, field: str) -> bool | None:
    if raw is None:
        return None
    if not isinstance(raw, bool):  # 嚴格 bool：字串 "true"/Decimal(1) 都不算
        _log.warning("[admin_config] %s 不是 bool（%r），視為未設定", field, raw)
        return None
    return raw


def _parse_str(raw: Any, field: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        _log.warning("[admin_config] %s 不是非空字串（%r），視為未設定", field, raw)
        return None
    return raw


def _parse_version(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        _log.warning("[admin_config] version 存了 bool（%r），視為未設定", raw)
        return None
    try:
        return int(raw)  # DynamoDB N 型別回 Decimal，int() 直接可用
    except (TypeError, ValueError):
        _log.warning("[admin_config] version 無法解析（%r），視為未設定", raw)
        return None


def _config_from_item(item: dict[str, Any]) -> AdminConfig:
    return AdminConfig(
        daily_cap_usd=_parse_cap(item.get("daily_cap_usd")),
        bedrock_enabled=_parse_bool(item.get("bedrock_enabled"), "bedrock_enabled"),
        live_token_hash=_parse_str(item.get("live_token_hash"), "live_token_hash"),
        live_token_last4=_parse_str(item.get("live_token_last4"), "live_token_last4"),
        version=_parse_version(item.get("version")),
        updated_at=_parse_str(item.get("updated_at"), "updated_at"),
        updated_by=_parse_str(item.get("updated_by"), "updated_by"),
    )


def get_config(
    store: AdminConfigStore | None = None, *, consistent: bool = False
) -> AdminConfig:
    """讀 `__admin_config__` item。

    - item 不存在 → 空 `AdminConfig`（全 None）——舊部署/全新環境相容。
    - 讀取失敗 → raise `AdminConfigReadError`（呼叫端各自 fail-safe）。
    - 壞欄 → 該欄 None（逐欄容錯，見模組 docstring）。

    `consistent`：admin API 的「寫後即讀」場景傳 `True`（DynamoDB
    ConsistentRead）；熱路徑走 `get_config_cached()`（最終一致讀即可）。
    """
    resolved = store if store is not None else _default_store()
    try:
        resp = resolved._get_table().get_item(
            Key={"source_id": ADMIN_CONFIG_SOURCE, "coin": ADMIN_CONFIG_COIN},
            ConsistentRead=consistent,
        )
    except Exception as exc:
        raise AdminConfigReadError(f"admin config 讀取失敗: {exc}") from exc
    item = resp.get("Item")
    if not isinstance(item, dict):
        return AdminConfig()  # item 不存在：合法的「尚未設定過」狀態
    return _config_from_item(item)


# ---------------------------------------------------------------------------
# process 內 TTL 快取（熱路徑用；比照 web.py `_status_cache` 慣例）
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"expires_at": 0.0, "config": None}


def get_config_cached(
    store: AdminConfigStore | None = None, now_fn=time.time
) -> AdminConfig:
    """`get_config()` 的 TTL 快取版（15s），給每請求熱路徑（PR-3 的
    `daily_cap_usd()`）用。

    ⚠️ 鎖只保護快取字典；**cache-miss 的 DynamoDB 讀在鎖外執行**（store
    自帶 3s/3s/2 attempts 有界 timeout），不會讓持有呼叫端 lock（如
    `BudgetReservation.try_reserve`）的執行緒卡在慢網路上超過該上界。
    讀取失敗 raise（不做負面快取——由呼叫端 fail-safe；連續失敗時每請求
    最多付一次有界 timeout 的代價）。
    """
    now = now_fn()
    with _cache_lock:
        cached = _cache["config"]
        if cached is not None and now < _cache["expires_at"]:
            return cached
    config = get_config(store)  # 鎖外：慢網路不佔 _cache_lock
    with _cache_lock:
        _cache["config"] = config
        _cache["expires_at"] = now_fn() + CACHE_TTL_SECONDS
    return config


def _store_cache(config: AdminConfig, now_fn=time.time) -> None:
    """write-through：`put_config()` 成功後把新設定直接寫進本 process
    快取（本 process 立即生效；其他 process 最晚 TTL 15s 收斂）。"""
    with _cache_lock:
        _cache["config"] = config
        _cache["expires_at"] = now_fn() + CACHE_TTL_SECONDS


def _reset_admin_config_cache_for_tests() -> None:
    """測試隔離用（比照 `budget_guard._reset_reservation_for_tests` 慣例，
    由 `tests/conftest.py` autouse fixture 每測前後呼叫）。"""
    with _cache_lock:
        _cache["config"] = None
        _cache["expires_at"] = 0.0


# ---------------------------------------------------------------------------
# 寫入（CAS）+ 審計
# ---------------------------------------------------------------------------
def _mask_token_change(
    had_token: bool, new_plaintext: str | None
) -> tuple[str, str]:
    """審計用 token 遮罩值（old, new）——絕不出現明文/完整 hash。"""
    old = "<set>" if had_token else "<unset>"
    if new_plaintext is None:
        new = "<cleared>"
    elif had_token:
        new = f"<rotated last4={new_plaintext[-4:]}>"
    else:
        new = f"<set last4={new_plaintext[-4:]}>"
    return old, new


def _validate_changes(changes: dict[str, Any]) -> None:
    """儲存層最小驗證（範圍/長度等業務驗證歸 PR-2 API 層；這裡只擋「寫進
    庫會變成壞資料」的型別/有限性問題，維持庫內資料自洽）。"""
    if not changes:
        raise ValueError("changes 不可為空（至少要改一個欄位）")
    unknown = set(changes) - _ALLOWED_CHANGE_FIELDS
    if unknown:
        raise ValueError(f"不支援的設定欄位: {sorted(unknown)}")
    if "daily_cap_usd" in changes and changes["daily_cap_usd"] is not None:
        cap = changes["daily_cap_usd"]
        if isinstance(cap, bool) or not isinstance(cap, (int, float)):
            raise ValueError(f"daily_cap_usd 必須是數字，不是 {type(cap).__name__}")
        if not math.isfinite(float(cap)):
            raise ValueError(f"daily_cap_usd 必須是有限數: {cap!r}")
    if "bedrock_enabled" in changes and changes["bedrock_enabled"] is not None:
        if not isinstance(changes["bedrock_enabled"], bool):
            raise ValueError("bedrock_enabled 必須是 bool")
    if "live_token" in changes and changes["live_token"] is not None:
        token = changes["live_token"]
        if not isinstance(token, str) or not token:
            raise ValueError("live_token 必須是非空字串（或 None＝清除）")


def put_config(
    changes: dict[str, Any],
    expected_version: int | None,
    actor: str,
    *,
    store: AdminConfigStore | None = None,
    user_agent: str | None = None,
    now_fn=time.time,
) -> PutConfigResult:
    """CAS 寫入設定 + 審計（計劃 §1.3/§6）。

    `changes`：部分更新，允許鍵 `daily_cap_usd`（float|None）、
    `bedrock_enabled`（bool|None）、`live_token`（**明文** str|None——本層
    落庫前轉 sha256 hash + last4，明文不落庫、不進審計）。值 `None`＝清除
    該欄（回落 env/DEFAULT 層）。

    `expected_version`：呼叫端剛讀到的 `version`（item 不存在時傳 0 或
    None）。CAS 條件 `attribute_not_exists(version) OR version = :expected`；
    衝突 raise `VersionConflictError`（PR-2 回 409）。

    寫成功後：journal + DynamoDB 審計雙寫（best-effort，DynamoDB 審計失敗
    不回滾、`audit_warning` 帶出）、write-through 更新本 process TTL 快取。
    """
    _validate_changes(changes)
    if expected_version is None:
        expected_version = 0
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise ValueError("expected_version 必須是 int（item 不存在時傳 0）")
    if expected_version < 0:
        raise ValueError(f"expected_version 不可為負: {expected_version}")
    if not actor or not isinstance(actor, str):
        raise ValueError("actor 必須是非空字串（如 'admin@203.0.113.5'）")

    resolved = store if store is not None else _default_store()

    # 讀當前值：審計 old 值 + 未變更欄位的保留來源（ConsistentRead——寫入
    # 決策不可根據過期讀）。讀失敗 → raise（沒有可信 old 值就不寫，避免
    # 盲寫覆蓋 + 審計記錯 old）。
    current = get_config(resolved, consistent=True)
    version_to = expected_version + 1
    now_iso = datetime.now(timezone.utc).isoformat()

    # 合併：未出現在 changes 的欄位沿用當前值（parse 過的——當前壞欄等同
    # 未設定，合併後自然被清掉，庫內資料收斂回自洽狀態）
    new_cap = current.daily_cap_usd
    if "daily_cap_usd" in changes:
        raw_cap = changes["daily_cap_usd"]
        new_cap = None if raw_cap is None else float(raw_cap)
    new_enabled = current.bedrock_enabled
    if "bedrock_enabled" in changes:
        new_enabled = changes["bedrock_enabled"]
    new_hash = current.live_token_hash
    new_last4 = current.live_token_last4
    if "live_token" in changes:
        plaintext = changes["live_token"]
        if plaintext is None:
            new_hash = None
            new_last4 = None
        else:
            new_hash = hash_live_token(plaintext)
            new_last4 = plaintext[-4:]

    item: dict[str, Any] = {
        "source_id": ADMIN_CONFIG_SOURCE,
        "coin": ADMIN_CONFIG_COIN,
        "version": version_to,
        "updated_at": now_iso,
        "updated_by": actor,
        # ⚠️ 不寫 `ttl` 屬性：表級 TTL 不得回收 config item（計劃 §1.3）
    }
    if new_cap is not None:
        item["daily_cap_usd"] = str(new_cap)  # 金額字串存，避免 Decimal/float 歧義
    if new_enabled is not None:
        item["bedrock_enabled"] = new_enabled
    if new_hash is not None:
        item["live_token_hash"] = new_hash
    if new_last4 is not None:
        item["live_token_last4"] = new_last4

    from boto3.dynamodb.conditions import Attr  # 延遲匯入，同 boto3 lazy 慣例
    from botocore.exceptions import ClientError

    condition = Attr("version").not_exists() | Attr("version").eq(expected_version)
    try:
        resolved._get_table().put_item(Item=item, ConditionExpression=condition)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise VersionConflictError(expected_version) from exc
        raise AdminConfigWriteError(f"admin config 寫入失敗: {exc}") from exc
    except Exception as exc:
        raise AdminConfigWriteError(f"admin config 寫入失敗: {exc}") from exc

    # ---- 審計（設定已寫入成功，以下 best-effort，不回滾）----
    change_entries: list[dict[str, Any]] = []
    if "daily_cap_usd" in changes:
        change_entries.append(
            {"field": "daily_cap_usd", "old": current.daily_cap_usd, "new": new_cap}
        )
    if "bedrock_enabled" in changes:
        change_entries.append(
            {"field": "bedrock_enabled", "old": current.bedrock_enabled, "new": new_enabled}
        )
    if "live_token" in changes:
        old_masked, new_masked = _mask_token_change(
            current.live_token_hash is not None, changes["live_token"]
        )
        change_entries.append({"field": "live_token", "old": old_masked, "new": new_masked})

    # journal（logging → systemd journal）先寫：最便宜、幾乎不會失敗，
    # DynamoDB 審計掛了也至少有這一份（計劃 §6-2）
    _log.info(
        "[admin_config] %s by %s (version %d -> %d)",
        "; ".join(f"{c['field']}: {c['old']!r} -> {c['new']!r}" for c in change_entries),
        actor,
        expected_version,
        version_to,
    )

    audit_warning: str | None = None
    audit_item: dict[str, Any] = {
        "source_id": ADMIN_AUDIT_SOURCE,
        "coin": f"{now_iso}#{uuid.uuid4().hex[:8]}",  # SK 字典序＝時間序
        "ts": now_iso,
        "actor": actor,
        "changes_json": json.dumps(change_entries, ensure_ascii=False),
        "version_from": expected_version,
        "version_to": version_to,
        # 同樣不寫 `ttl` 屬性：審計不得被表級 TTL 回收
    }
    if user_agent:
        audit_item["user_agent"] = str(user_agent)
    try:
        resolved._get_table().put_item(Item=audit_item)
    except Exception as exc:
        audit_warning = (
            "設定已寫入成功，但 DynamoDB 審計紀錄寫入失敗（journal 已留痕）: "
            f"{exc}"
        )
        _log.error("[admin_config] 審計 item 寫入失敗（設定本身已成功）: %s", exc)

    new_config = AdminConfig(
        daily_cap_usd=new_cap,
        bedrock_enabled=new_enabled,
        live_token_hash=new_hash,
        live_token_last4=new_last4,
        version=version_to,
        updated_at=now_iso,
        updated_by=actor,
    )
    _store_cache(new_config, now_fn=now_fn)  # write-through（本 process 立即生效）
    return PutConfigResult(config=new_config, audit_warning=audit_warning)


# ---------------------------------------------------------------------------
# 審計讀回
# ---------------------------------------------------------------------------
def list_audit(
    limit: int = 50, *, store: AdminConfigStore | None = None
) -> list[dict[str, Any]]:
    """近 `limit` 筆設定變更審計，時間降序（Query PK=`__admin_audit__`、
    `ScanIndexForward=False`——SK 是 `{ISO ts}#{uuid8}`，字典序＝時間序，
    絕不 Scan）。讀失敗 raise `AdminConfigReadError`。"""
    if limit <= 0:
        return []
    resolved = store if store is not None else _default_store()
    from boto3.dynamodb.conditions import Key  # 延遲匯入，同 boto3 lazy 慣例

    try:
        resp = resolved._get_table().query(
            KeyConditionExpression=Key("source_id").eq(ADMIN_AUDIT_SOURCE),
            ScanIndexForward=False,
            Limit=limit,
        )
    except Exception as exc:
        raise AdminConfigReadError(f"admin audit 讀取失敗: {exc}") from exc

    records: list[dict[str, Any]] = []
    for item in resp.get("Items", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            entries = json.loads(item.get("changes_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            entries = []
        if not isinstance(entries, list):
            entries = []
        records.append(
            {
                "ts": item.get("ts"),
                "actor": item.get("actor"),
                "changes": entries,
                "version_from": _parse_version(item.get("version_from")),
                "version_to": _parse_version(item.get("version_to")),
                "user_agent": item.get("user_agent"),
            }
        )
    return records
