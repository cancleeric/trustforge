# Tool Capability Contract

> Epic: [#914](https://github.com/cancleeric/trustforge/issues/914)
> Issue: [#918](https://github.com/cancleeric/trustforge/issues/918)
> Related: [TRUST-KERNEL-BOUNDARY](../architecture/TRUST-KERNEL-BOUNDARY.md) |
> [MEMORY-OS-CONTRACT](./MEMORY-OS-CONTRACT.md) |
> [TASK-SKILL-CONTRACT](./TASK-SKILL-CONTRACT.md) |
> [CONTEXT-MANIFEST-CONTRACT](./CONTEXT-MANIFEST-CONTRACT.md)

## 1. 概述

Tool Capability Registry 管理 Hermes 可使用的所有外部工具（API 呼叫、
檔案操作、外部服務），記錄 side-effect classification 與 invocation audit
trail。

核心安全原則：
- **Unknown tools cannot execute**（fail-closed）
- **External-write/deploy always require human approval**

## 2. Identity Schema

### 2.1 tool_capability

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tool_id` | string | yes (PK) | 唯一識別（e.g. `coingecko-price-fetch`） |
| `name` | string | yes | 人類可讀名稱 |
| `version` | string | yes | 版本號（semver） |
| `side_effect_class` | enum | yes | 見 §3 Side-Effect Classification |
| `evidence_class` | enum | yes | 見 §4 Evidence Classification |
| `approval_requirement` | enum | yes | `never` \| `always` \| `conditional` |
| `timeout_sec` | int | yes | 執行超時（預設 30s） |
| `max_retries` | int | yes | 最大重試次數（預設 0） |
| `backoff_sec` | float | yes | 重試退避（預設 1.0s） |
| `owner` | string | yes | 維護者/負責人 |
| `schema_input` | JSON Schema | yes | 輸入參數 schema |
| `schema_output` | JSON Schema | yes | 輸出結果 schema |
| `created_at` | ISO 8601 | yes | 註冊時間 |

### 2.2 tool_invocation

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `invocation_id` | UUID | yes (PK) | 呼叫唯一識別 |
| `run_id` | UUID | yes | 所屬 analysis run |
| `tool_id` | string (FK) | yes | 被呼叫的 tool |
| `input_hash` | SHA-256 | yes | 輸入參數的 hash |
| `output_hash` | SHA-256 | no | 輸出結果的 hash（pending 時為空） |
| `status` | enum | yes | `pending` \| `success` \| `failed` \| `timeout` \| `rejected` |
| `error` | string | no | 錯誤訊息 |
| `evidence_refs` | list[string] | yes | 相關 Evidence IDs |
| `started_at` | ISO 8601 | yes | 開始時間 |
| `completed_at` | ISO 8601 | no | 完成時間 |

**Constraint**: Invocation records 為 append-only，不可 DELETE。

## 3. Side-Effect Classification

| Level | Class | 定義 | 範例 |
|-------|-------|------|------|
| 0 | `read_only` | 不改變任何外部狀態 | CoinGecko 查價、讀取 DB |
| 1 | `local_write` | 只寫入本地受控資源 | 寫入 SQLite cache、更新 telemetry |
| 2 | `external_write` | 寫入外部系統 | 發送 webhook、寫外部 API |
| 3 | `deploy_or_release` | 影響生產環境 | 部署服務、模型上線、schema migration |

### Approval Invariant

若 `side_effect_class` 為 `external_write` 或 `deploy_or_release`，
`approval_requirement` **必須**為 `always`：

```
side_effect_class ∈ {external_write, deploy_or_release}
    → approval_requirement = always (ENFORCED)
```

違反此 invariant → 註冊失敗（fail-closed）。

## 4. Evidence Classification

| Class | 定義 | 可作為 Evidence？ |
|-------|------|------------------|
| `none` | 工具輸出不含資訊價值 | No |
| `context_only` | 輸出可作為 context display，但不可作為 Evidence | No |
| `candidate_evidence` | 輸出可能成為 Evidence（pending validation） | Conditional |
| `trusted_evidence` | 輸出已驗證，可直接作為 Evidence | Yes |

### Evidence Guard

`context_only` 與 `none` 的 tool output **不可進入 Evidence scoring pipeline**：

```
evidence_class ∈ {none, context_only}
    → output MUST NOT enter Trust scoring input
```

此 guard 由 Context Builder (#921) 與 Runtime (#922) 在組裝時強制執行。

## 5. Invocation Hash

```python
input_hash = SHA-256(canonical_json({"tool_id": tool_id, "args": args}))
output_hash = SHA-256(canonical_json(output))  # or SHA-256(output_string)
```

Hash 用途：
- Replay verification（same input → same hash → 可驗證執行一致性）
- Audit trail（可追溯每次呼叫的 I/O）
- Deduplication（short time window 內相同 input hash → cache）

## 6. Timeout & Retry Policy

| Field | 預設 | 說明 |
|-------|------|------|
| `timeout_sec` | 30 | 單次呼叫超時，超時 → status=timeout |
| `max_retries` | 0 | 最大重試次數（0=不重試） |
| `backoff_sec` | 1.0 | 重試間隔（指數退避 base） |

超時或重試耗盡 → status=failed，記錄 error。

## 7. Unknown Tool Handling（Fail-Closed）

```
is_known(tool_id) == false
    → CANNOT EXECUTE
    → requires_approval() returns true (defensive)
    → runtime MUST reject the invocation
```

任何未在 registry 中註冊的 tool 都不可執行。
這是 Agent OS 的**安全邊界**——新 tool 必須先註冊才能使用。

## 8. Audit Trail

Invocation records 提供完整的 tool 使用軌跡：

1. `record_invocation(pending)` — 執行前記錄
2. Tool execution
3. `complete_invocation(status, output_hash)` — 執行後更新

Audit trail 支援：
- 成本追蹤（per-tool invocation count）
- 異常偵測（高 failure rate）
- Replay verification（重播確認 I/O 一致）
- Evidence 溯源（output → invocation → tool → evidence_class）

## 9. 禁止事項

| 禁止行為 | 原因 |
|----------|------|
| 未註冊 tool 執行 | Fail-closed security boundary |
| external_write 不經 approval | Prevent unintended external side effects |
| deploy_or_release 自動執行 | Production safety |
| context_only output 進入 Evidence | Evidence integrity |
| 刪除 invocation records | Audit trail immutability |
| Tool Registry import Trust Kernel | 單向依賴 |

## 10. 與其他 Contract 的關係

```
Tool Capability Registry
       │
       ├─→ Context Manifest (tool_refs in manifest)
       ├─→ Runtime (invocation audit during execution)
       └─→ Admin API (disclosure of invocation history)
             │
             ↓
      Trust Kernel (NEVER TOUCHED)
```
