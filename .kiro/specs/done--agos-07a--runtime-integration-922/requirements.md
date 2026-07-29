# Analysis Runtime Integration 與 Execution Lineage

> Issue: #922 | Epic: #914
> Depends on: #919, #920, #921
> Labels: agent-os, runtime, P0

## 背景

#916–#921 各自實作了 Memory OS、Skill Registry/Loader、Tool Registry、Context Builder。
本 issue 將它們整合進既有的 continuous analysis run（`analysis_flow.py` / `hermes.py`），
建立完整的 execution lineage。

## 範圍

在既有 analysis run 中插入 Agent OS 呼叫：
- 建立 context manifest
- 記錄 skill selection lineage
- 記錄 memory retrieval lineage
- 記錄 tool invocation audit

**不改變 Trust scoring** — Trust Kernel inputs / weights / formula 完全不動。

**不包含**：Admin API（#923）、Admin UI（#924）、E2E tests（#925）。

## 功能需求

### FR-1: Context Manifest 建立

在 analysis run 開始時（orchestrator 或 analysis_flow 入口），呼叫
ContextBuilder.build() 建立該 run 的 context manifest。

### FR-2: Skill Selection Lineage

Execution log 新增事件：
```json
{
  "event": "skill_selection",
  "run_id": "...",
  "selected_skills": [{"skill_id": "...", "revision_hash": "...", "reason": "..."}],
  "frozen_manifest_hash": "...",
  "timestamp": "..."
}
```

### FR-3: Memory Retrieval Lineage（queryable by run）

`MemoryRetrievalAdapter` 的 lineage 事件（#919）已寫入 execution log。
本 issue 確保 lineage 可按 run_id 查詢（via memory_os repository）。

### FR-4: Tool Invocation Audit（queryable by run）

每次 tool 呼叫（目前主要是 ingestion connectors + bedrock）記錄到
tool_registry：
- record_invocation(pending)
- complete_invocation(success/failed)

### FR-5: Frozen References Throughout Run

Run 開始後凍結的 skill/memory/tool refs 在整個 run 期間不變。
即使外部 state 在 run 進行中改變，該 run 仍使用凍結版本。

### FR-6: Existing Workflow Non-Regression

- Question RAG / dialogue workflows 行為不變
- Trust Kernel scoring inputs 不變
- Evidence scoring 不變
- Report generation 不變（只多了 context manifest metadata）

## 非功能需求

- **NFR-1: 最小侵入** — 在 existing flow 中加 hook points，不重構 orchestrator
- **NFR-2: 可選啟用** — 環境變數 `TRUSTFORGE_AGOS_ENABLED=1` 控制是否啟用
- **NFR-3: 失敗降級** — Agent OS 元件故障時 gracefully degrade（log warning, continue run）

## 驗收條件

1. Continuous analysis 建立 context manifest before execution
2. Execution log 記錄 selected skill reason/revision
3. Memory retrieval 和 tool invocation lineage 可按 run 查詢
4. Frozen references 在整個 run 中使用
5. 既有 Question RAG/dialogue workflows 不回歸
6. Trust Kernel 和 Evidence scoring inputs 不變
7. Integration 和 full pre-push 通過
