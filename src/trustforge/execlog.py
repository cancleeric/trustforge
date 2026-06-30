"""執行紀錄 Execution Log — 官方必交件。

記錄時戳、工具呼叫、資料取得與分析流程摘要，輸出 JSONL。
同時用於 15 分鐘執行預算的時間追蹤。
"""
from __future__ import annotations

import json
import time

from .schema import iso_utc

# 官方執行上限 15 分鐘
RUNTIME_BUDGET_SEC = 15 * 60


class ExecutionLog:
    def __init__(self, now_fn=time.time):
        self._now = now_fn
        self.start = self._now()
        self.events: list[dict] = []
        self.record("session.start", summary="TrustForge 分析開始")

    def elapsed(self) -> float:
        return self._now() - self.start

    def remaining(self) -> float:
        return max(0.0, RUNTIME_BUDGET_SEC - self.elapsed())

    def over_budget(self) -> bool:
        return self.elapsed() >= RUNTIME_BUDGET_SEC

    def record(self, tool: str, params: dict | None = None, summary: str = "") -> None:
        self.events.append({
            "ts": iso_utc(self._now()),
            "elapsed_sec": round(self.elapsed(), 2),
            "tool": tool,
            "params": params or {},
            "summary": summary,
        })

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(e, ensure_ascii=False) for e in self.events)
