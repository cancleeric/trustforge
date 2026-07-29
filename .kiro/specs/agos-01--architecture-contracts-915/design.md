# 設計：Agent OS Architecture Contracts

> Issue: #915 | Epic: #914

## 架構決策

### AD-1: Contract 定位——介面規格而非實作

四份 contract 是**介面規格文件**，定義 data shape、invariant 與 boundary，
但不包含具體 SQL DDL 或 Python class。具體實作由 #916–#921 各自負責。

理由：
- 單一職責：contract 只管「what」，implementation issue 管「how」
- 可在 contract 完成後平行展開 #916/#917/#918
- 變更 contract 需要 design review，但實作細節可 PR-level 決定

### AD-2: 層次關係——Agent OS 位於 Layer 3 之下

```
┌─────────────────────────────────────────────────────────┐
│  Layer 3 — Agent（orchestrator, build_report）          │
├─────────────────────────────────────────────────────────┤
│  Agent OS（Memory + Skill + Tool + Context Manifest）   │  ← NEW
├─────────────────────────────────────────────────────────┤
│  Layer 2 — Trust Kernel（scoring, Dawid-Skene）         │  IMMUTABLE
├─────────────────────────────────────────────────────────┤
│  Layer 1 — Ingestion（connectors, raw fetch）           │
└─────────────────────────────────────────────────────────┘
```

Agent OS 是 Layer 3 的**基礎設施層**，提供 context/memory/skill/tool 管理，
但不觸碰 Layer 2 Trust Kernel 的計算邏輯。

### AD-3: Evidence Eligibility — Fail-Closed 預設

Memory entries 預設 `evidence_eligible = false`。要成為 Evidence input 必須：
1. `provider` 非空
2. `published_at` 非空（原始資料的發布時間）
3. `retrieved_at` 非空（系統抓取時間）
4. `content_hash` 非空且可驗證
5. 明確設定 `evidence_eligible = true`

Historical conclusions（Agent 自己過去產出的分析結論）**永遠不可**成為 Evidence
或 scoring input，即使 hash/time 完整也不行。

### AD-4: Risk Classification — 四級分類

| Level | Side-Effect Class | Approval | 範例 |
|-------|-------------------|----------|------|
| 0 | `read_only` | never | 查詢 CoinGecko 價格、讀 DB |
| 1 | `local_write` | conditional | 寫入本地 cache、更新 telemetry |
| 2 | `external_write` | always | 發 webhook、寫外部 API |
| 3 | `deploy_or_release` | always + security review | 部署、模型上線、schema migration |

Level 2+ 在 MVP 中一律需要人工 approval，不可自動執行。
Unknown tools（未在 registry 中）→ fail closed（不可執行）。

### AD-5: Immutability — Content-Addressed Revision

所有 revision（memory entry、skill revision、context manifest）使用
content-addressed hash：

```
revision_hash = SHA-256(canonical_json(content))
```

其中 `canonical_json` 使用 `sort_keys=True, separators=(',', ':'), ensure_ascii=False`
——與現有 `skills.py::canonical_json()` 一致。

一旦寫入，revision 不可修改（只可 retire/supersede）。

### AD-6: Lineage — Run-Scoped Tracing

每次分析 run 產生一份 Context Manifest，記錄：
- 該 run 使用了哪些 memory entries（含 rank/reason）
- 選擇了哪些 skill revisions（含 reason）
- 呼叫了哪些 tool capabilities（含 input/output hash）

這些 lineage 記錄與 execution_log.jsonl 互相引用，Admin API 可查詢。

## 文件結構

```
docs/
├── contracts/
│   ├── MEMORY-OS-CONTRACT.md
│   ├── TASK-SKILL-CONTRACT.md
│   ├── TOOL-CAPABILITY-CONTRACT.md
│   └── CONTEXT-MANIFEST-CONTRACT.md
└── backlog/
    └── AGENT-OS-BACKLOG.md
```

## Schema 草案（Pseudo-code，具體 DDL 由 #916–#918 實作）

### Memory Entry Schema

```yaml
memory_entry:
  memory_id: UUID
  kind: enum(episodic, semantic, procedural, dialogue)
  provider: string          # 來源系統標識
  content_hash: SHA-256     # canonical content hash
  content_ref: string       # 指向實際內容的 URI/path
  published_at: datetime?   # 原始發布時間
  retrieved_at: datetime    # 系統抓取時間
  expires_at: datetime?     # 過期時間（optional）
  evidence_eligible: bool   # default=false
  created_at: datetime
  run_id: UUID?             # 產生此 entry 的 run（if applicable）
```

### Task Skill Schema

```yaml
skill:
  skill_id: string          # e.g. "task-analysis-fundamental"
  family: enum(source, analysis, report, evaluation, improvement)
  name: string
  description: string
  risk_class: enum(read_only, local_write, external_write, deploy_or_release)
  side_effect_class: string
  verification_contract:
    preconditions: list[string]
    postconditions: list[string]
  lifecycle: enum(draft, staged, active, frozen, retired)
  created_at: datetime

skill_revision:
  revision_hash: SHA-256    # content-addressed
  skill_id: string
  content: JSON             # immutable snapshot
  created_at: datetime
  is_active: bool           # 最多一個 active revision per skill

skill_dependency:
  from_skill_id: string
  to_skill_id: string
  relation: enum(requires, optional, conflicts)
```

### Tool Capability Schema

```yaml
tool_capability:
  tool_id: string           # e.g. "coingecko-price-fetch"
  name: string
  version: string
  side_effect_class: enum(read_only, local_write, external_write, deploy_or_release)
  evidence_class: enum(none, context_only, candidate_evidence, trusted_evidence)
  approval_requirement: enum(never, always, conditional)
  timeout_sec: int
  retry_policy: {max_retries: int, backoff_sec: float}
  owner: string
  schema_input: JSON Schema
  schema_output: JSON Schema

tool_invocation:
  invocation_id: UUID
  run_id: UUID
  tool_id: string
  input_hash: SHA-256
  output_hash: SHA-256?
  status: enum(pending, success, failed, timeout, rejected)
  error: string?
  evidence_refs: list[string]?
  started_at: datetime
  completed_at: datetime?
```

### Context Manifest Schema

```yaml
context_manifest:
  manifest_id: UUID
  run_id: UUID
  created_at: datetime
  content_hash: SHA-256     # deterministic hash of full manifest
  token_budget: int
  token_used: int
  included_refs:
    snapshot_ref: string?
    question_ref: string?
    memory_refs: list[{memory_id, rank, reason}]
    skill_refs: list[{skill_id, revision_hash, reason}]
    tool_refs: list[{tool_id, version}]
    policy_refs: list[{policy_id, revision_hash}]
  excluded_refs: list[{ref_id, ref_type, reason}]
    # reasons: stale, over_budget, approval_required, evidence_ineligible
```

## 與既有系統的邊界

| 既有系統 | Agent OS 關係 | 不可改動 |
|----------|---------------|----------|
| Trust Kernel (`trust/kernel.py`) | Agent OS 完全不觸碰 | weights, formula, PIT time, evidence binding |
| Outer Skill Registry (`skills.py`) | 共存；task skill 是新增維度，不取代 outer policy | `SKILL_FAMILIES`, `FORBIDDEN_FAMILIES` |
| Skill Changes (`skill_changes.py`) | 共存；task skill lifecycle 建構在類似 governance 之上 | append-only log, active_revision pointer |
| Evidence scoring | Memory 預設不進入 scoring；只有 `evidence_eligible=true` 的才能進 pipeline | scoring formula, corroboration logic |
| Execution log | Agent OS lineage 寫入 execution_log.jsonl 為新增事件類型 | 既有事件格式不改 |

## 測試策略

本 issue 為純文件，無程式碼測試。驗證：
- Markdown lint 通過
- 所有 cross-link 可解析（相對路徑正確）
- Pre-push docs check 通過
