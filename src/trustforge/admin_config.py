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
    **絕不記明文/完整 hash**。⚠️ vp-eng review MEDIUM-1：token 明文長度
    `< _SHORT_TOKEN_LAST4_THRESHOLD`（16）時**不落 last4**、審計遮罩也
    只給 `"<set>"/"<rotated>"`（無 last4）——否則短 token 的 last4 等於
    洩露過半甚至全部明文，破壞「明文絕不落庫」不變式。
  - **CAS 寫入**：`expected_version == 0`（item 應不存在）→ 條件僅
    `attribute_not_exists(version)`；`expected_version > 0` → 條件僅
    `version = :expected`（vp-eng review MEDIUM-3/LOW-6：兩者**不再用
    OR 合併**——舊版 OR 寫法會讓「對空表傳任意正數 version」也因
    `not_exists()` 分支通過而誤放行，語意上不該接受）。衝突 raise
    `VersionConflictError`（供 PR-2 API 層回 409）。
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
    TTL 比較改用 `time.monotonic`（`now_fn` 預設，vp-eng review LOW-5）：
    `time.time()` 會被系統時鐘回撥影響，`monotonic` 不受影響，是 TTL
    這種「相對時間窗」該用的時鐘。⚠️ vp-eng review MEDIUM-2（write-through
    被 stale 讀 clobber）：快取字典加 `generation` 計數，`_store_cache()`
    （`put_config()` 的 write-through）每次遞增；`get_config_cached()` 在
    鎖外讀完 DynamoDB 回填前，若發現鎖內 `generation` 已經變了（代表這段
    鎖外讀取期間，有一個 `put_config()` 搶先 write-through 了更新的值），
    直接丟棄這次讀到的舊值、改回傳鎖內目前值——避免「cache-miss 鎖外讀到
    的舊資料」蓋掉「put 剛寫入的新資料」，破壞「本 process 立即生效」的
    write-through 承諾。
  - **version 損毀訊號**：`AdminConfig.exists`／`AdminConfig.version_corrupt`
    區分「item 不存在」（`exists=False`）vs「item 存在但 `version` 欄位
    損毀成非 int」（`exists=True, version_corrupt=True, version=None`）
    （vp-eng review MEDIUM-3）——後者若不區分，CAS 會永遠對不上任何
    `expected_version`（包含 0，因為 item 其實存在）陷入無法修復的 409
    死鎖。**修復 SOP**（PR-2/維運）：`version_corrupt=True` 時人工用
    AWS Console/CLI 對 `__admin_config__` item 的 `version` 屬性寫回一個
    合法整數（或整包刪除該 item，等同回到「未設定過」狀態），本層不提供
    自動修復（避免自動邏輯誤判合法的並發寫入）。

⚠️ 本 repo 端（開發/CI）**不打真 AWS**：`AdminConfigStore.__init__` 只讀
env、不連線，boto3 Table lazy 建立（比照 `DynamoDBCache`；lazy 建立本身加
`threading.Lock` 雙重檢查，vp-eng review LOW-9：`boto3` 預設 session 在
多執行緒下並發首次建立不是 thread-safe 的）；測試一律注入 mock `_table`
（比照 `test_connector_cache.py` 慣例）。

⚠️ `AdminConfig` **絕不可直接序列化進 API 回應**（`live_token_hash` 是
機敏欄位）——PR-2 一律呼叫 `AdminConfig.to_public_dict()`（vp-eng review
LOW-11）。

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
# issue #155：新增 `disabled_sources`——admin 可明確關掉個別真實連接器
# （如 ["coindesk"]）；預設空（= 全啟用，fail-closed：忘了設也不會誤關真實源）。
_ALLOWED_CHANGE_FIELDS = frozenset(
    {"daily_cap_usd", "bedrock_enabled", "hermes_autonomy_enabled", "live_token", "disabled_sources"}
)

# process 內 TTL 快取窗（秒）——計劃 §1.4 定 15s
CACHE_TTL_SECONDS = 15.0

# token 明文長度低於此門檻 → 不落 last4、審計也不露 last4（vp-eng review
# MEDIUM-1）：短 token 的 last4 等於洩露過半甚至全部明文。16 是「明顯比
# 4 碼展示值長很多」的保守門檻（前端建議產生 32 bytes hex＝64 字元token，
# 這裡不採用該上限本身，只擋「短到 last4 洩密」這個具體風險）。
_SHORT_TOKEN_LAST4_THRESHOLD = 16

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


class VersionCorruptError(AdminConfigError):
    """`__admin_config__` item 存在，但 `version` 欄位損毀成非 int
    （`AdminConfig.version_corrupt`，vp-eng review MEDIUM-3）——CAS 對任何
    `expected_version` 都不可能成功（一般 `VersionConflictError` 暗示
    「重新讀取最新 version 再試一次」就能解決，這種情況不行，會無限
    409）。PR-2 應把它呈現成明確的「設定損毀，需人工介入」異常，而不是
    一般的樂觀鎖衝突。修復 SOP 見模組頂部說明（人工修正/刪除該 item）。"""

    def __init__(self):
        super().__init__(
            "admin config item 存在但 version 欄位損毀（非合法整數）——"
            "CAS 無法對任何 expected_version 成功，需人工修復（見模組頂部 "
            "'version 損毀訊號' SOP 說明），不是一般的樂觀鎖衝突"
        )


@dataclass(frozen=True)
class AdminConfig:
    """讀出的設定快照。所有欄位 `None` ＝「未設定」（item 不存在、欄位
    不存在、或欄位存了壞資料被逐欄丟棄）——呼叫端據此落下一層 fallback
    （config → env → DEFAULT，PR-3）。

    `exists`／`version_corrupt`（vp-eng review MEDIUM-3）：`version=None`
    本身**無法區分**「item 根本不存在」（`exists=False`，首次寫入時
    `expected_version` 傳 0 即可）跟「item 存在但 `version` 欄位被寫壞成
    非 int」（`exists=True, version_corrupt=True`——CAS 對任何
    `expected_version` 都無法通過，需人工修復，見模組頂部「version 損毀
    訊號」SOP 說明）。PR-2 應對後者呈現明確異常，不能沿用一般 409 流程
    （409 暗示「重讀就能改」，但損毀狀態重讀還是拿不到合法 version）。"""

    daily_cap_usd: float | None = None
    bedrock_enabled: bool | None = None
    hermes_autonomy_enabled: bool | None = None
    live_token_hash: str | None = None
    live_token_last4: str | None = None
    # issue #155：被明確關掉的連接器名稱集合（如 {"coindesk", "sec-gov"}）。
    # 預設 None（= 空，全啟用，fail-closed）。落庫成排序後的 list；讀回轉 frozenset。
    disabled_sources: set[str] | None = None
    version: int | None = None
    updated_at: str | None = None
    updated_by: str | None = None
    exists: bool = False
    version_corrupt: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        """給 PR-2 API 回應用的安全序列化——**刻意排除** `live_token_hash`
        （vp-eng review LOW-11）。`live_token_last4` 本來就是設計給前端
        顯示用的非機敏值（見模組 docstring「live token 只存 sha256 hash +
        末 4 碼」），保留。⚠️ PR-2 絕不可改用 `dataclasses.asdict(config)`
        或直接把 `AdminConfig` 丟進 `json.dumps`——那樣會把 `live_token_hash`
        原樣序列化出去。"""
        return {
            "daily_cap_usd": self.daily_cap_usd,
            "bedrock_enabled": self.bedrock_enabled,
            "hermes_autonomy_enabled": self.hermes_autonomy_enabled,
            "live_token_last4": self.live_token_last4,
            "live_token_configured": self.live_token_hash is not None,
            "disabled_sources": sorted(self.disabled_sources) if self.disabled_sources else [],
            "version": self.version,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "exists": self.exists,
            "version_corrupt": self.version_corrupt,
        }


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
        # lazy 建立的雙重檢查鎖（vp-eng review LOW-9）：boto3 預設 session
        # 在多執行緒下並發首次建立 resource/Table 不是 thread-safe 的，
        # 若兩個請求同時撞上 `self._table is None` 各自建一份，可能互相
        # 干擾/浪費連線。這把鎖只序列化「lazy 建立」那一瞬間，不影響建好
        # 之後的平行讀寫（`get_item`/`put_item`/`query` 本身是 thread-safe）。
        self._table_lock = threading.Lock()

    def _get_table(self) -> Any:
        if self._table is None:
            with self._table_lock:
                if self._table is None:  # 雙重檢查：拿到鎖後可能已被別的執行緒建好
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


def _is_local_admin_config_unavailable(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict):
            code = str(error.get("Code", ""))
            if code in {
                "AccessDenied",
                "AccessDeniedException",
                "ExpiredTokenException",
                "InvalidClientTokenId",
                "NoCredentialsError",
                "PartialCredentialsError",
                "ResourceNotFoundException",
                "UnrecognizedClientException",
            }:
                return True

    name = type(exc).__name__
    if name in {"NoCredentialsError", "PartialCredentialsError"}:
        return True

    text = str(exc)
    return any(
        marker in text
        for marker in (
            "AccessDenied",
            "ExpiredToken",
            "InvalidClientTokenId",
            "NoCredentialsError",
            "PartialCredentialsError",
            "ResourceNotFoundException",
            "UnrecognizedClientException",
        )
    )


# ---------------------------------------------------------------------------
# live token helpers（明文絕不落庫）
# ---------------------------------------------------------------------------
def hash_live_token(plaintext: str) -> str:
    """token 明文 → sha256 hex（落庫用的唯一形式）。"""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def verify_live_token(plaintext: Any, stored_hash: Any) -> bool:
    """比對請求帶來的 token 明文與庫存 hash。恆定時間
    （`hmac.compare_digest`，比照 `web.py` live token 既有慣例）；任一邊
    未設定/空 → 一律 `False`（fail-closed）。

    型別刻意標 `Any`（不是 `str | None`）：`plaintext` 可能來自 HTTP
    header/query 這種不受信任輸入，呼叫端萬一手滑傳非 str（如 `bytes`/
    `int`/`list`）不該讓這裡直接 `AttributeError` 炸掉（vp-eng review
    LOW-7）——一律先做 `isinstance` 檢查，非 str 視同「沒帶 token」
    fail-closed 回 `False`。"""
    if not isinstance(plaintext, str) or not plaintext:
        return False
    if not isinstance(stored_hash, str) or not stored_hash:
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


def _parse_str(raw: Any, field: str, *, sensitive: bool = False) -> str | None:
    """`sensitive=True`（vp-eng review LOW-10）：欄位本身是機敏值（token
    hash/last4）——損毀值的警告 log **只印型別，不印 `%r` 內容**，避免
    「壞掉的 hash/last4」原樣洩漏到 log（損毀值仍可能是攻擊者可控輸入或
    部分明文殘留）。非 sensitive 欄位維持印值方便除錯。"""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        if sensitive:
            _log.warning(
                "[admin_config] %s 不是非空字串（型別 %s），視為未設定",
                field, type(raw).__name__,
            )
        else:
            _log.warning("[admin_config] %s 不是非空字串（%r），視為未設定", field, raw)
        return None
    return raw


def _parse_set(raw: Any, field: str) -> set[str] | None:
    """issue #155：把 DynamoDB 讀回的 `disabled_sources`（list）轉成
    frozenset[str]；None/非 list/含非字串元素一律視為未設定（None），不拖垮
    整包（逐欄容錯，同 `_parse_cap` 等）。空 list 視為未設定（= 全啟用）。"""
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple, set, frozenset)):
        _log.warning("[admin_config] %s 不是 list/set（%r），視為未設定", field, type(raw).__name__)
        return None
    out: set[str] = set()
    for v in raw:
        if isinstance(v, str) and v:
            out.add(v)
        else:
            _log.warning("[admin_config] %s 元素非非空字串（%r），忽略", field, v)
    return frozenset(out) if out else None


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
    version_raw = item.get("version")
    version = _parse_version(version_raw)
    # version 損毀訊號（vp-eng review MEDIUM-3）：item 確實存在、`version`
    # 欄位也確實有值，但解析失敗（非 int）——跟「item 不存在」（version_raw
    # 為 None）區分開，見 `AdminConfig` docstring。
    version_corrupt = version_raw is not None and version is None
    return AdminConfig(
        daily_cap_usd=_parse_cap(item.get("daily_cap_usd")),
        bedrock_enabled=_parse_bool(item.get("bedrock_enabled"), "bedrock_enabled"),
        hermes_autonomy_enabled=_parse_bool(item.get("hermes_autonomy_enabled"), "hermes_autonomy_enabled"),
        live_token_hash=_parse_str(
            item.get("live_token_hash"), "live_token_hash", sensitive=True
        ),
        live_token_last4=_parse_str(
            item.get("live_token_last4"), "live_token_last4", sensitive=True
        ),
        disabled_sources=_parse_set(item.get("disabled_sources"), "disabled_sources"),
        version=version,
        updated_at=_parse_str(item.get("updated_at"), "updated_at"),
        updated_by=_parse_str(item.get("updated_by"), "updated_by"),
        exists=True,
        version_corrupt=version_corrupt,
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
        if _is_local_admin_config_unavailable(exc):
            _log.warning("[admin_config] local admin config unavailable, using empty config: %s", exc)
            return AdminConfig()
        raise AdminConfigReadError(f"admin config 讀取失敗: {exc}") from exc
    item = resp.get("Item")
    if not isinstance(item, dict):
        return AdminConfig()  # item 不存在：合法的「尚未設定過」狀態
    return _config_from_item(item)


# ---------------------------------------------------------------------------
# process 內 TTL 快取（熱路徑用；比照 web.py `_status_cache` 慣例）
#
# `now_fn` 預設改用 `time.monotonic`（vp-eng review LOW-5）：TTL 是「相對
# 時間窗」的比較，`time.time()` 會被系統時鐘校時/回撥干擾（可能讓快取
# 提前或延後過期），`monotonic` 不受影響。呼叫端若自訂 `now_fn`（如測試
# 用假時鐘），務必同一個時鐘家族貫穿 `get_config_cached()`/`put_config()`
# 兩處呼叫，兩者混用不同時鐘基準會讓過期判斷失真。
#
# `generation`（vp-eng review MEDIUM-2）：`_store_cache()`（write-through）
# 每次遞增；`get_config_cached()` 鎖外讀 DynamoDB 完成、要回填鎖內快取前，
# 先比對鎖內 `generation` 是否還是自己開始讀之前那個值——若已經變了，代表
# 這段鎖外讀取期間有 `put_config()` 搶先 write-through 了更新的值，這次
# 讀到的（可能已經是舊的）結果**不可回填**，直接回傳鎖內目前值，避免蓋掉
# 剛寫入的新設定（破壞「本 process 立即生效」的 write-through 承諾）。
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"expires_at": 0.0, "config": None, "generation": 0}


def get_config_cached(
    store: AdminConfigStore | None = None, now_fn=time.monotonic
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
        generation_before = _cache["generation"]
    config = get_config(store)  # 鎖外：慢網路不佔 _cache_lock
    with _cache_lock:
        if _cache["generation"] != generation_before:
            # 鎖外讀取期間有 put_config() write-through 搶先寫入更新值：
            # 這次讀到的結果可能已經是舊的，不可回填蓋掉新值（MEDIUM-2）。
            # 直接回傳鎖內目前值（write-through 剛寫入的，一定比我們手上
            # 這份新鮮）；若鎖內意外沒值（理論上不會，write-through 一定
            # 帶 config），保守 fallback 回這次讀到的值。
            return _cache["config"] if _cache["config"] is not None else config
        _cache["config"] = config
        _cache["expires_at"] = now_fn() + CACHE_TTL_SECONDS
    return config


def _store_cache(config: AdminConfig, now_fn=time.monotonic) -> None:
    """write-through：`put_config()` 成功後把新設定直接寫進本 process
    快取（本 process 立即生效；其他 process 最晚 TTL 15s 收斂）。遞增
    `generation`，供 `get_config_cached()` 偵測「鎖外讀取期間被搶先
    write-through」而放棄回填舊值（vp-eng review MEDIUM-2）。"""
    with _cache_lock:
        _cache["config"] = config
        _cache["expires_at"] = now_fn() + CACHE_TTL_SECONDS
        _cache["generation"] += 1


def _reset_admin_config_cache_for_tests() -> None:
    """測試隔離用（比照 `budget_guard._reset_reservation_for_tests` 慣例，
    由 `tests/conftest.py` autouse fixture 每測前後呼叫）。一併清
    `get_config_cached_failsoft()` 的失敗負快取窗。"""
    global _fail_window_until
    with _cache_lock:
        _cache["config"] = None
        _cache["expires_at"] = 0.0
        _cache["generation"] = 0
    with _fail_window_lock:
        _fail_window_until = 0.0


# ---------------------------------------------------------------------------
# opt-in 失敗負快取（PR-3 複審 qa M1）：budget 熱路徑專用
#
# `get_config_cached()` 本身刻意**不做** negative caching（儲存層誠實 raise，
# 由各呼叫端自主權衡）。web 顯示/閘門層已有自己的 15s 失敗窗
# （`web._admin_cfg_fail_until`），但 budget 路徑（`budget_guard.
# daily_cap_usd_resolved()` → `pipeline.daily_cap_exceeded()`，**每個**
# analyze 請求都走、含純離線檔位）先前直呼 `get_config_cached()`——DynamoDB
# 持續故障（斷網/憑證失效/throttle）時，每個請求都要重付一次有界 timeout
# （connect 3s + read 3s × 2 attempts，最壞 ~12s）的失敗讀，等於管理面故障
# 對所有分析流量加上秒級延遲洪水。budget_guard 不能 import web 拿它的失敗窗
# （web import budget_guard，會循環），所以在本層提供這個 **opt-in** 包裝：
# 失敗後 `CACHE_TTL_SECONDS`（15s）窗內直接 raise `AdminConfigReadError`
# （不打 DynamoDB，呼叫端 except 落 env/fail-closed，跟真失敗同一條路徑），
# 期滿才重試；成功即清窗。恢復延遲 ≤15s，與 TTL 收斂上界一致。
# ---------------------------------------------------------------------------
_fail_window_lock = threading.Lock()
_fail_window_until = 0.0  # time.monotonic 基準；0.0＝無失敗窗


def get_config_cached_failsoft(
    store: AdminConfigStore | None = None, now_fn=time.monotonic
) -> AdminConfig:
    """`get_config_cached()` 的失敗負快取版（opt-in，budget 熱路徑用）。

    行為：讀取失敗 → 記 15s 失敗窗並照樣 raise（呼叫端 fail-safe 邏輯
    不變）；失敗窗內的後續呼叫**不打 DynamoDB**、直接 raise
    `AdminConfigReadError`（呼叫端同一條 except 路徑落 env/fail-closed）；
    讀取成功 → 清窗、回傳 config。`now_fn` 須與 `get_config_cached()`
    同一個時鐘家族（預設 `time.monotonic`，見上方 TTL 快取區說明）。"""
    global _fail_window_until
    now = now_fn()
    with _fail_window_lock:
        if now < _fail_window_until:
            raise AdminConfigReadError(
                "admin config 失敗負快取窗內（前次讀取失敗，"
                f"{CACHE_TTL_SECONDS:.0f}s 內不重試 DynamoDB）"
            )
    try:
        config = get_config_cached(store, now_fn=now_fn)
    except Exception:
        with _fail_window_lock:
            _fail_window_until = now_fn() + CACHE_TTL_SECONDS
        raise
    with _fail_window_lock:
        _fail_window_until = 0.0
    return config


# ---------------------------------------------------------------------------
# 寫入（CAS）+ 審計
# ---------------------------------------------------------------------------
def _mask_token_change(
    had_token: bool, new_plaintext: str | None, new_last4: str | None
) -> tuple[str, str]:
    """審計用 token 遮罩值（old, new）——絕不出現明文/完整 hash。

    `new_last4`：呼叫端算好的末 4 碼，**只有明文長度 >=
    `_SHORT_TOKEN_LAST4_THRESHOLD` 時才會有值**（vp-eng review MEDIUM-1）
    ——短 token 傳 `None`，這裡就不露 last4（`"<set>"`/`"<rotated>"`
    不帶 last4），避免短 token 的 last4 洩露過半甚至全部明文。"""
    old = "<set>" if had_token else "<unset>"
    if new_plaintext is None:
        new = "<cleared>"
    elif new_last4 is not None:
        new = f"<rotated last4={new_last4}>" if had_token else f"<set last4={new_last4}>"
    else:
        new = "<rotated>" if had_token else "<set>"
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
        # 儲存層防禦縱深（vp-eng review LOW-8）：上下界（0.1~50）業務規則
        # 歸 PR-2 API 層驗證，但負值在任何一層都不該被允許寫入——即使
        # PR-2 驗證日後有漏洞/被繞過，儲存層仍擋住「cap 變負數」這種明顯
        # 荒謬的資料（不影響既有「cap<=0＝env 層緊急關閉」語意：那個語意
        # 專屬 env 層，config 層本來就不該出現負值）。
        if float(cap) < 0:
            raise ValueError(f"daily_cap_usd 不可為負值: {cap!r}")
    if "bedrock_enabled" in changes and changes["bedrock_enabled"] is not None:
        if not isinstance(changes["bedrock_enabled"], bool):
            raise ValueError("bedrock_enabled 必須是 bool")
    if "hermes_autonomy_enabled" in changes and changes["hermes_autonomy_enabled"] is not None:
        if not isinstance(changes["hermes_autonomy_enabled"], bool):
            raise ValueError("hermes_autonomy_enabled 必須是 bool")
    if "live_token" in changes and changes["live_token"] is not None:
        token = changes["live_token"]
        if not isinstance(token, str) or not token:
            raise ValueError("live_token 必須是非空字串（或 None＝清除）")
    if "disabled_sources" in changes:
        ds = changes["disabled_sources"]
        if ds is not None:
            if not isinstance(ds, (list, tuple, set, frozenset)):
                raise ValueError("disabled_sources 必須是 list/set（或 None＝清除）")
            for v in ds:
                if not isinstance(v, str) or not v:
                    raise ValueError("disabled_sources 元素必須是非空字串")


def put_config(
    changes: dict[str, Any],
    expected_version: int | None,
    actor: str,
    *,
    store: AdminConfigStore | None = None,
    user_agent: str | None = None,
    now_fn=time.monotonic,
) -> PutConfigResult:
    """CAS 寫入設定 + 審計（計劃 §1.3/§6）。

    `changes`：部分更新，允許鍵 `daily_cap_usd`（float|None）、
    `bedrock_enabled`（bool|None）、`live_token`（**明文** str|None——本層
    落庫前轉 sha256 hash + last4（token 過短見 `_SHORT_TOKEN_LAST4_THRESHOLD`
    不落 last4），明文不落庫、不進審計）。值 `None`＝清除該欄（回落
    env/DEFAULT 層）。

    `expected_version`：呼叫端剛讀到的 `version`（item 不存在時傳 0 或
    None）。CAS 條件依 `expected_version` 是否為 0 分兩種（vp-eng review
    MEDIUM-3/LOW-6，不再用 OR 合併）：`== 0` → 只用
    `attribute_not_exists(version)`；`> 0` → 只用
    `version = :expected`。衝突 raise `VersionConflictError`（PR-2 回
    409）；item 存在但 `version` 欄位本身損毀（非 int）→ raise
    `VersionCorruptError`（不可能被一般重試解決，見該例外 docstring）。

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
    if current.version_corrupt:
        # 提早失敗（vp-eng review MEDIUM-3）：item 存在但 version 損毀，
        # 任何 expected_version 的 CAS 都注定失敗，不必浪費一次 PutItem
        # 往返，直接給 PR-2 一個能明確區分「需人工修復」而非「重試即可」
        # 的訊號。
        raise VersionCorruptError()
    version_to = expected_version + 1
    now_iso = datetime.now(timezone.utc).isoformat()

    # 合併：未出現在 changes 的欄位沿用當前值（parse 過的——當前壞欄等同
    # 未設定，合併後自然被清掉，庫內資料收斂回自洽狀態）
    new_cap = current.daily_cap_usd
    if "daily_cap_usd" in changes:
        raw_cap = changes["daily_cap_usd"]
        new_cap = None if raw_cap is None else float(raw_cap)
        # 二次防禦（vp-eng review LOW-8）：`_validate_changes` 已擋過，這裡
        # 再確認一次——即使未來有人繞過 `_validate_changes` 直接呼叫合併
        # 邏輯，也不會讓負值真的落庫。
        if new_cap is not None and new_cap < 0:
            raise ValueError(f"daily_cap_usd 不可為負值: {new_cap!r}")
    new_enabled = current.bedrock_enabled
    if "bedrock_enabled" in changes:
        new_enabled = changes["bedrock_enabled"]
    new_autonomy = current.hermes_autonomy_enabled
    if "hermes_autonomy_enabled" in changes:
        new_autonomy = changes["hermes_autonomy_enabled"]
    new_hash = current.live_token_hash
    new_last4 = current.live_token_last4
    if "live_token" in changes:
        plaintext = changes["live_token"]
        if plaintext is None:
            new_hash = None
            new_last4 = None
        else:
            new_hash = hash_live_token(plaintext)
            # 短 token 不落 last4（vp-eng review MEDIUM-1）：見
            # `_SHORT_TOKEN_LAST4_THRESHOLD` 說明，避免短明文被 last4 洩漏過半甚至全部內容。
            new_last4 = (
                plaintext[-4:] if len(plaintext) >= _SHORT_TOKEN_LAST4_THRESHOLD else None
            )

    # issue #155：disabled_sources 合併（None＝清除，回復全啟用 fail-closed）。
    new_disabled = current.disabled_sources
    if "disabled_sources" in changes:
        raw_ds = changes["disabled_sources"]
        new_disabled = None if raw_ds is None else frozenset(raw_ds)

    # 相同設定不應產生新版本或稽核紀錄。這尤其重要於二態開關：管理面
    # 重送「開啟」時，紀錄必須保留真正的狀態轉換，而不是出現「開 → 開」。
    if (
        new_cap == current.daily_cap_usd
        and new_enabled == current.bedrock_enabled
        and new_autonomy == current.hermes_autonomy_enabled
        and new_hash == current.live_token_hash
        and new_last4 == current.live_token_last4
        and new_disabled == current.disabled_sources
    ):
        return PutConfigResult(config=current, audit_warning=None)


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
    if new_autonomy is not None:
        item["hermes_autonomy_enabled"] = new_autonomy
    if new_hash is not None:
        item["live_token_hash"] = new_hash
    if new_last4 is not None:
        item["live_token_last4"] = new_last4
    if new_disabled:  # 非空集合才寫入（空/None 一律不寫＝全啟用）
        item["disabled_sources"] = sorted(new_disabled)

    from boto3.dynamodb.conditions import Attr  # 延遲匯入，同 boto3 lazy 慣例
    from botocore.exceptions import ClientError

    # CAS 條件依 expected_version 分兩支，不再用 OR 合併（vp-eng review
    # MEDIUM-3/LOW-6）：`== 0` 代表呼叫端讀到「item 不存在」，只接受
    # `attribute_not_exists`；`> 0` 代表讀到某個既有 version，只接受
    # `version` 精確相等——舊版 OR 寫法會讓「對一張空表傳任意正數
    # expected_version」也因 `not_exists()` 分支通過而誤放行，語意錯誤。
    condition = (
        Attr("version").not_exists()
        if expected_version == 0
        else Attr("version").eq(expected_version)
    )
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
    if "hermes_autonomy_enabled" in changes:
        change_entries.append(
            {"field": "hermes_autonomy_enabled", "old": current.hermes_autonomy_enabled, "new": new_autonomy}
        )
    if "live_token" in changes:
        old_masked, new_masked = _mask_token_change(
            current.live_token_hash is not None, changes["live_token"], new_last4
        )
        change_entries.append({"field": "live_token", "old": old_masked, "new": new_masked})
    if "disabled_sources" in changes:
        change_entries.append({
            "field": "disabled_sources",
            "old": sorted(current.disabled_sources) if current.disabled_sources else [],
            "new": sorted(new_disabled) if new_disabled else [],
        })

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
        hermes_autonomy_enabled=new_autonomy,
        live_token_hash=new_hash,
        live_token_last4=new_last4,
        disabled_sources=new_disabled,
        version=version_to,
        updated_at=now_iso,
        updated_by=actor,
        exists=True,
        version_corrupt=False,
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
