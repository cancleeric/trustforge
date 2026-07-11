"""#51/#87 延伸：跨行程 / 跨路由的 durable idempotency lease（D2.5）。

背景：`web.py::_dedup_analyze_call` 的 single-flight（#51/#87）只在「單一
process 內」把同一把 dedup key 的併發請求 coalesce 成一次 `compute()`——
多實例部署（多 process / 多機器）時，不同 process 各自獨立維護自己的
`_analyze_dedup_inflight` 字典，彼此互不可見：兩個實例同時收到同一組
參數的 `/api/analyze` 請求，會各自成為自己 process 裡的 leader、各自真
打一次 Bedrock，重複計費（#9 護欄的每日 cap 是「累計花費」上限，擋不住
「同一件事被兩個實例各做一遍」這種語意重複）。

本模組補上**持久租約（durable lease）**層：每把 dedup key 在「成為 leader、
準備 `compute()`」之前，先去一個**跨實例共享**的 backend（DynamoDB /
本地 JSON fallback）原子地 `try_acquire` 一把租約；只有拿到租約的那個
實例真的 `compute()`，其餘實例看到租約已被**別的 process** 佔用 → 回可
重試的 429/503（避免重複計費）。leader 完成（成功或失敗）後 `release`
租約——因此**循序**進來的相同請求（前一次已結束、租約已釋放）仍會 fresh
重新計算，不會被 15 分鐘 TTL 卡住（#51 Round 12 移除 60s 結果快取的初衷
不變）。

owner 編碼為 `"<pid>:<uuid>"`：
  - **同 process 的 stale-leader 接手**（Round 15 機制）視為「自己人」——
    同一 process 內 in-memory single-flight 已保證不會真的雙重 compute
    到危害程度（舊 leader 仍在跑、新 leader 接手重算，舊 leader 結果會被
    in-memory fencing 擋下不覆寫新結果），故允許同 pid 重新取得租約，不
    阻塞 Round 15 的接手語意。
  - **不同 process**（多實例）持有 → 嚴格擋下，回 429，避免跨實例重複
    計費。
  release 用**完整** owner（pid+uuid）比對，確保接手後的舊 leader thread
  結束時不會誤刪新 leader 的租約。

截止感知（deadline-aware）：租約帶 TTL（預設 15 分鐘，
`analyze_lease_ttl_seconds()`）——leader 若真的 crash / 被殺，租約在 TTL
後自動過期，下一個請求可以接手（self-healing），不會永久卡死；同時這
個 TTL 也就是「重複計費風險窗口」的上界。

backend 選擇（env `TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND`）：
  - `dynamodb`：跨實例共享（多實例部署必須）；用條件式 `PutItem`（owner
    pid 比對 / 過期 / 不存在三態）做原子 acquire，TTL 走 DynamoDB 原生
    ttl 屬性。
  - `json`（預設，dev/CI/單實例）：本地 JSON 檔案 + `fcntl.flock`；單
    實例下與 in-memory single-flight 重疊，但提供「process 重啟後仍記得
    哪把 key 正在算」的韌性（重啟後不會立刻對同一 key 重複 compute）。
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

# 15 分鐘租約 TTL —— D2.5「15 分鐘 deadline-aware」的具體上界：
# 重複計費風險窗口 / 單一 key 最多被計算一次的間隔上界（正常結束會立即
# release，此 TTL 僅作 crash 後 self-healing 的上界）。
_ANALYZE_LEASE_TTL_SECONDS = 15 * 60


def analyze_lease_ttl_seconds() -> int:
    """租約 TTL（秒）。另可經 env `TRUSTFORGE_ANALYZE_LEASE_TTL_SECONDS`
    覆寫（決賽現場若要調整 deadline 窗口）。"""
    raw = os.getenv("TRUSTFORGE_ANALYZE_LEASE_TTL_SECONDS")
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return _ANALYZE_LEASE_TTL_SECONDS


def new_owner_id() -> str:
    """每次成為 leader 都產生一個全新 owner id，編碼為 `"<pid>:<uuid>"`：
    pid 用於「同 process 接手」判定（見模組頂部），uuid 用於精確 release
    比對（避免接手後的舊 leader 誤刪新 leader 租約）。"""
    return f"{os.getpid()}:{uuid.uuid4().hex}"


_log = logging.getLogger(__name__)


def _owner_pid(owner_id: str) -> str:
    return owner_id.split(":", 1)[0]


class LeaseBackend(ABC):
    """跨實例 durable 租約最小介面。"""

    @abstractmethod
    def try_acquire(self, key: str, owner_id: str, ttl_seconds: int) -> bool:
        """原子地嘗試取得 `key` 的租約（owner=owner_id）。
        - 不存在 / 已過期 → 取得成功（True）。
        - 被**別的 process**（pid 不同）持有且未過期 → 失敗（False）。
        - 被**同一 process**（pid 相同）持有 → 視為「自己人接手」，重新
          取得（刷新 expires_at）並回 True（支援 Round 15 stale 接手）。"""

    @abstractmethod
    def release(self, key: str, owner_id: str) -> None:
        """釋放 `key` 的租約——只有持有者（owner 完全相符，pid+uuid）才真的
        刪除，避免誤刪別的實例 / 接手後舊 leader 的租約。釋放失敗（backend
        故障）只印 warning、不 raise（reconcile 失敗不該讓分析炸）。"""

    @abstractmethod
    def is_held(self, key: str) -> bool:
        """`key` 目前是否有（未過期）租約被持有。"""


class JsonLeaseBackend(LeaseBackend):
    """本地 JSON 檔案實作，靠 `fcntl.flock` 包住整段 read-modify-write
    保證跨行程原子（單機多實例也安全）。預設 backend（dev/CI/單實例）。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else self._default_path()

    @staticmethod
    def _default_path() -> Path:
        home = Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2])))
        return Path(os.getenv("TRUSTFORGE_LEASE_PATH", str(home / "out" / "analyze_leases.json")))

    @property
    def _lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".lock")

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def try_acquire(self, key: str, owner_id: str, ttl_seconds: int) -> bool:
        # codex ⑤ fail-safe 可用性：磁碟滿 / 權限不足等會讓 `_write` 拋
        # `OSError`，若直接冒泡，`web._dedup_analyze_call` 的 lease 段（在
        # `try_acquire` 與「拿到租約才 compute」之間）沒有 catch，異常會一路
        # 衝出、導致剛建立的 `my_flight` 永遠不被 pop → in-memory flight 洩漏
        # （後續同 key 請求永遠 coalesce 進一條死掉的 leader）。這裡把 OSError
        # 視同「拿不到租約」回 `False`，由呼叫端既有的 `if not _lease_acquired`
        # 分支清理 flight 並回可重試 429（fail-safe，不 crash 也不洩漏）。
        try:
            return self._try_acquire_locked(key, owner_id, ttl_seconds)
        except OSError:
            _log.warning(
                "[idempotency_lease] JsonLeaseBackend.try_acquire 寫入失敗（磁碟滿/權限不足），"
                "fail-safe 視同未取得租約（本請求不 compute，回 429 由呼叫端處理）",
                exc_info=True,
            )
            return False

    def _try_acquire_locked(self, key: str, owner_id: str, ttl_seconds: int) -> bool:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                now = time.time()
                data = self._load()
                existing = data.get(key)
                if existing is not None and now < float(existing.get("expires_at", 0.0)):
                    # 仍被持有：同 process（pid 相同）→ 自己人接手，允許重取；
                    # 別的 process（pid 不同）→ 擋下。
                    if _owner_pid(existing.get("owner_id", "")) != _owner_pid(owner_id):
                        return False
                data[key] = {
                    "owner_id": owner_id,
                    "acquired_at": now,
                    "expires_at": now + ttl_seconds,
                }
                self._write(data)
                return True
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def release(self, key: str, owner_id: str) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                data = self._load()
                existing = data.get(key)
                # 精確比對完整 owner（pid+uuid）：接手後的舊 leader 的 uuid
                # 與新 leader 不同，不會誤刪新 leader 的租約。
                if existing is not None and existing.get("owner_id") == owner_id:
                    data.pop(key, None)
                    self._write(data)
            except Exception:  # noqa: BLE001 — reconcile 失敗不 raise
                pass
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def is_held(self, key: str) -> bool:
        existing = self._load().get(key)
        if existing is None:
            return False
        return time.time() < float(existing.get("expires_at", 0.0))


class DynamoDBLeaseBackend(LeaseBackend):
    """跨實例共享租約（多實例部署必須）。條件式 `PutItem` 原子 acquire
    （不存在 / 過期 / 同 pid 接手三態），`DeleteItem`（完整 owner 比對）
    原子 release，TTL 走 DynamoDB 原生 ttl。建構本身不連 AWS（lazy table）。"""

    def __init__(self, table_name: str | None = None, region: str | None = None):
        self.table_name = table_name or os.getenv("TRUSTFORGE_LEASE_TABLE", "trustforge-analyze-leases")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._table: Any = None

    def _get_table(self):
        if self._table is None:
            import boto3  # 延遲匯入：未啟用 dynamodb backend 時不需 boto3

            self._table = boto3.resource("dynamodb", region_name=self.region).Table(self.table_name)
        return self._table

    def try_acquire(self, key: str, owner_id: str, ttl_seconds: int) -> bool:
        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError
        from decimal import Decimal

        now = time.time()
        expires_at = int(now + ttl_seconds)
        my_pid = _owner_pid(owner_id)
        try:
            self._get_table().put_item(
                Item={
                    "lease_key": key,
                    "owner_id": owner_id,
                    "owner_pid": my_pid,
                    "expires_at": Decimal(str(expires_at)),
                    "ttl": expires_at,
                },
                # 不存在，或既有租約已過期，或同 process 自己人接手 → 可取得
                ConditionExpression=(
                    Attr("lease_key").not_exists()
                    | Attr("expires_at").lt(Decimal(str(int(now))))
                    | Attr("owner_pid").eq(my_pid)
                ),
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            # codex ⑤ fail-safe 可用性（與 JsonLeaseBackend 的 OSError 處理同
            # 源）：ProvisionedThroughputExceeded / 網路超時等非條件衝突的
            # ClientError 若直接冒泡，`web._dedup_analyze_call` 的 lease 段（在
            # `try_acquire` 與「拿到租約才 compute」之間）沒有 catch，異常會
            # 一路衝出、導致剛建立的 `my_flight` 永遠不被 pop → in-memory flight
            # 洩漏（後續同 key 請求永遠 coalesce 進一條死掉的 leader）。這裡把
            # 這類非「被別人持有」的錯誤視同「拿不到租約」回 `False`，由呼叫端
            # 既有的 `if not _lease_acquired` 分支清理 flight 並回可重試 429
            # （fail-safe，不 crash 也不洩漏）。注意：成功取得租約的 `return True`
            # 在 try 內、錯誤才落進 except，絕不會把成功誤判為失敗。
            _log.warning(
                "[idempotency_lease] DynamoDBLeaseBackend.try_acquire 非條件衝突錯誤（"
                "ProvisionedThroughputExceeded/網路超時等），fail-safe 視同未取得租約"
                "（本請求不 compute，回 429 由呼叫端處理）",
                exc_info=True,
            )
            return False
        except Exception:  # noqa: BLE001 — AWS 抖動（非 ClientError 的網路/逾時）同樣 fail-safe
            _log.warning(
                "[idempotency_lease] DynamoDBLeaseBackend.try_acquire 未預期例外，"
                "fail-safe 視同未取得租約（本請求不 compute，回 429 由呼叫端處理）",
                exc_info=True,
            )
            return False

    def release(self, key: str, owner_id: str) -> None:
        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError

        try:
            self._get_table().delete_item(
                Key={"lease_key": key},
                # 只有完整 owner（pid+uuid）相符才刪得掉，避免誤刪別的實例 /
                # 接手後舊 leader 的租約
                ConditionExpression=Attr("owner_id").eq(owner_id),
            )
        except ClientError:  # noqa: BLE001 — reconcile 失敗不 raise
            pass

    def is_held(self, key: str) -> bool:
        try:
            item = self._get_table().get_item(Key={"lease_key": key}).get("Item")
        except Exception:  # noqa: BLE001
            return False
        if not item:
            return False
        return time.time() < float(item.get("expires_at", 0.0))


_LEASE_BACKEND: LeaseBackend | None = None


def get_lease_backend() -> LeaseBackend:
    """依 env `TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND`（`dynamodb`|`json`，
    預設 `json`）選 backend。回傳 process 級單例。"""
    global _LEASE_BACKEND
    if _LEASE_BACKEND is None:
        backend = os.getenv("TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND", "json").strip().lower()
        if backend == "dynamodb":
            _LEASE_BACKEND = DynamoDBLeaseBackend()
        else:
            _LEASE_BACKEND = JsonLeaseBackend()
    return _LEASE_BACKEND


def set_lease_backend(backend: LeaseBackend | None) -> None:
    """測試 / 注入用：覆寫（或重置為 `None` 讓下次 `get_lease_backend()`
    重新依 env 選擇）process 級單例。"""
    global _LEASE_BACKEND
    _LEASE_BACKEND = backend
