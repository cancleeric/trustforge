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

    def record_llm_cost(
        self, model: str | None, tokens_in: int, tokens_out: int, cost_usd: float
    ) -> None:
        """記錄一次真實 Bedrock LLM 呼叫的 token 用量與估算成本。

        走既有 `record()`（tool="llm.cost"），不改 `to_jsonl()`/event schema —
        WebUI 本次成本卡與跨 run 帳本都從 `events` 篩 `tool=="llm.cost"` 彙總。
        離線/無 model_id 的呼叫也可能傳入（tokens=0, cost_usd=0.0），代表
        「此次呼叫發生但無花費」，不代表沒呼叫——是否呼叫由呼叫端決定要不要記。
        """
        model = model or "offline"
        cost_usd = round(float(cost_usd or 0.0), 6)
        self.record(
            "llm.cost",
            params={
                "model": model,
                "tokens_in": int(tokens_in or 0),
                "tokens_out": int(tokens_out or 0),
                "cost_usd": cost_usd,
            },
            summary=f"{model}：輸入 {tokens_in} tokens／輸出 {tokens_out} tokens／${cost_usd:.4f}",
        )

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(e, ensure_ascii=False) for e in self.events)
