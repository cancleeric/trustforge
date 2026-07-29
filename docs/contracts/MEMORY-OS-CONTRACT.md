# Memory OS Contract

> Epic: [#914](https://github.com/cancleeric/trustforge/issues/914)
> Issue: [#916](https://github.com/cancleeric/trustforge/issues/916)
> Related: [TRUST-KERNEL-BOUNDARY](../architecture/TRUST-KERNEL-BOUNDARY.md) |
> [TASK-SKILL-CONTRACT](./TASK-SKILL-CONTRACT.md) |
> [TOOL-CAPABILITY-CONTRACT](./TOOL-CAPABILITY-CONTRACT.md) |
> [CONTEXT-MANIFEST-CONTRACT](./CONTEXT-MANIFEST-CONTRACT.md)

## 1. 概述

Memory OS 是 Agent OS 的持久化記憶層，管理 Hermes 在分析過程中累積的
episodic、semantic、procedural 和 dialogue 四類記憶。

核心設計原則：**Evidence-ineligible by default**——所有 memory entry 預設
不可作為 Trust scoring 的 Evidence input，必須通過嚴格的 eligibility
validation 才能升格。

## 2. Identity Schema

### 2.1 memory_entry

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `memory_id` | UUID | yes | 全域唯一識別碼 |
| `kind` | enum | yes | `episodic` \| `semantic` \| `procedural` \| `dialogue` |
| `provider` | string | yes | 來源系統標識（e.g. `coingecko`, `question_bank`, `hermes-analysis`） |
| `content_hash` | SHA-256 | yes | 內容的確定性 hash |
| `content_ref` | string | yes | 指向實際內容的 URI 或路徑 |
| `published_at` | ISO 8601 | no | 原始資料發布時間（外部來源才有） |
| `retrieved_at` | ISO 8601 | yes | 系統抓取/產生時間 |
| `expires_at` | ISO 8601 | no | 過期時間（過期後不再被 retrieval） |
| `evidence_eligible` | boolean | yes | **預設 `false`** |
| `run_id` | UUID | no | 產生此 entry 的 analysis run |
| `created_at` | ISO 8601 | yes | DB record 建立時間 |

### 2.2 memory_link

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `link_id` | UUID | yes | 關聯識別碼 |
| `from_memory_id` | UUID (FK) | yes | 來源 memory |
| `to_memory_id` | UUID (FK) | yes | 目標 memory |
| `relation` | enum | yes | `derived_from` \| `supersedes` \| `contradicts` \| `supports` |
| `created_at` | ISO 8601 | yes | 建立時間 |

**Constraint**: `from_memory_id ≠ to_memory_id`（禁止 self-link）

## 3. Revision / Content Hash

```
content_hash = SHA-256(canonical_json(content))

canonical_json: sort_keys=True, separators=(',',':'), ensure_ascii=False
```

與 `skills.py::canonical_json()` 使用相同序列化規則，確保跨模組 hash 一致。

一旦 memory entry 寫入，`content_hash` 不可修改。若內容變更，須建立新 entry
並用 `supersedes` link 關聯。

## 4. Evidence Eligibility Boundary

### 4.1 預設行為（Fail-Closed）

```
evidence_eligible = false  (ALL new entries)
```

### 4.2 升格為 Evidence 的必要條件

設定 `evidence_eligible = true` 前，必須全部滿足：

1. `provider` 非空
2. `published_at` 非空（原始資料有明確的發布時間）
3. `retrieved_at` 非空
4. `content_hash` 為有效 64-character hex（SHA-256）
5. `kind ≠ dialogue`（對話記錄永遠不可作為 Evidence）

**任一條件不滿足 → 拒絕（raise error），不做 fallback。**

### 4.3 Historical Conclusion Guard

Agent 自己過去產出的分析結論（`kind=semantic` 且 `provider` 以 `hermes-` 開頭）
**永遠不可**成為 Evidence 或 scoring input：

- 即使 timestamps / hash 完整
- 即使被 retrieve 也必須標記 `evidence_eligible=false`
- Runtime 應在 scoring pipeline 入口再次檢查

此規則確保 Hermes 不會自我引用形成回饋環路。

## 5. Retrieval Lineage

每次 memory retrieval 記錄：

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | 執行此 retrieval 的 analysis run |
| `memory_id` | UUID | 被 retrieve 的 memory |
| `rank` | int | retrieval 排名（1-based） |
| `reason` | string | 選取原因（e.g. `question_rag_similarity`, `dialogue_recent`） |

Lineage 事件同時寫入 `execution_log.jsonl`（event type: `memory_retrieval`）。

## 6. Validity Window

- `retrieved_at` ≤ current time（不可是未來時間）
- `expires_at`（若設定）過期後，memory 不被 retrieval 選取（視為 stale）
- `published_at` 用於時效衰減計算（由 Trust Kernel 的 `KIND_HALFLIFE_HOURS` 控制）

## 7. Lifecycle

Memory entry 不可刪除（append-only design）。狀態由 `expires_at` 與
`evidence_eligible` 控制：

- Active: `expires_at` 未設定或未過期
- Expired: `expires_at` 已過
- Evidence-eligible: `evidence_eligible=true` 且通過 validation
- Historical: `provider` 為 `hermes-*`，永遠 non-evidentiary

## 8. 禁止事項

| 禁止行為 | 原因 |
|----------|------|
| Historical memory 進入 Evidence scoring | 防止自我引用回饋環路 |
| Dialogue memory 作為 Evidence | 對話為主觀交互，非客觀事實 |
| 修改已存在的 content_hash | Immutability guarantee |
| 刪除 memory entry | Append-only audit trail |
| Memory OS import Trust Kernel | 單向依賴，Kernel 不知道 Memory |

## 9. 與 Trust Kernel 的邊界

Memory OS **不 import** Trust Kernel 模組。Memory 只提供資料：

```
Memory OS ──(data)──→ Context Builder ──(eligible refs)──→ Scoring Pipeline
                                                              │
                                                    Trust Kernel (immutable)
```

Memory 的 `evidence_eligible` 標記是**資料層 guard**；
Scoring Pipeline 的入口是**計算層 guard**——雙重防護。
