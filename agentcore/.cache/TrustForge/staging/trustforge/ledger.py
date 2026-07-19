"""跨 run 持久化成本帳本（Bedrock LLM 呼叫花費）。

⚠️ 不與 `execlog.py` 混淆：`ExecutionLog` 是「單次 run 內」的執行紀錄
（`llm.cost` 事件、15 分鐘預算追蹤），本檔是「跨 run」累積帳本 —— 每次
`run_agent_pipeline` 收尾把該 run 的成本彙總寫一筆，讓 WebUI `/costs`
能看到歷史所有 run 的累計花費，即使伺服器程序重啟也能重讀（本機使用
SQLite；DynamoDB backend 見下方，可跨機器/跨容器重建也不失）。

Backend 可插拔（CEO 架構決策）：
  - `Ledger`：最小介面（`append` / `read_all`），呼叫端只依賴介面，換 backend
    不用改呼叫碼。
  - `SQLiteLedger`：本機開發預設，使用 WAL 與唯一 run_id 保存完整成本帳本。
  - `JsonlLedger`：相容既有 append-only JSONL 帳本與資料遷移。
  - `DynamoDBLedger`：線上持久用實作（本 PR 只寫 code + mock 測試，**不打真
    AWS、不建表**）。需先建表 + 賦予執行環境（EC2 instance role / Lambda
    execution role）dynamodb:PutItem / dynamodb:Scan 權限，兩者皆完成後才由
    CEO 另立步驟切 `COST_LEDGER_BACKEND=dynamodb` 真正啟用。
  - `get_ledger()` 依 env `COST_LEDGER_BACKEND`（sqlite|jsonl|dynamodb，預設 jsonl）
    選 backend；`append_run()` 對 DynamoDB 等 backend 呼叫失敗（缺憑證/表未建/
    網路問題）一律 fallback 寫 JsonlLedger，確保帳本永遠可寫、pipeline 不因
    帳本故障而中斷。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import uuid
from abc import ABC, abstractmethod
from contextlib import closing
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
    # W1.5（#15）+ codex 審查發現的 MEDIUM 修正：bedrock.py 預設 stance_model_id
    # 已改用 `au.` region 前綴 + 帶日期/版本後綴的完整 id，跟這裡舊有的
    # `apac.anthropic.claude-haiku-4-5` 精確 key 對不上，導致 estimate_cost()
    # 精確查找失敗 → 真實呼叫成本被悄悄記成 $0，掩蓋支出。價格同 Haiku 4.5。
    "au.anthropic.claude-haiku-4-5-20251001-v1:0": (1.0, 5.0),
}


def estimate_cost(model_id: str | None, tokens_in: int, tokens_out: int) -> float:
    """依 `PRICING` 估算單次呼叫成本（USD）。model_id 為 None/未知 → 0，不 raise。

    刻意**只做精確查表**，不做子字串/正規化比對（codex 審查發現：先前加過的
    「model id 含 haiku-4-5/sonnet-4-6 關鍵字就套價」子字串 fallback 太鬆，
    `vendor.fake-haiku-4-5` 這種非法/山寨 id 也會被誤套 Haiku 價，違反
    「unknown model → 0」的精確契約）。我們的 model id 自己可控，換 region/
    版本時直接在 `PRICING` 明確加一個 key 即可，不靠模糊比對猜。
    """
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

# codex HIGH（/api/costs 序列化無界 ledger）：`Ledger.summary()` 的 `runs` 欄位過去是
# `read_all()` 的完整結果，帳本無上限成長時會讓 SSR /status、/costs 與 JSON /api/costs
# 三個消費端都序列化整份帳本（server 建巨大 JSON + 網路傳輸 + 前端保留全量陣列）。
# 這裡把 `runs` 上限訂為最近 50 筆——剛好對齊 `_render_costs_page()` 既有的
# `list(reversed(runs))[:50]` 顯示上限，讓帳本 ≤50 筆時輸出完全不變、帳本 >50 筆時
# SSR/`/api/costs` 都只挑最近 50 筆（真實總筆數另外用 `run_count` 欄位提供）。
SUMMARY_RECENT_RUNS_CAP = 50


class Ledger(ABC):
    """成本帳本最小介面：append-only 寫入 + 讀出全部紀錄。"""

    @abstractmethod
    def append(self, record: dict[str, Any]) -> None:
        """寫入一筆 run 記錄（append-only，不可修改/刪除既有紀錄）。"""

    @abstractmethod
    def read_all(self) -> list[dict[str, Any]]:
        """讀出全部歷史紀錄（依寫入順序）。"""

    def summary(self) -> dict[str, Any]:
        """累計總花費 + 依 model 分組彙總（預設實作；backend 可覆寫做更有效率的版本）。

        成本會計階段1（純顯示層）：除了既有的 `by_model`（model → 累計成本，向後
        相容、格式不變，避免破壞既有呼叫端/測試），另外彙總 `by_model_detail`
        （model → {cost_usd, tokens_in, tokens_out}）——資料來源與 `by_model` 完全
        相同（每筆 run 的 `calls[]`），只是同時累加 tokens_in/tokens_out，供
        `/costs`、`/status` 顯示「Model｜輸入tokens｜輸出tokens｜單價｜成本」明細表。
        用 `.get(..., 0)` 防呆：舊紀錄（本欄位加入前寫入的 `calls[]`）沒有
        tokens_in/tokens_out 欄位時視為 0，不 raise、不影響既有 cost_usd 彙總。

        codex HIGH（成本端點可擴展性）：`total_cost_usd`/`by_model`/`by_model_detail`
        仍照舊彙總「全部」紀錄（有界統計值，不受帳本大小影響回應體積）；但 `runs`
        欄位改成有界的 `run_count`（真實總筆數）+ 最近 `SUMMARY_RECENT_RUNS_CAP` 筆
        （依原本寫入時間順序，最舊在前，跟 `read_all()` 回傳順序一致），不再回傳
        無界的完整清單，避免帳本成長後拖垮序列化/傳輸/前端記憶體。
        """
        records = self.read_all()
        total = 0.0
        by_model: dict[str, float] = {}
        by_model_detail: dict[str, dict[str, float | int]] = {}
        for rec in records:
            total += float(rec.get("total_cost_usd", 0.0) or 0.0)
            for call in rec.get("calls", []) or []:
                model = call.get("model") or "offline"
                cost = float(call.get("cost_usd", 0.0) or 0.0)
                tokens_in = int(call.get("tokens_in", 0) or 0)
                tokens_out = int(call.get("tokens_out", 0) or 0)
                by_model[model] = by_model.get(model, 0.0) + cost
                detail = by_model_detail.setdefault(
                    model, {"cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0}
                )
                detail["cost_usd"] += cost
                detail["tokens_in"] += tokens_in
                detail["tokens_out"] += tokens_out
        return {
            "total_cost_usd": round(total, 6),
            "by_model": {m: round(v, 6) for m, v in by_model.items()},
            "by_model_detail": {
                m: {
                    "cost_usd": round(d["cost_usd"], 6),
                    "tokens_in": d["tokens_in"],
                    "tokens_out": d["tokens_out"],
                }
                for m, d in by_model_detail.items()
            },
            "run_count": len(records),
            "runs": records[-SUMMARY_RECENT_RUNS_CAP:],
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


class SQLiteLedger(Ledger):
    """本機 append-only SQLite 成本帳本。

    使用 WAL 讓 WebUI 讀取與背景排程寫入可以並行；payload 保留完整 record，
    不把會持續演進的成本欄位拆成僵硬欄位。``sequence`` 是唯一排序依據，
    ``run_id`` 唯一約束防止重複匯入同一筆歷史紀錄。
    """

    def __init__(self, path: str | Path | None = None):
        home = Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2])))
        default_path = Path(os.getenv("TRUSTFORGE_SQLITE_PATH", str(home / "out" / "trustforge.sqlite3")))
        self.path = Path(path) if path is not None else default_path
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cost_ledger (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL UNIQUE,
                        ts TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )

    def append(self, record: dict[str, Any]) -> None:
        payload = dict(record)
        run_id = str(payload.get("run_id") or uuid.uuid4().hex)
        payload["run_id"] = run_id
        ts = str(payload.get("ts") or datetime.now(timezone.utc).isoformat())
        payload["ts"] = ts
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock, closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO cost_ledger (run_id, ts, payload_json) VALUES (?, ?, ?)",
                    (run_id, ts, encoded),
                )

    def read_all(self) -> list[dict[str, Any]]:
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM cost_ledger ORDER BY sequence ASC"
            ).fetchall()
        records: list[dict[str, Any]] = []
        for (payload_json,) in rows:
            try:
                record = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(record, dict):
                records.append(record)
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

    def __init__(
        self,
        table_name: str | None = None,
        region: str | None = None,
        *,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
        max_attempts: int | None = None,
    ):
        self.table_name = table_name or os.getenv(
            "TRUSTFORGE_COST_LEDGER_TABLE", "trustforge-cost-ledger"
        )
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._table: Any = None  # lazy：建構本身不連 AWS
        # 三個 timeout/重試參數**預設一律 None**（沿用 boto3/botocore 內建
        # 預設值，等同修改前行為）——比照 `cache.py::DynamoDBCache` 同款設計，
        # 只有明確傳入才會限縮，不影響既有呼叫端（`fetch_scheduler.py` 一般
        # 排程路徑／`get_ledger()` 預設路徑）既有的容錯空間。codex HIGH：
        # `deploy/deploy_ec2.sh` 的 `--probe` 部署 gate 需要「真正有界」的
        # DynamoDB 呼叫（不能被 boto3 預設 timeout/重試拖到數分鐘），才會
        # 明確傳入這三個參數，見 `scripts/fetch_scheduler.py::_probe_ledger_backend()`。
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._max_attempts = max_attempts

    def _get_table(self) -> Any:
        """lazy 取得 boto3 Table 物件；第一次呼叫才真的碰 AWS SDK。"""
        if self._table is None:
            import boto3  # 延遲匯入：建構/未啟用 dynamodb backend 時不需要憑證

            config = None
            if (
                self._connect_timeout is not None
                or self._read_timeout is not None
                or self._max_attempts is not None
            ):
                from botocore.config import Config

                kwargs: dict[str, Any] = {}
                if self._connect_timeout is not None:
                    kwargs["connect_timeout"] = self._connect_timeout
                if self._read_timeout is not None:
                    kwargs["read_timeout"] = self._read_timeout
                if self._max_attempts is not None:
                    kwargs["retries"] = {"max_attempts": self._max_attempts, "mode": "standard"}
                config = Config(**kwargs)

            self._table = boto3.resource(
                "dynamodb", region_name=self.region, config=config
            ).Table(self.table_name)
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

    def get_canary(self, run_id: str, ts: str) -> dict[str, Any] | None:
        """低階「按完整主鍵」`GetItem`（強一致 `ConsistentRead=True`），查某筆
        固定 canary 是否真的寫入落地。

        供 `scripts/fetch_scheduler.py --probe` 用：canary 的 `(run_id, ts)`
        每次都固定，直接按完整主鍵 `GetItem` 一定讀得到剛才 `append()` 寫入
        的那筆——不像 `Scan`：`Scan` 只保證掃過去，但（1）預設是最終一致讀，
        PutItem 後立刻讀理論上可能讀到舊值；（2）單次回應只有第一頁
        （回應大小 ≤1MB，`FilterExpression` 是掃完才套用，不保證篩到的那頁
        剛好含目標項目）——表越大、canary 落在越後面的頁就越可能被誤判成
        「讀不到」，造成正常部署被判失敗。`GetItem` 按主鍵查，沒有分頁問題，
        搭配 `ConsistentRead=True` 也沒有最終一致的問題，能不受表大小/複寫
        延遲影響地確定性判斷「這筆到底寫進去了沒」。
        """
        resp = self._get_table().get_item(
            Key={"run_id": run_id, "ts": ts}, ConsistentRead=True
        )
        item = resp.get("Item")
        return item if isinstance(item, dict) else None

    def probe_scan_permission(self) -> None:
        """純粹驗證 `dynamodb:Scan` 這個 action 本身有沒有被拒——**不**要求
        掃到任何特定內容。

        `/costs` 端點跟 `read_all()` 都是靠 `Scan` 讀這張表，若 IAM 只放行
        `PutItem`、`Scan` 被拒，一般 `append()` 仍會成功但 `/costs` 會整個
        讀失敗，這是 probe 要另外驗的東西。但**驗證方式跟「驗證寫入落地」
        必須分開**：若拿 `Scan` 的結果去核對「有沒有掃到剛寫的 canary」，
        會被最終一致讀 + 分頁問題污染，變成誤判（見 `get_canary()`
        docstring）；這裡只做 `Table.scan(Limit=1)`，只要呼叫本身不丟例外
        （不管掃回什麼、掃不掃得到 canary）就代表 `Scan` 權限正常，呼叫端
        自行決定例外（含 AccessDenied）要怎麼處理（probe 一律視為失敗）。
        """
        self._get_table().scan(Limit=1)

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

        讀 fallback 這步單獨包 try/except：本機檔案權限異常/I-O 失敗/非 UTF-8
        等問題只會讓 fallback 那幾筆讀不到，**不會**讓已經 scan 成功的
        DynamoDB 結果也跟著拋錯——DynamoDB 才是這個 backend 的主資料來源。
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

        # 讀 JSONL fallback 單獨隔離例外：本機檔案權限異常/I-O 失敗/非 UTF-8
        # 等問題，不能拖垮已經 scan 成功的 DynamoDB 結果——寧可少幾筆 outage
        # 期間的 fallback 記錄，也不能讓整個 read_all 拋錯把 /costs 弄成 500。
        try:
            fallback_records = JsonlLedger().read_all()
        except Exception as exc:
            print(f"[ledger] WARNING: 讀取 JSONL fallback 失敗，忽略並僅回傳 "
                  f"DynamoDB 結果：{exc}", file=sys.stderr)
            fallback_records = []

        merged: dict[str, dict[str, Any]] = {}
        for rec in fallback_records:
            merged[self._dedup_key(rec)] = rec
        for rec in dynamo_records:
            merged[self._dedup_key(rec)] = rec  # DynamoDB 版本優先，覆蓋 fallback 那筆

        records = list(merged.values())
        records.sort(key=lambda r: (str(r.get("ts", "")), str(r.get("run_id", ""))))
        return records


def get_ledger() -> Ledger:
    """依 env `COST_LEDGER_BACKEND`（jsonl|sqlite|dynamodb）選 backend。

    選 `dynamodb` 本身不會 raise（`DynamoDBLedger.__init__` 只讀 env、不連
    AWS），實際是否可用（憑證/表是否存在）要到 `append`/`read_all` 呼叫時才
    知道，失敗由 `append_run()` 接住 fallback 寫 JsonlLedger。
    """
    backend = os.getenv("COST_LEDGER_BACKEND", "jsonl").strip().lower()
    if backend == "dynamodb":
        return DynamoDBLedger()
    if backend == "sqlite":
        return SQLiteLedger()
    return JsonlLedger()


def append_run(record: dict[str, Any], ledger: Ledger | None = None) -> bool:
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

    codex HIGH 追加（記帳完整性）：回傳 `bool`——`True` 表示這筆記錄確實
    持久化成功（primary 或 fallback 任一成功即算），`False` 表示 primary
    與 fallback 都失敗（真的沒記進帳本）。呼叫端
    （`agent.orchestrator.run_agent_pipeline()`）用這個回傳值判斷：真的
    花掉、但沒記成功的成本不能被當作「沒發生」，需另行記到
    `budget_guard.record_unledgered_spend()`，讓每日 cap 的比較仍算得到
    這筆花費，不會被「帳本沒記錄」繞過。**仍然不往上拋例外**——只是把
    「有沒有真的寫進去」用回傳值誠實回報，呼叫端要不要處理是呼叫端的事，
    帳本壞了本身依然不中斷 pipeline。
    """
    if not record.get("run_id"):
        record["run_id"] = uuid.uuid4().hex

    target = ledger if ledger is not None else get_ledger()
    try:
        target.append(record)
        return True
    except Exception as exc:
        print(f"[ledger] WARNING: append 失敗（backend={type(target).__name__}）：{exc}",
              file=sys.stderr)

    if isinstance(target, (JsonlLedger, SQLiteLedger)):
        return False  # 本機持久層失敗時不偷偷改寫另一種儲存格式

    try:
        JsonlLedger().append(record)
        return True
    except Exception as exc:
        print(f"[ledger] WARNING: fallback JsonlLedger append 仍失敗：{exc}", file=sys.stderr)
        return False
