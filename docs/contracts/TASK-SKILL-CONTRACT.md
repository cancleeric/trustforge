# Task Skill Contract

> Epic: [#914](https://github.com/cancleeric/trustforge/issues/914)
> Issue: [#917](https://github.com/cancleeric/trustforge/issues/917)
> Related: [TRUST-KERNEL-BOUNDARY](../architecture/TRUST-KERNEL-BOUNDARY.md) |
> [MEMORY-OS-CONTRACT](./MEMORY-OS-CONTRACT.md) |
> [TOOL-CAPABILITY-CONTRACT](./TOOL-CAPABILITY-CONTRACT.md) |
> [CONTEXT-MANIFEST-CONTRACT](./CONTEXT-MANIFEST-CONTRACT.md) |
> [SKILL-CHANGE-CONTROL](../qa/SKILL-CHANGE-CONTROL.md)

## 1. 概述

Task Skill 是 Agent OS 中可組合、可治理的細粒度技能單元。每個 skill 代表
Hermes 的一項能力（如基本面分析、情緒分析、markdown 報告生成等）。

與既有 **Outer Policy Family**（`skills.py` 的 5-family system）共存但不覆寫：
- Outer Policy = 高層策略參數（source/analysis/report/evaluation/improvement policies）
- Task Skill = 可執行的技能元件，帶有 risk/dependency/lifecycle governance

## 2. Identity Schema

### 2.1 skill

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `skill_id` | string | yes | 唯一識別（e.g. `analysis-fundamental`, `report-markdown-gen`） |
| `family` | enum | yes | `source` \| `analysis` \| `report` \| `evaluation` \| `improvement` |
| `name` | string | yes | 人類可讀名稱 |
| `description` | string | yes | 用途說明 |
| `risk_class` | enum | yes | 見 §4 Risk Classification |
| `side_effect_class` | string | yes | 副作用類型描述 |
| `verification_preconditions` | list[string] | yes | 執行前置條件 |
| `verification_postconditions` | list[string] | yes | 執行後置條件 |
| `lifecycle` | enum | yes | 見 §5 Lifecycle |
| `created_at` | ISO 8601 | yes | 建立時間 |

### 2.2 skill_revision

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `revision_hash` | SHA-256 | yes (PK) | content-addressed hash |
| `skill_id` | string (FK) | yes | 所屬 skill |
| `content` | JSON | yes | 完整 skill 定義 snapshot（immutable） |
| `is_active` | boolean | yes | 最多一個 active revision per skill |
| `created_at` | ISO 8601 | yes | 建立時間 |

### 2.3 skill_dependency

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `from_skill_id` | string (FK) | yes | 依賴方 |
| `to_skill_id` | string (FK) | yes | 被依賴方 |
| `relation` | enum | yes | `requires` \| `optional` \| `conflicts` |
| `created_at` | ISO 8601 | yes | 建立時間 |

**Constraints**:
- `from_skill_id ≠ to_skill_id`（禁止 self-cycle）
- Transitive cycle detection（`requires` edges, depth ≤ 10）
- Duplicate `(from, to, relation)` 禁止

## 3. Immutable Revision

```
revision_hash = SHA-256(canonical_json(content))
```

一旦 revision 寫入：
- `content` 欄位不可修改
- 相同 hash + 相同 content → 冪等（no-op）
- 相同 hash + 不同 content → hash collision error（reject）

要修改 skill 行為 → 建立新 revision → 切換 active pointer。

每個 skill 最多有一個 `is_active=true` 的 revision。

## 4. Risk Classification

| Level | Class | Approval | 範例 |
|-------|-------|----------|------|
| 0 | `read_only` | never | 讀取 DB、查詢 API |
| 1 | `local_write` | conditional | 寫入本地 cache、更新 telemetry |
| 2 | `external_write` | always | 發 webhook、寫外部 API |
| 3 | `deploy_or_release` | always + security | 部署、模型上線、schema migration |

Level 2+ 在 MVP 中一律需要人工 approval。

## 5. Lifecycle State Machine

```
draft ──→ staged ──→ active ──→ frozen ──→ retired
                       ↑           │
                       └───────────┘ (unfreeze: approval required)
```

| Status | 意義 | 可被選擇？ |
|--------|------|-----------|
| `draft` | 開發中 | No |
| `staged` | 準備 review | No |
| `active` | 正式可用 | Yes |
| `frozen` | 暫時凍結 | No |
| `retired` | 永久下架 | No |

**高風險 skill**（`external_write` / `deploy_or_release`）不可直接
`draft → active`，必須經過 `staged` 且有 approval。

## 6. Dependency Graph

- `requires`: 硬依賴——被依賴方必須 active 且有 valid revision
- `optional`: 軟依賴——有則用，無則 skip
- `conflicts`: 互斥——兩者不可同時出現在同一 run manifest

Cycle detection 只針對 `requires` edges（構成 DAG）。
`optional` 和 `conflicts` 不構成循環風險。

## 7. Frozen Manifest（Per-Run）

每次 analysis run 開始時，凍結所有選定 skill 的 exact revision hash：

```
FrozenSkillManifest = {
    run_id: UUID,
    entries: [{skill_id, revision_hash, reason}],
    created_at: ISO 8601,
}
```

凍結後：
- 即使 active pointer 被切換，該 run 仍使用原始 revision
- Manifest 持久化，可用於 replay verification

## 8. 與 Outer Policy Family 的關係

| 面向 | Outer Policy (`skills.py`) | Task Skill (`skill_registry.py`) |
|------|----------------------------|----------------------------------|
| 粒度 | 5 families, 策略參數 | N skills, 可執行元件 |
| Governance | append-only JSONL log | DB lifecycle + approval |
| Immutability | artifact hash | revision hash |
| Scope | policy knobs (weights, budgets) | executable behavior |

**共存規則**：
- Task Skill 不修改 `SKILL_FAMILIES` / `FORBIDDEN_FAMILIES`
- Task Skill 不覆寫 Outer Policy 的 active revision
- 兩者可引用相同的 family enum，但語義不同

## 9. 禁止事項

| 禁止行為 | 原因 |
|----------|------|
| Skill output 覆寫 Trust Kernel weights/formula | Kernel immutability |
| Skill output 修改 security policy | Security boundary |
| Skill output 修改 cost/budget limits | Cost governance |
| Skill output 觸發 deployment/activation | Deployment approval gate |
| Self-cycle dependency | Logical impossibility |
| Skip staged for high-risk activation | Governance requirement |

## 10. 與 Trust Kernel 的邊界

Task Skill 位於 Agent OS 層（Layer 3 infra），不可 import Trust Kernel：

```
Task Skill Registry
       │
       ↓ (provides skill metadata & frozen revision)
Context Builder ──→ Runtime ──→ Orchestrator
                                     │
                                     ↓
                            Trust Kernel (IMMUTABLE)
                            - weights unchanged
                            - formula unchanged
                            - evidence binding unchanged
```
