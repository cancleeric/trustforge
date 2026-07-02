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

import json
import os
import sys
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
        """依 `ts` 字串排序取最新一筆；無紀錄回 `None`。"""
        records = self.read_all()
        if not records:
            return None
        return max(records, key=lambda r: str(r.get("ts", "")))


class JsonlSchedulerRunLog(SchedulerRunLog):
    """Append-only JSONL 檔案（離線/測試/開發預設）。

    ⚠️ 本機/單容器持久，EC2 重建即失——線上長期持久見 `DynamoDBSchedulerRunLog`。
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_run_log_path()

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()

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


class DynamoDBSchedulerRunLog(SchedulerRunLog):
    """線上持久用 backend（DynamoDB 實作，比照 `ledger.py::DynamoDBLedger` 慣例）。

    ⚠️ 本 repo 端（開發/CI）**不打真 AWS**：`__init__` 只讀 env、不連線，
    `boto3` resource/Table 一律 lazy 建立（第一次真的 `append`/`read_all` 才建），
    確保沒有 AWS 憑證、表也還沒建的環境下，**建構本類別不會炸**。

    真表建立（PK=`run_id`、SK=`ts`）+ IAM 權限（`dynamodb:PutItem`/
    `dynamodb:Scan`）+ 生產環境切 `SCHEDULER_RUN_LOG_BACKEND=dynamodb` 由 CEO
    另立步驟完成，本檔不涉及、不真的建表。
    """

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
        item = dict(record)
        ts = item.get("ts") or datetime.now(timezone.utc).isoformat()
        run_id = item.get("run_id") or uuid.uuid4().hex
        item["ts"] = str(ts)
        item["run_id"] = str(run_id)
        self._get_table().put_item(Item=self._to_decimal(item))

    def read_all(self) -> list[dict[str, Any]]:
        table = self._get_table()
        records: list[dict[str, Any]] = []
        scan_kwargs: dict[str, Any] = {}
        while True:
            resp = table.scan(**scan_kwargs)
            for item in resp.get("Items", []) or []:
                converted = self._from_decimal(item)
                if isinstance(converted, dict):
                    records.append(converted)
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
        return records


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
    先試主 backend，失敗再試本地 `JsonlSchedulerRunLog`（比照 `cache.py::
    cache_get()` 的 fallback 慣例），兩者都失敗回 `None`——絕不拋例外，
    `/status` 頁面必須永遠能顯示，讀不到就顯示「尚無紀錄」。
    """
    primary = get_scheduler_run_log()
    try:
        return primary.latest()
    except Exception as exc:
        print(f"[scheduler_log] WARNING: 讀取失敗（backend={type(primary).__name__}）："
              f"{exc}", file=sys.stderr)

    if isinstance(primary, JsonlSchedulerRunLog):
        return None  # 同一顆 JsonlSchedulerRunLog 剛失敗，換個新實例打同路徑必再失敗

    try:
        return JsonlSchedulerRunLog().latest()
    except Exception as exc:
        print(f"[scheduler_log] WARNING: fallback JsonlSchedulerRunLog 讀取仍失敗："
              f"{exc}", file=sys.stderr)
        return None
