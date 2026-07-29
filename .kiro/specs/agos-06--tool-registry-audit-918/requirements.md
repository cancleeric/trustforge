# Tool Capability Registry Schema 與 Invocation Audit

> Issue: #918 | Epic: #914
> Depends on: #915
> Labels: agent-os, database, security, P0
> Safety: ⛔ DB schema/migration — 需 Eric 當日授權 token

## 背景

Agent OS 需要一個 Tool Capability Registry，記錄所有 agent 可使用的工具（API 呼叫、
檔案操作、外部服務）的 metadata 與 side-effect classification，以及一個 invocation
audit trail 記錄每次工具呼叫的 input/output hash。

核心安全原則：**unknown tools cannot execute**; **external-write/deploy always require
human approval**。

## 範圍

實作 `tool_capabilities` 與 `tool_invocations` 兩張表的 migration、repository、
validation logic。

**不包含**：Context Builder 整合（#921）、Runtime wiring（#922）、production deployment。

## 功能需求

### FR-1: tool_capabilities table

| Column | Type | Constraint |
|--------|------|-----------|
| tool_id | TEXT | PK |
| name | TEXT | NOT NULL |
| version | TEXT | NOT NULL DEFAULT '1.0.0' |
| side_effect_class | TEXT | NOT NULL, CHECK in (read_only, local_write, external_write, deploy_or_release) |
| evidence_class | TEXT | NOT NULL DEFAULT 'none', CHECK in (none, context_only, candidate_evidence, trusted_evidence) |
| approval_requirement | TEXT | NOT NULL DEFAULT 'never', CHECK in (never, always, conditional) |
| timeout_sec | INTEGER | NOT NULL DEFAULT 30 |
| max_retries | INTEGER | NOT NULL DEFAULT 0 |
| backoff_sec | REAL | NOT NULL DEFAULT 1.0 |
| owner | TEXT | NOT NULL DEFAULT '' |
| schema_input | TEXT (JSON) | NOT NULL DEFAULT '{}' |
| schema_output | TEXT (JSON) | NOT NULL DEFAULT '{}' |
| created_at | TEXT (ISO 8601) | NOT NULL |

- 若 `side_effect_class` 為 `external_write` 或 `deploy_or_release`，
  `approval_requirement` 必須為 `always`（DB-level trigger 或 app-level check）

### FR-2: tool_invocations table

| Column | Type | Constraint |
|--------|------|-----------|
| invocation_id | TEXT (UUID) | PK |
| run_id | TEXT (UUID) | NOT NULL |
| tool_id | TEXT | FK → tool_capabilities, NOT NULL |
| input_hash | TEXT (SHA-256) | NOT NULL |
| output_hash | TEXT (SHA-256) | nullable |
| status | TEXT | NOT NULL, CHECK in (pending, success, failed, timeout, rejected) |
| error | TEXT | nullable |
| evidence_refs | TEXT (JSON array) | NOT NULL DEFAULT '[]' |
| started_at | TEXT (ISO 8601) | NOT NULL |
| completed_at | TEXT (ISO 8601) | nullable |

- Index on `(run_id)`
- Index on `(tool_id, started_at)`

### FR-3: ToolCapability / ToolInvocation dataclasses

### FR-4: ToolRegistryRepository

- `register_tool(cap: ToolCapability) -> None`
- `get_tool(tool_id: str) -> ToolCapability | None`
- `list_tools(*, side_effect_class: str | None = None) -> list[ToolCapability]`
- `is_known(tool_id: str) -> bool`
- `requires_approval(tool_id: str) -> bool`
- `record_invocation(inv: ToolInvocation) -> None`
- `complete_invocation(invocation_id: str, *, output_hash: str, status: str, error: str | None = None) -> None`
- `get_invocations_by_run(run_id: str) -> list[ToolInvocation]`
- `get_invocation(invocation_id: str) -> ToolInvocation | None`

### FR-5: Security Invariants

1. Unknown tool（not in registry）→ `is_known()` returns False → caller MUST NOT execute
2. `external_write` / `deploy_or_release` → `requires_approval()` returns True
3. `context_only` evidence_class → output 不可進入 Evidence scoring
4. Missing policy（tool exists but approval_requirement misconfigured）→ fail closed

## 非功能需求

- **NFR-1: 零第三方依賴**
- **NFR-2: fail-closed** — unknown tool / missing policy → reject
- **NFR-3: Audit trail 不可刪除** — invocation records 只能 INSERT，不可 UPDATE/DELETE

## 驗收條件

1. Capability records 含 schemas, side-effect/evidence classes, approval, timeout/retry, owner
2. Invocation records hash input/output 並記錄 status/errors/evidence refs
3. Unknown tools cannot execute
4. External-write/deployment capabilities require approval
5. Context-only output cannot enter Evidence
6. Migration upgrade/rollback 與 audit tests 通過
7. 完整 pre-push 通過
