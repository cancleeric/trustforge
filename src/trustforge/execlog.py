"""執行紀錄 Execution Log — 官方必交件。

記錄時戳、工具呼叫、資料取得與分析流程摘要，輸出 JSONL。
同時用於 15 分鐘執行預算的時間追蹤。
"""
from __future__ import annotations

import json
import time
import uuid

from .schema import iso_utc

# 官方執行上限 15 分鐘
RUNTIME_BUDGET_SEC = 15 * 60


# Hermes is the public-facing agent identity.  The pipeline remains deliberately
# deterministic around trust scoring; these nodes make that work inspectable
# instead of presenting the final narrative as an unexplained black box.
HERMES_NODES = (
    ("source_ingestion", "來源蒐集", 1),
    ("claim_extraction", "主張抽取", 2),
    ("trust_reasoning", "信任推理", 3),
    ("evidence_assembly", "證據組裝", 4),
    ("report_delivery", "報告交付", 5),
)


def _node_for_event(tool: str, params: dict) -> tuple[str, str, int]:
    """Map low-level events to the stable Hermes workflow graph."""
    step = params.get("step")
    if tool in {"ingestion.collect", "ingestion.source", "session.start"}:
        return HERMES_NODES[0]
    if step == 1 or tool == "pipeline.step1.start":
        return HERMES_NODES[1]
    if step == 2 or tool in {"pipeline.step2.start", "judgment.derive"}:
        return HERMES_NODES[2]
    if tool == "evidence.build":
        return HERMES_NODES[3]
    return HERMES_NODES[4]


def _status_for_event(tool: str, params: dict) -> str:
    if params.get("outcome") == "failed":
        return "failed"
    if tool.endswith(".start"):
        return "started"
    if tool in {
        "ingestion.collect", "judgment.derive", "evidence.build",
        "report.done", "comparison.done", "bedrock.complete",
    }:
        return "completed"
    return "observed"


class ExecutionLog:
    def __init__(self, now_fn=time.time, run_id: str | None = None):
        self._now = now_fn
        self.start = self._now()
        self.run_id = run_id or f"hermes-{uuid.uuid4().hex[:12]}"
        self.events: list[dict] = []
        self.record("session.start", summary="Hermes Agent 分析開始")

    def elapsed(self) -> float:
        return self._now() - self.start

    def remaining(self) -> float:
        return max(0.0, RUNTIME_BUDGET_SEC - self.elapsed())

    def over_budget(self) -> bool:
        return self.elapsed() >= RUNTIME_BUDGET_SEC

    def record(self, tool: str, params: dict | None = None, summary: str = "") -> None:
        params = params or {}
        node_id, node_label, node_order = _node_for_event(tool, params)
        # Keep the top-level JSONL contract stable for existing tooling.  Hermes
        # context lives under params, which was intentionally extensible.
        params = {
            **params,
            "hermes": {
                "run_id": self.run_id,
                "agent": "hermes",
                "node_id": node_id,
                "node_label": node_label,
                "node_order": node_order,
                "status": _status_for_event(tool, params),
            },
        }
        self.events.append({
            "ts": iso_utc(self._now()),
            "elapsed_sec": round(self.elapsed(), 2),
            "tool": tool,
            "params": params,
            "summary": summary,
        })

    def log_policy_snapshot(self, snapshot: dict) -> None:
        """Record effective policy snapshot at run start.

        Args:
            snapshot: Dict from PolicyExecutor.snapshot_for_log(), containing
                      per-family revision hash + policy_summary.
        """
        self.record(
            "policy.snapshot",
            params=snapshot,
            summary="Effective outer-skill policies frozen for this run",
        )

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

    def manifest(self) -> dict:
        """Public execution envelope used by API consumers and exported logs."""
        # Use the last recorded event, not wall clock at serialization time.  A
        # deduplicated API result may be serialized by several concurrent
        # followers; they must all receive byte-identical audit metadata.
        elapsed = self.events[-1]["elapsed_sec"] if self.events else 0.0
        return {
            "agent": "hermes",
            "run_id": self.run_id,
            "started_at": iso_utc(self.start),
            "elapsed_sec": elapsed,
            "budget_sec": RUNTIME_BUDGET_SEC,
            "nodes": [
                {"id": node_id, "label": label, "order": order}
                for node_id, label, order in HERMES_NODES
            ],
            "skill_revisions": next(
                (event["params"] for event in self.events if event["tool"] == "hermes.skills"),
                {"outer_skills": [], "core_controls": "not-recorded"},
            ),
        }
