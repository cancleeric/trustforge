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
  - `DynamoDBLedger`：stub，未實作（本 PR 不打真 AWS、不建表）。線上持久用，
    需先建表 + 賦予執行環境（EC2 instance role / Lambda execution role）
    dynamodb:PutItem / dynamodb:Scan 權限，兩者皆完成後才由 CEO 另立步驟啟用。
  - `get_ledger()` 依 env `COST_LEDGER_BACKEND`（jsonl|dynamodb，預設 jsonl）
    選 backend；`append_run()` 對 DynamoDB 等未實作/失敗的 backend 一律 fallback
    寫 JsonlLedger，確保帳本永遠可寫、pipeline 不因帳本故障而中斷。
"""
from __future__ import annotations

import json
import os
import sys
from abc import ABC, abstractmethod
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
    """線上持久用 backend（stub，未實作）。

    CEO gated AWS 步驟：需先建 DynamoDB 表（建議 PK=run_id 或 ts+coin 組合），
    並賦予執行環境（EC2 instance role / Lambda execution role）
    `dynamodb:PutItem` / `dynamodb:Scan` 權限，兩者皆完成後才能真正啟用
    （`COST_LEDGER_BACKEND=dynamodb`）。

    本 PR **不實作真 DynamoDB、不打 AWS**，僅預留介面位置：`append`/`read_all`
    目前一律 raise `NotImplementedError`，`get_ledger()`/`append_run()` 遇到此
    情況會 fallback 回 `JsonlLedger`，確保呼叫端不受影響。
    """

    def __init__(self, table_name: str | None = None):
        self.table_name = table_name or os.getenv(
            "TRUSTFORGE_COST_LEDGER_TABLE", "trustforge-cost-ledger"
        )

    def append(self, record: dict[str, Any]) -> None:
        raise NotImplementedError(
            "DynamoDBLedger 尚未實作（stub）。需 CEO gated AWS 步驟（建表 + IAM 權限）後啟用。"
        )

    def read_all(self) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "DynamoDBLedger 尚未實作（stub）。需 CEO gated AWS 步驟（建表 + IAM 權限）後啟用。"
        )


def get_ledger() -> Ledger:
    """依 env `COST_LEDGER_BACKEND`（jsonl|dynamodb，預設 jsonl）選 backend。

    `dynamodb` 目前是未實作 stub；選了也不 raise（建構本身不打 AWS），實際失敗
    會在 `append`/`read_all` 呼叫時發生，由 `append_run()` 接住 fallback。
    """
    backend = os.getenv("COST_LEDGER_BACKEND", "jsonl").strip().lower()
    if backend == "dynamodb":
        return DynamoDBLedger()
    return JsonlLedger()


def append_run(record: dict[str, Any], ledger: Ledger | None = None) -> None:
    """寫入一筆 run 記錄；`ledger` 未提供時用 `get_ledger()`。

    帳本是分析 pipeline 的旁路（side-channel）：任何 backend 失敗（如
    `DynamoDBLedger` 的 `NotImplementedError`、未來真 DynamoDB 缺憑證/建表、
    或 `JsonlLedger` 路徑不可寫）一律吞掉例外、只印 stderr warning，**絕不
    往上拋**——帳本壞了頂多這筆沒記錄，不能讓已經算完的分析報告因此中斷
    （502）。fallback 也包 try/except，且若 target 本身已是 `JsonlLedger`
    就不重試同一路徑（同路徑必再失敗，重試沒有意義）。
    """
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
