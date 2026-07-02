"""排程執行紀錄（`scripts/fetch_scheduler.py` 收尾寫入的輕量 run record）。

Phase3：`/status` 要顯示「最近排程執行」，需要一個跨 run 持久化的地方記錄
`fetch_scheduler.py` 每次批次跑完的摘要（時間、目標數、成功數、失敗清單）。
比照 `ledger.py`（成本帳本）同一套雙 backend fallback 慣例：

  - `SchedulerRunLog`：最小介面（`append` / `read_all`），呼叫端只依賴介面，
    換 backend 不用改呼叫碼。
  - `JsonlSchedulerRunLog`：append-only JSONL 檔案，離線/測試/開發預設。
  - `DynamoDBSchedulerRunLog`：線上持久用實作（本檔只寫 code + mock 測試，
    **不打真 AWS、不建表**——真建表 + IAM 權限由 CEO 另立步驟完成，比照
    `DynamoDBLedger`/`DynamoDBCache` 那兩次）。
  - `get_scheduler_run_log()` 依 env `SCHEDULER_RUN_LOG_BACKEND`（jsonl|dynamodb，
    預設 jsonl，同 `ledger.py::get_ledger()` 的預設）選 backend。
  - `append_scheduler_run()`：寫入失敗**只印 stderr 警告，絕不 raise**——這是
    `fetch_scheduler.py` 收尾的旁路記錄，寫入失敗不能讓排程 exit code 被誤判
    成「這輪抓取失敗」（那要看 `run_once()` 自己算出的 `failures` 清單，語意
    上是分開的兩件事：run log 只是「有沒有留下一筆執行摘要」）。
  - `get_last_scheduler_run()`：`/status` 用的唯讀查詢，任何 backend 失敗一律
    降級回 `None`（或退回本地 JSONL），不拋例外——`/status` 頁面必須永遠能
    顯示，讀不到就顯示「尚無紀錄」。
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


def _default_run_log_path() -> Path:
    """動態算出預設 run log 路徑（每次呼叫都重讀 env），供測試用
    `monkeypatch.setenv()` 在建立 `JsonlSchedulerRunLog()` 之前覆寫，立即生效
    （同 `ledger.py::_default_ledger_path()` 慣例）。"""
    home = Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2])))
    return Path(
        os.getenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(home / "out" / "scheduler_runs.jsonl"))
    )


class SchedulerRunLog(ABC):
    """排程 run record 最小介面：append-only 寫入 + 讀出全部紀錄。"""

    @abstractmethod
    def append(self, record: dict[str, Any]) -> None:
        """寫入一筆 run 記錄（append-only，不可修改/刪除既有紀錄）。"""

    @abstractmethod
    def read_all(self) -> list[dict[str, Any]]:
        """讀出全部歷史紀錄。"""

    def latest(self) -> dict[str, Any] | None:
        """依 `ts` 字串排序取最新一筆；無紀錄回 `None`。

        ⚠️ 這是**未優化的參考實作**（`read_all()` 掃全表再取 max）——只給沒有
        專用「latest 指標」的假想 backend 當退路用。`JsonlSchedulerRunLog` /
        `DynamoDBSchedulerRunLog` 兩個實際 backend 都**必須 override 成 O(1)
        常數成本**（維護一份獨立更新的 latest 指標，append 時比較 `ts` 決定
        是否覆寫），不可讓 `/status` 的「最近排程執行」查詢隨排程歷史筆數線性
        增長（見兩個子類別各自的 `latest()` docstring）。"""
        records = self.read_all()
        if not records:
            return None
        return max(records, key=lambda r: str(r.get("ts", "")))

    def recent(self, n: int = 30) -> list[dict[str, Any]]:
        """依 `ts` 字串排序取最近 `n` 筆（新到舊）；無紀錄回空清單。

        ⚠️ 這是**未優化的參考實作**（`read_all()` 掃全表再排序取前 n 筆）——
        同 `latest()` 慣例，只給沒有專用「recent window」結構的假想 backend
        當退路用。`JsonlSchedulerRunLog` 必須 override 成 O(1)-相容（讀一份
        獨立維護、bounded 大小的 recent window 檔，不掃全歷史），供
        `/status`「連接器用量」表彙總近期 `source_calls` 用（見階段2）。"""
        records = sorted(self.read_all(), key=lambda r: str(r.get("ts", "")), reverse=True)
        return records[:n]


# `JsonlSchedulerRunLog`/`DynamoDBSchedulerRunLog` 的 recent-window 大小：只保留
# 最近 N 筆排程 run record 供 `/status`「連接器用量」彙總用（見 `recent()`）。
# ⚠️ 這是「最近 N 次排程執行」的視窗，不是嚴格的日曆 30 天——排程 cron 間隔可
# 調整，N 筆不保證恰好對應 30 個日曆天，UI 顯示文案應誠實標「最近 N 次排程執行」
# 而非「近 30 天」，避免無根據宣稱（見 web.py `_render_connector_usage_table`）。
RECENT_WINDOW_SIZE = 30


class JsonlSchedulerRunLog(SchedulerRunLog):
    """Append-only JSONL 檔案（離線/測試/開發預設）。

    ⚠️ 本機/單容器持久，EC2 重建即失——線上長期持久見 `DynamoDBSchedulerRunLog`。

    效能設計：`append()` 除了寫入完整歷史（append-only `self.path`），還會
    同步維護一份**獨立的單筆 latest 指標檔**（`self._latest_path`，原子寫
    temp file + `os.replace`，同 `JsonCacheBackend` 慣例）。`latest()` 只讀
    這份單筆檔案，**不掃描/不讀取完整歷史**——查詢成本恆為 O(1)，不隨排程
    歷史筆數增長（history 檔本身可以無限增長，但沒有任何讀取路徑會整份掃
    它；`read_all()` 仍會整份讀，只給匯出/除錯等離線用途用，不在 `/status`
    的熱路徑上）。

    ⚠️ 併發安全：維護 latest 指標是「讀目前指標 → 比較 ts → （可能）覆
    寫」的 compare-and-set，`os.replace` 只保證單次寫入本身原子，**不保證
    整個讀-比較-寫序列不被打斷**——兩個重疊排程各自進入這段臨界區時，較
    新的一筆若先寫完，較舊的一筆若照著它自己更早讀到的（尚未看到較新一
    筆的）舊狀態判斷「該覆寫」，仍可能把指標蓋回舊值（lost update）。因
    此整個 compare-and-set 用 `fcntl.flock` 包住（見 `_update_latest_pointer_locked`），
    跨行程/跨執行緒序列化，確保最終指標不會倒退。
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_run_log_path()

    @property
    def _latest_path(self) -> Path:
        return self.path.with_name(self.path.name + ".latest.json")

    @property
    def _latest_lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".latest.lock")

    @property
    def _recent_path(self) -> Path:
        """階段2：bounded「最近 N 筆」window 檔（見 `RECENT_WINDOW_SIZE`），
        供 `recent()` 讀取。跟 `_latest_path` 是姊妹檔，同一把鎖
        （`_latest_lock_path`）保護，不另開一把鎖——`append()` 只有一次
        機會更新兩者，用同一把鎖序列化即可，不需要額外鎖粒度。"""
        return self.path.with_name(self.path.name + ".recent.json")

    def _read_latest_pointer(self) -> dict[str, Any] | None:
        if not self._latest_path.exists():
            return None
        try:
            data = json.loads(self._latest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def _write_json_atomic(self, path: Path, payload: Any) -> None:
        """原子寫入單一 JSON 檔（temp file + `os.replace`），`_latest_path`／
        `_recent_path` 共用此輔助，避免重複實作同一套「寫 temp → replace」邏輯。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _write_latest_pointer(self, record: dict[str, Any]) -> None:
        self._write_json_atomic(self._latest_path, record)

    def _read_recent_window(self) -> list[dict[str, Any]]:
        if not self._recent_path.exists():
            return []
        try:
            data = json.loads(self._recent_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []

    def _update_pointers_locked(self, record: dict[str, Any]) -> None:
        """原子 compare-and-set + window 更新：`fcntl.flock` 包住「讀目前指標
        → 比較 ts → （可能）覆寫 latest」與「把新記錄併入 recent window
        （依 ts 排序、截斷到 `RECENT_WINDOW_SIZE` 筆）」整段臨界區，跨行程/
        跨執行緒序列化，避免兩個重疊排程交錯造成指標倒退或 window 漏記
        （見類別 docstring）。

        latest 指標只在新記錄的 `ts` 嚴格大於目前指標時才覆寫（同
        `max(records, key=ts)` 對平手取先出現者的既有語意）；recent window
        則一律把新記錄併入再依 ts 排序截斷——window 本身有限大小
        （`RECENT_WINDOW_SIZE`），用來源不需要「只新不舊」的嚴格單調語意，
        重跑/亂序寫入頂多讓 window 內順序重排，不影響正確性。"""
        self._latest_lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._latest_lock_path, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                current = self._read_latest_pointer()
                if current is None or str(record.get("ts", "")) > str(current.get("ts", "")):
                    self._write_latest_pointer(record)

                window = self._read_recent_window()
                window.append(record)
                window.sort(key=lambda r: str(r.get("ts", "")), reverse=True)
                window = window[:RECENT_WINDOW_SIZE]
                self._write_json_atomic(self._recent_path, window)
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()

        self._update_pointers_locked(record)

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # 損毀行跳過，不讓整份 log 讀取失敗
            if isinstance(rec, dict):
                records.append(rec)
        return records

    def latest(self) -> dict[str, Any] | None:
        """O(1)：只讀 `self._latest_path` 單筆指標檔，不呼叫 `read_all()`、
        不掃描歷史檔案。指標檔遺失/損毀視為「尚無紀錄」（`/status` 會顯示
        提示文字），不退回全表掃描以維持常數成本保證。"""
        return self._read_latest_pointer()

    def recent(self, n: int = RECENT_WINDOW_SIZE) -> list[dict[str, Any]]:
        """O(1)-相容：只讀 `self._recent_path` 維護的 bounded window（append
        時同步更新，見 `_update_pointers_locked`），不呼叫 `read_all()`、不
        掃描完整歷史——查詢成本恆為 `min(n, RECENT_WINDOW_SIZE)`，不隨排程
        歷史總筆數增長。`n` 大於視窗實際大小時只能拿到視窗內已有的筆數
        （不會回頭去掃歷史補齊；`n` 大於 `RECENT_WINDOW_SIZE` 時同理，window
        本身就只保留最近 `RECENT_WINDOW_SIZE` 筆）。視窗檔遺失/損毀視為
        「尚無紀錄」（回空清單），不退回全表掃描。"""
        window = self._read_recent_window()
        return window[:n]


class DynamoDBSchedulerRunLog(SchedulerRunLog):
    """線上持久用 backend（DynamoDB 實作，比照 `ledger.py::DynamoDBLedger` 慣例）。

    ⚠️ 本 repo 端（開發/CI）**不打真 AWS**：`__init__` 只讀 env、不連線，
    `boto3` resource/Table 一律 lazy 建立（第一次真的 `append`/`read_all` 才建），
    確保沒有 AWS 憑證、表也還沒建的環境下，**建構本類別不會炸**。

    真表建立（PK=`run_id`、SK=`ts`）+ IAM 權限（`dynamodb:PutItem`/
    `dynamodb:GetItem`/`dynamodb:Scan`）+ 生產環境切
    `SCHEDULER_RUN_LOG_BACKEND=dynamodb` 由 CEO 另立步驟完成，本檔不涉及、
    不真的建表。

    效能設計：`append()` 除了寫入完整歷史記錄，還會用固定 Key
    （`run_id="__latest__"`, `ts="__latest__"`）額外 `put_item` 一份「latest
    指標」（每次覆寫同一筆，只在新記錄 `ts` 嚴格較新時才覆寫，比照
    `JsonlSchedulerRunLog` 語意）。`latest()` 只對這個固定 Key 做**單筆
    `GetItem`**，**絕不 `Scan`**——查詢成本恆為 O(1)，不隨排程歷史筆數增長。
    `read_all()` 仍會整份 `Scan`，只給匯出/除錯等離線用途用，不在 `/status`
    的熱路徑上。

    ⚠️ 併發安全：維護 latest 指標**不能**用「先 `GetItem` 比較、再無條件
    `PutItem`」——兩個重疊排程各自 `GetItem` 到同一份舊指標後，較新的一筆
    先寫完，較舊的一筆仍照著它自己更早讀到的舊狀態判斷「該覆寫」，會把
    指標蓋回舊值（lost update / read-compare-write 競態）。因此改用**條件
    式 `PutItem`**（`ConditionExpression`：目前指標不存在，或其 `source_ts`
    嚴格小於這次要寫入的值），把「比較」跟「覆寫」在 DynamoDB 伺服器端合
    成單一原子操作；`ConditionalCheckFailedException` 代表已有更新（或相
    等）的指標存在，直接忽略、不當錯誤。
    """

    _LATEST_KEY = {"run_id": "__latest__", "ts": "__latest__"}

    def __init__(self, table_name: str | None = None, region: str | None = None):
        self.table_name = table_name or os.getenv(
            "TRUSTFORGE_SCHEDULER_RUN_TABLE", "trustforge-scheduler-runs"
        )
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._table: Any = None  # lazy：建構本身不連 AWS

    def _get_table(self) -> Any:
        if self._table is None:
            import boto3  # 延遲匯入：建構/未啟用 dynamodb backend 時不需要憑證

            self._table = boto3.resource("dynamodb", region_name=self.region).Table(
                self.table_name
            )
        return self._table

    @staticmethod
    def _to_decimal(value: Any) -> Any:
        """遞迴把 float 轉 `Decimal`（DynamoDB 不接受 float），巢狀 dict/list 一併處理。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, dict):
            return {k: DynamoDBSchedulerRunLog._to_decimal(v) for k, v in value.items()}
        if isinstance(value, list):
            return [DynamoDBSchedulerRunLog._to_decimal(v) for v in value]
        return value

    @staticmethod
    def _from_decimal(value: Any) -> Any:
        """讀出時把 `Decimal` 轉回 Python number（整數值轉 `int`，否則 `float`），
        格式對齊 `JsonlSchedulerRunLog`（原生 JSON int/float）。"""
        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                return int(value)
            return float(value)
        if isinstance(value, dict):
            return {k: DynamoDBSchedulerRunLog._from_decimal(v) for k, v in value.items()}
        if isinstance(value, list):
            return [DynamoDBSchedulerRunLog._from_decimal(v) for v in value]
        return value

    def append(self, record: dict[str, Any]) -> None:
        table = self._get_table()
        item = dict(record)
        ts = item.get("ts") or datetime.now(timezone.utc).isoformat()
        run_id = item.get("run_id") or uuid.uuid4().hex
        item["ts"] = str(ts)
        item["run_id"] = str(run_id)
        table.put_item(Item=self._to_decimal(item))
        self._update_latest_pointer(table, item)

    def _update_latest_pointer(self, table: Any, item: dict[str, Any]) -> None:
        """維護固定 Key 的 latest 指標。真正的 key 屬性（`run_id`/`ts`）改成
        固定值，原始值另存 `source_run_id`/`source_ts` 欄位供 `latest()`
        還原。

        用**條件式 `PutItem`**做原子 compare-and-set，取代「先 `GetItem`
        比較、再無條件 `PutItem`」——後者在兩個重疊排程交錯時，較舊的一筆
        可能照著自己更早讀到的舊狀態把指標蓋回去（見類別 docstring）。
        `ConditionExpression` 讓「目前指標不存在，或其 `source_ts` 嚴格小於
        這次要寫入的值」與「覆寫」在 DynamoDB 伺服器端合成單一原子操作，
        不會有 read-compare-write 之間的競態窗口。"""
        from boto3.dynamodb.conditions import Attr  # 延遲匯入，同 boto3 lazy 慣例
        from botocore.exceptions import ClientError

        pointer = dict(item)
        pointer["source_run_id"] = pointer.pop("run_id")
        pointer["source_ts"] = pointer.pop("ts")
        pointer["run_id"] = self._LATEST_KEY["run_id"]
        pointer["ts"] = self._LATEST_KEY["ts"]

        condition = Attr("source_ts").not_exists() | Attr("source_ts").lt(pointer["source_ts"])
        try:
            table.put_item(Item=self._to_decimal(pointer), ConditionExpression=condition)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return  # 已有更新（或相等）的指標存在，忽略，不當錯誤
            raise

    def latest(self) -> dict[str, Any] | None:
        """O(1)：對固定 Key 做單筆 `GetItem`，**絕不 `Scan`**——查詢成本不隨
        排程歷史筆數增長。指標項目不存在（表剛建/尚未有任何 append）視為
        「尚無紀錄」。"""
        resp = self._get_table().get_item(Key=self._LATEST_KEY)
        item = resp.get("Item")
        if not item:
            return None
        converted = self._from_decimal(item)
        if not isinstance(converted, dict):
            return None
        record = dict(converted)
        record["run_id"] = record.pop("source_run_id", record.get("run_id"))
        record["ts"] = record.pop("source_ts", record.get("ts"))
        return record

    def read_all(self) -> list[dict[str, Any]]:
        """整份 `Scan` 讀出全部歷史紀錄（含 latest 指標項目本身，已用固定
        Key 濾掉）——只給匯出/除錯等離線用途，**不在 `/status` 的熱路徑上**
        （`/status` 走 `latest()`，恆為 O(1)）。"""
        table = self._get_table()
        records: list[dict[str, Any]] = []
        scan_kwargs: dict[str, Any] = {}
        while True:
            resp = table.scan(**scan_kwargs)
            for item in resp.get("Items", []) or []:
                if item.get("run_id") == self._LATEST_KEY["run_id"] and item.get(
                    "ts"
                ) == self._LATEST_KEY["ts"]:
                    continue  # 跳過 latest 指標項目，只回傳真正的歷史記錄
                converted = self._from_decimal(item)
                if isinstance(converted, dict):
                    records.append(converted)
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
        return records

    # `recent()`：本 PR 範圍內不覆寫，沿用 `SchedulerRunLog.recent()` 的未優化
    # 參考實作（`read_all()` 全 Scan 再排序取前 n 筆）——誠實標記：這條路徑
    # **不是** O(1)，成本隨歷史筆數增長。`SCHEDULER_RUN_LOG_BACKEND` 預設
    # `jsonl`（`JsonlSchedulerRunLog.recent()` 才是真正 O(1) 的 window 讀取，
    # 見該類別實作），DynamoDB backend 要先由 CEO 另立步驟建表、切換 env 才會
    # 生效；屆時若要在 DynamoDB backend 上維持 O(1)，應比照 `latest()` 用
    # 固定 Key + 條件式 `PutItem` 維護一份獨立的 recent-window 項目，不在本
    # PR（成本會計階段2）範圍內，先誠實留白，不假裝已經優化。


def get_scheduler_run_log() -> SchedulerRunLog:
    """依 env `SCHEDULER_RUN_LOG_BACKEND`（jsonl|dynamodb，**預設 jsonl**，同
    `ledger.py::get_ledger()`）選 backend。"""
    backend = os.getenv("SCHEDULER_RUN_LOG_BACKEND", "jsonl").strip().lower()
    if backend == "dynamodb":
        return DynamoDBSchedulerRunLog()
    return JsonlSchedulerRunLog()


def append_scheduler_run(
    record: dict[str, Any], log: SchedulerRunLog | None = None
) -> None:
    """寫入一筆排程 run record；`log` 未提供時用 `get_scheduler_run_log()`。

    ⚠️ 任何 backend 失敗（缺憑證/建表、路徑不可寫）一律吞掉例外、只印 stderr
    警告，**絕不往上拋**——`scripts/fetch_scheduler.py` 的 exit code 語意由
    `run_once()` 算出的 `failures`（真呼叫/cache 寫入是否成功）決定，run log
    本身只是旁路的執行摘要，寫入失敗不該連帶讓排程被誤判成「這輪抓取失敗」。
    fallback 也包 try/except，且若 target 本身已是 `JsonlSchedulerRunLog` 就
    不重試同一路徑（同路徑必再失敗，重試沒有意義）。
    """
    if not record.get("run_id"):
        record["run_id"] = uuid.uuid4().hex
    if not record.get("ts"):
        record["ts"] = datetime.now(timezone.utc).isoformat()

    target = log if log is not None else get_scheduler_run_log()
    try:
        target.append(record)
        return
    except Exception as exc:
        print(
            f"[scheduler_log] WARNING: append 失敗（backend={type(target).__name__}）：{exc}",
            file=sys.stderr,
        )

    if isinstance(target, JsonlSchedulerRunLog):
        return  # 同一顆 JsonlSchedulerRunLog 剛失敗，換個新實例打同路徑必再失敗，不重試

    try:
        JsonlSchedulerRunLog().append(record)
    except Exception as exc:
        print(
            f"[scheduler_log] WARNING: fallback JsonlSchedulerRunLog append 仍失敗：{exc}",
            file=sys.stderr,
        )


def get_last_scheduler_run() -> dict[str, Any] | None:
    """讀「最近一次排程執行」記錄，供 `/status` 顯示。

    ⚠️ 唯讀，任何 backend 失敗（DynamoDB 缺憑證/表未建/網路問題）一律降級：
    絕不拋例外，`/status` 頁面必須永遠能顯示，讀不到就顯示「尚無紀錄」。

    ⚠️ split-brain：`append_scheduler_run()` 在 primary（如 DynamoDB）寫入
    latest 指標**失敗**時，會 fallback 把整筆記錄寫進本地
    `JsonlSchedulerRunLog`——這代表「真正最新的一筆」有可能只存在 fallback
    裡，而 primary 讀取本身**沒有拋例外**、只是回傳它自己那份還沒更新到
    的舊指標。如果只在 `primary.latest()` 拋例外時才去讀 fallback，就會被
    這個「primary 讀得到但內容是舊的」情境騙過，隱藏 fallback 裡更新的一
    筆。因此**兩邊都讀**（primary + 本地 JSONL fallback，皆為 O(1) 單筆
    `latest()`，不掃全表），依 `ts` 字串比較取較新者；任一邊讀失敗就退化
    用另一邊；兩者都沒有才回 `None`。

    當 primary 本身就是 `JsonlSchedulerRunLog`（預設 `SCHEDULER_RUN_LOG_BACKEND
    =jsonl`）時，primary 與 fallback 是同一份資料，不必重讀第二次。
    """
    primary = get_scheduler_run_log()

    primary_record: dict[str, Any] | None = None
    try:
        primary_record = primary.latest()
    except Exception as exc:
        print(f"[scheduler_log] WARNING: 讀取失敗（backend={type(primary).__name__}）："
              f"{exc}", file=sys.stderr)

    if isinstance(primary, JsonlSchedulerRunLog):
        return primary_record  # primary 已是本地 JSONL，跟 fallback 同一份資料

    fallback_record: dict[str, Any] | None = None
    try:
        fallback_record = JsonlSchedulerRunLog().latest()
    except Exception as exc:
        print(f"[scheduler_log] WARNING: fallback JsonlSchedulerRunLog 讀取仍失敗："
              f"{exc}", file=sys.stderr)

    if primary_record is None:
        return fallback_record
    if fallback_record is None:
        return primary_record
    if str(fallback_record.get("ts", "")) > str(primary_record.get("ts", "")):
        return fallback_record
    return primary_record
