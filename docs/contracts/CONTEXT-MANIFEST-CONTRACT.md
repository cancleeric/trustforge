# Context Manifest Contract

> Epic: [#914](https://github.com/cancleeric/trustforge/issues/914)
> Issue: [#921](https://github.com/cancleeric/trustforge/issues/921)
> Related: [TRUST-KERNEL-BOUNDARY](../architecture/TRUST-KERNEL-BOUNDARY.md) |
> [MEMORY-OS-CONTRACT](./MEMORY-OS-CONTRACT.md) |
> [TASK-SKILL-CONTRACT](./TASK-SKILL-CONTRACT.md) |
> [TOOL-CAPABILITY-CONTRACT](./TOOL-CAPABILITY-CONTRACT.md)

## 1. 概述

Context Manifest 是每次 analysis run 的**不可變執行快照**，記錄該 run
使用了哪些 memory、skill、tool 和 policy references，以及哪些被排除
（含排除原因）。

核心設計原則：**Immutable after creation**——一旦建立，後續的 memory/skill/tool/policy
更新不影響已存在的 manifest。

## 2. Identity Schema

### 2.1 context_manifest

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `manifest_id` | UUID | yes (PK) | 唯一識別 |
| `run_id` | UUID | yes (UNIQUE) | 所屬 analysis run（一個 run 一份 manifest） |
| `created_at` | ISO 8601 | yes | 建立時間 |
| `content_hash` | SHA-256 | yes | 完整 manifest 的 deterministic hash |
| `token_budget` | int | yes | 該 run 的 token 預算上限 |
| `token_used` | int | yes | 實際使用的 token 數 |
| `included_refs` | JSON | yes | 見 §3 |
| `excluded_refs` | JSON | yes | 見 §4 |

## 3. Included References

```yaml
included_refs:
  snapshot_ref: string | null      # market data snapshot reference
  question_ref: string | null      # user question reference
  memory_refs:                     # list of included memory entries
    - memory_id: UUID
      rank: int                    # retrieval rank (1-based)
      reason: string               # selection reason
      evidence_eligible: bool      # eligibility status at freeze time
  skill_refs:                      # list of frozen skill revisions
    - skill_id: string
      revision_hash: SHA-256
      reason: string               # selection reason
  tool_refs:                       # list of available tools
    - tool_id: string
      version: string
  policy_refs:                     # list of active policies
    - policy_id: string
      revision_hash: SHA-256
```

## 4. Excluded References

```yaml
excluded_refs:                     # list of excluded items
  - ref_id: string                 # the excluded item's ID
    ref_type: enum                 # memory | skill | tool | policy
    reason: enum                   # see below
```

### Exclusion Reasons

| Reason | 意義 | 適用類型 |
|--------|------|----------|
| `stale` | 過期或已 retired/frozen | memory, skill, tool |
| `over_budget` | Token 用量超過 budget | memory |
| `approval_required` | 高風險未獲 approval | skill, tool |
| `evidence_ineligible` | 不可作為 Evidence input | memory |

**Note**: `evidence_ineligible` memory 仍可出現在 context display（report 呈現、
Admin summary），但**不可進入 Evidence scoring input**。

## 5. Immutability Guarantee

### 5.1 Freeze-on-Create

Context Manifest 一旦由 `ContextBuilder.build()` 建立：
- `content_hash` 為 final
- `included_refs` 為 final
- `excluded_refs` 為 final
- DB record 不可 UPDATE

### 5.2 隔離保證

後續事件不影響已建立的 manifest：
- Memory entry 被新增/修改/過期 → 不影響
- Skill active pointer 被切換 → 不影響
- Tool 被新增/移除 → 不影響
- Policy 被更新 → 不影響

Manifest 是該 run 的**時間切片快照**。

## 6. Deterministic Hash

```python
content_hash = SHA-256(canonical_json({
    "run_id": run_id,
    "included_refs": included_refs.to_dict(),
    "excluded_refs": [e.to_dict() for e in excluded_refs],
    "token_budget": token_budget,
    "token_used": token_used,
}))
```

同一組輸入 → 同一 `content_hash`。用途：
- **Replay verification**: 重播同一 manifest 應產出相同 hash
- **Tamper detection**: hash mismatch = content 被篡改
- **Deduplication**: 可快速比較兩份 manifest 是否等價

## 7. Token Budget

### 7.1 計算方式

```
token_estimate(text) ≈ (ascii_chars / 4) + (cjk_chars / 2) + 1
```

簡化估算（不依賴 tokenizer library，符合零第三方依賴原則）。

### 7.2 Budget 執行

Build 過程中按 rank 順序加入 memory refs：
1. 加入下一筆 → 估算 token cost
2. `token_used + cost > token_budget` → 排除（reason: `over_budget`）
3. 繼續下一筆

## 8. Report / Admin Disclosure

Context Manifest 提供兩種揭露：

### 8.1 Report Disclosure（面向使用者）

在最終報告中揭露 context 使用概況：
- 使用了多少 memory references
- 有多少被排除（原因分佈）
- Token 使用率

**不可將 context display 宣稱為 Evidence**。

### 8.2 Admin Disclosure（面向管理者）

Admin API 提供完整 manifest 內容：
- 所有 included refs 的詳細資訊
- 所有 excluded refs 的排除原因
- Content hash for verification

## 9. 禁止事項

| 禁止行為 | 原因 |
|----------|------|
| 修改已建立的 manifest | Immutability guarantee |
| 將 excluded evidence_ineligible memory 當作 Evidence | Evidence integrity |
| 在 manifest 建立後加入新 refs | Freeze-on-create |
| 刪除 manifest record | Audit trail |
| 一個 run 建立多份 manifest | One manifest per run |
| Context Manifest import Trust Kernel | 單向依賴 |

## 10. 與其他 Contract 的關係

```
Memory OS ────────→ memory_refs ─────┐
                                     │
Task Skill Registry → skill_refs ────┤
                                     ├──→ Context Manifest ──→ Runtime
Tool Capability ──→ tool_refs ───────┤            │
                                     │            ↓
Outer Policy ─────→ policy_refs ─────┘     Admin API / Report
                                           (disclosure only)
                                                  │
                                                  ↓
                                        Trust Kernel (UNTOUCHED)
```

Context Manifest 是 Agent OS 的**集成點**，將四個子系統的 references
凍結為一份 immutable snapshot，供 Runtime 使用、供 Admin 揭露。
