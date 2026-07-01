"""跨 run 持久化成本帳本（Bedrock LLM 呼叫花費）。

⚠️ 不與 `execlog.py` 混淆：`ExecutionLog` 是「單次 run 內」的執行紀錄
（`llm.cost` 事件、15 分鐘預算追蹤），本檔是「跨 run」累積帳本 —— 每次
`run_agent_pipeline` 收尾把該 run 的成本彙總寫一筆，讓 WebUI `/costs`
能看到歷史所有 run 的累計花費，即使伺服器程序重啟也能重讀（JSONL 檔案
持久化；DynamoDB backend 見下方，可跨機器/跨容器重建也不失）。

Backend 可插拔（CEO 架構決策）：
  - `Ledger`：最小介面（`append` / `read_all`），呼叫端只依賴介面，換 backend
    不用改呼叫碼。
  - `JsonlLedger`：本 PR 唯一實作，append-only JSONL 檔案。離線/測試/開發預設。
    ⚠️ EC2 容器重建即失（非真持久），僅供本機/單機部署與測試用。
  - `DynamoDBLedger`：線上持久用實作（本 PR 只寫 code + mock 測試，**不打真
    AWS、不建表**）。需先建表 + 賦予執行環境（EC2 instance role / Lambda
    execution role）dynamodb:PutItem / dynamodb:Scan 權限，兩者皆完成後才由
    CEO 另立步驟切 `COST_LEDGER_BACKEND=dynamodb` 真正啟用。
  - `get_ledger()` 依 env `COST_LEDGER_BACKEND`（jsonl|dynamodb，預設 jsonl）
    選 backend；`append_run()` 對 DynamoDB 等 backend 呼叫失敗（缺憑證/表未建/
    網路問題）一律 fallback 寫 JsonlLedger，確保帳本永遠可寫、pipeline 不因
    帳本故障而中斷。
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

# ---------------------------------------------------------------------------
# 定價表（USD / 1M tokens）：(輸入單價, 輸出單價)。未知 model_id → 0，不 raise。
# ---------------------------------------------------------------------------
PRICING: dict[str, tuple[float, float]] = {
    "apac.anthropic.claude-haiku-4-5": (1.0, 5.0),
    # 待 AWS 官網確認：競賽現場（8/1）公告正式模型 id 與定價後需更新此暫值。
    "apac.anthropic.claude-sonnet-4-6": (3.0, 15.0),  # 待 AWS 官網確認
}


def estimate_cost(model_id: str | None, tokens_in: int, tokens_out: int) -> float:
    """依 `PRICING` 估算單次呼叫成本（USD）。model_id 為 None/未知 → 0，不 raise。"""
    if not model_id:
        return 0.0
    rate = PRICING.get(model_id)
    if rate is None:
        return 0.0
    in_rate, out_rate = rate
    tokens_in = max(0, int(tokens_in or 0))
    tokens_out = max(0, int(tokens_out or 0))
    cost = tokens_in / 1_000_000 * in_rate + tokens_out / 1_000_000 * out_rate
    return round(cost, 6)


# ---------------------------------------------------------------------------
# 路徑：可用 env 覆寫，預設放 runtime 位置（out/，已在 .gitignore；⚠️ 絕對不要放
# data/ —— 那是 HOYA BIT 官方 OHLCV 受保護資料目錄，不可混入生成產物）。
def _default_ledger_path() -> Path:
    """動態算出預設帳本路徑（每次呼叫都重讀 env，而非匯入當下就凍結）。

    測試/工具可用 `monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", ...)` 在建立
    `JsonlLedger()` 之前覆寫，立即生效——不像模組級常數只在 import 那一刻讀一次 env。
    """
    home = Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2])))
    return Path(os.getenv("TRUSTFORGE_COST_LEDGER_PATH", str(home / "out" / "cost_ledger.jsonl")))


# repo 根目錄：ledger.py 位於 src/trustforge/ledger.py → parents[2] = repo 根。
# 保留這個模組級常數供外部讀取「目前預設路徑長怎樣」，但 `JsonlLedger` 內部一律呼叫
# `_default_ledger_path()` 動態取得，不依賴這個 import 當下的快照值。
DEFAULT_LEDGER_PATH = _default_ledger_path()


class Ledger(ABC):
    """成本帳本最小介面：append-only 寫入 + 讀出全部紀錄。"""

    @abstractmethod
    def append(self, record: dict[str, Any]) -> None:
        """寫入一筆 run 記錄（append-only，不可修改/刪除既有紀錄）。"""

    @abstractmethod
    def read_all(self) -> list[dict[str, Any]]:
        """讀出全部歷史紀錄（依寫入順序）。"""

    def summary(self) -> dict[str, Any]:
        """累計總花費 + 依 model 分組彙總（預設實作；backend 可覆寫做更有效率的版本）。"""
        records = self.read_all()
        total = 0.0
        by_model: dict[str, float] = {}
        for rec in records:
            total += float(rec.get("total_cost_usd", 0.0) or 0.0)
            for call in rec.get("calls", []) or []:
                model = call.get("model") or "offline"
                by_model[model] = by_model.get(model, 0.0) + float(call.get("cost_usd", 0.0) or 0.0)
        return {
            "total_cost_usd": round(total, 6),
            "by_model": {m: round(v, 6) for m, v in by_model.items()},
            "runs": records,
        }


class JsonlLedger(Ledger):
    """Append-only JSONL 檔案帳本（離線/開發預設）。

    ⚠️ 本機/單容器持久，EC2 重建即失 —— 線上長期持久見 `DynamoDBLedger`。
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_ledger_path()

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
                continue  # 損毀行跳過，不讓整個帳本讀取失敗
            if isinstance(rec, dict):
                records.append(rec)
        return records


class DynamoDBLedger(Ledger):
    """線上持久用 backend（DynamoDB 實作）。

    ⚠️ 本 repo 端（開發/CI）**不打真 AWS**：`__init__` 只讀 env、不連線，`boto3`
    resource/Table 一律 lazy 建立（第一次真的 `append`/`read_all` 才建），確保
    沒有 AWS 憑證、表也還沒建的環境下，**建構 `DynamoDBLedger()` 不會炸**。

    真表建立 + IAM 權限（`dynamodb:PutItem` / `dynamodb:Scan`）+ 生產環境切
    `COST_LEDGER_BACKEND=dynamodb` 由 CEO 另立步驟完成，本檔不涉及。

    表結構（CEO gated 建表時採用）：PK=`run_id`（S，全域唯一，見下方）、
    SK=`ts`（S，ISO8601）。`run_id` 由 `append_run()` 在分派給 backend/fallback
    **之前**統一生成（`uuid.uuid4().hex`），確保同一筆記錄無論寫成功或
    fallback 都拿到同一個 id——不再用 `ts+coin` 衍生：同幣同秒兩次執行
    （如 comparison 兩輪、平行 run）`ts` 精度只到秒會相同，`ts+coin` 會
    產生一樣的 PK 互相覆蓋，這裡改成每次呼叫都是全新 uuid，不會碰撞。
    `append()` 本身仍留一道防線（見下方），只在 record 真的沒有 `run_id`
    時才補（理論上經過 `append_run()` 後一定有）。
    DynamoDB 不吃 Python `float`，寫入前遞迴轉 `Decimal`；讀出時再轉回原本的
    數字型別（整數值如 token 數轉回 `int`，非整數值如成本轉回 `float`），
    格式與 `JsonlLedger.read_all()` 保持一致，呼叫端不用分辨 backend。
    """

    def __init__(self, table_name: str | None = None, region: str | None = None):
        self.table_name = table_name or os.getenv(
            "TRUSTFORGE_COST_LEDGER_TABLE", "trustforge-cost-ledger"
        )
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._table: Any = None  # lazy：建構本身不連 AWS

    def _get_table(self) -> Any:
        """lazy 取得 boto3 Table 物件；第一次呼叫才真的碰 AWS SDK。"""
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
            return {k: DynamoDBLedger._to_decimal(v) for k, v in value.items()}
        if isinstance(value, list):
            return [DynamoDBLedger._to_decimal(v) for v in value]
        return value

    @staticmethod
    def _from_decimal(value: Any) -> Any:
        """讀出時把 `Decimal` 轉回 Python number，格式對齊 `JsonlLedger`。

        整數值（如 token 數）轉回 `int`，非整數值（如成本）才轉 `float`——
        DynamoDB 一律用 `Decimal` 存數字，若不分整數/小數會把 `tokens_in=700`
        讀回變成 `700.0`，跟 JsonlLedger（原生 JSON int）不一致。
        """
        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                return int(value)
            return float(value)
        if isinstance(value, dict):
            return {k: DynamoDBLedger._from_decimal(v) for k, v in value.items()}
        if isinstance(value, list):
            return [DynamoDBLedger._from_decimal(v) for v in value]
        return value

    def append(self, record: dict[str, Any]) -> None:
        item = dict(record)
        ts = item.get("ts")
        if not ts:
            ts = datetime.now(timezone.utc).isoformat()
            item["ts"] = ts
        run_id = item.get("run_id")
        if not run_id:
            # 防線：理論上 append_run() 已在分派前生成全域唯一的 uuid run_id，
            # 這裡只防直接呼叫 DynamoDBLedger.append() 略過 append_run 的情況。
            # ⚠️ 不再用 ts+coin 衍生——同幣同秒兩次執行會撞成同一個 PK 互相覆蓋。
            run_id = uuid.uuid4().hex
        item["run_id"] = str(run_id)
        item["ts"] = str(ts)
        self._get_table().put_item(Item=self._to_decimal(item))

    @staticmethod
    def _dedup_key(record: dict[str, Any]) -> str:
        """去重唯一鍵：只靠 `run_id`（`append_run()` 保證每筆都有全域唯一的
        uuid run_id）。

        若 record 完全沒有 `run_id`（理論上不會發生，防禦舊資料/直接呼叫
        backend 略過 append_run 的情況），**不**退回用 `("", ts)` 這種空鍵，
        否則同一秒缺 run_id 的不同記錄（如不同幣）會被誤判成同一筆併掉；
        改用 `id(record)`（物件身分）保證這類記錄互不相撞、各自保留。
        """
        run_id = record.get("run_id")
        if run_id:
            return str(run_id)
        return f"__no_run_id__:{id(record)}"

    def read_all(self) -> list[dict[str, Any]]:
        """scan DynamoDB + 合併 JSONL fallback，去重後依 `(ts, run_id)` 排序回傳。

        `append_run()` 遇到 `put_item` 失敗（outage/缺憑證/表未建）會 fallback
        寫進 `JsonlLedger`（見模組頂部說明）；若這裡只 scan DynamoDB，outage
        期間的記錄雖然還在磁碟上，卻不會出現在 `/costs`。因此一律也讀一次
        `JsonlLedger()`（預設路徑）並與 DynamoDB 結果合併：
          - 去重鍵是 `run_id`（見 `_dedup_key`）；同一筆兩邊都有時（DynamoDB
            事後補寫成功）以 DynamoDB 版本為準。
          - `Scan` 本身無序、跨頁也不保證穩定，最後依 `(ts, run_id)` 排序，
            讓回傳順序與 `JsonlLedger.read_all()`（寫入序）一致、跨請求穩定。
        """
        dynamo_records: list[dict[str, Any]] = []
        table = self._get_table()
        scan_kwargs: dict[str, Any] = {}
        while True:
            resp = table.scan(**scan_kwargs)
            for item in resp.get("Items", []) or []:
                converted = self._from_decimal(item)
                if isinstance(converted, dict):
                    dynamo_records.append(converted)
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key

        fallback_records = JsonlLedger().read_all()

        merged: dict[str, dict[str, Any]] = {}
        for rec in fallback_records:
            merged[self._dedup_key(rec)] = rec
        for rec in dynamo_records:
            merged[self._dedup_key(rec)] = rec  # DynamoDB 版本優先，覆蓋 fallback 那筆

        records = list(merged.values())
        records.sort(key=lambda r: (str(r.get("ts", "")), str(r.get("run_id", ""))))
        return records


def get_ledger() -> Ledger:
    """依 env `COST_LEDGER_BACKEND`（jsonl|dynamodb，預設 jsonl）選 backend。

    選 `dynamodb` 本身不會 raise（`DynamoDBLedger.__init__` 只讀 env、不連
    AWS），實際是否可用（憑證/表是否存在）要到 `append`/`read_all` 呼叫時才
    知道，失敗由 `append_run()` 接住 fallback 寫 JsonlLedger。
    """
    backend = os.getenv("COST_LEDGER_BACKEND", "jsonl").strip().lower()
    if backend == "dynamodb":
        return DynamoDBLedger()
    return JsonlLedger()


def append_run(record: dict[str, Any], ledger: Ledger | None = None) -> None:
    """寫入一筆 run 記錄；`ledger` 未提供時用 `get_ledger()`。

    帳本是分析 pipeline 的旁路（side-channel）：任何 backend 失敗（如
    `DynamoDBLedger` 缺憑證/建表、`JsonlLedger` 路徑不可寫）一律吞掉例外、
    只印 stderr warning，**絕不往上拋**——帳本壞了頂多這筆沒記錄，不能讓
    已經算完的分析報告因此中斷（502）。fallback 也包 try/except，且若
    target 本身已是 `JsonlLedger` 就不重試同一路徑（同路徑必再失敗，重試
    沒有意義）。

    ⚠️ 統一在這裡（分派給 backend/fallback **之前**）生成 `run_id`（若
    record 本身沒有）：`record["run_id"] = uuid.uuid4().hex`。這樣不管最終
    寫進 backend（如 DynamoDB）還是 outage 時 fallback 寫進 JsonlLedger，
    拿到的都是同一個全域唯一 id——避免同幣同秒兩次執行（ts 只到秒）在
    backend 內部各自用 `ts+coin` 衍生出同一個 id 而互相覆蓋，也讓
    `DynamoDBLedger.read_all()` 合併 DynamoDB + fallback 時能正確去重、
    不會跟其他幣同秒的記錄相撞。
    """
    if not record.get("run_id"):
        record["run_id"] = uuid.uuid4().hex

    target = ledger if ledger is not None else get_ledger()
    try:
        target.append(record)
        return
    except Exception as exc:
        print(f"[ledger] WARNING: append 失敗（backend={type(target).__name__}）：{exc}",
              file=sys.stderr)

    if isinstance(target, JsonlLedger):
        return  # 同一顆 JsonlLedger 剛失敗，換個新實例打同路徑必再失敗，不重試

    try:
        JsonlLedger().append(record)
    except Exception as exc:
        print(f"[ledger] WARNING: fallback JsonlLedger append 仍失敗：{exc}", file=sys.stderr)
