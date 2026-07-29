# Agent OS Architecture Contracts

> Issue: #915 | Epic: #914
> Labels: architecture, agent-os, P0
> Depends on: None (root dependency)

## 背景

TrustForge Hermes 需要一個可治理的 Agent OS 基礎層，提供 Memory、Task Skill、
Tool Capability 與 Context Manifest 的正式合約（contract），作為後續所有實作
（#916–#925）的規格基準。

現有基礎：
- `src/trustforge/skills.py` — 5 family outer-skill registry + immutable artifact hashing
- `src/trustforge/skill_changes.py` — append-only approval-gated change history
- `src/trustforge/outer_skill_policy.py` — runtime policy guard
- `docs/architecture/TRUST-KERNEL-BOUNDARY.md` — Trust Kernel 不可變邊界

## 範圍

建立四份 architecture contract 文件，定義 Agent OS 各子系統的 identity、revision、
hash、lineage、lifecycle 與 fail-closed 預設行為。同時將 H-33–H-38 加入 backlog
index。

**本 issue 為純文件產出，不包含任何程式碼、DB schema、migration 或 production wiring 修改。**

## 功能需求

### FR-1: Memory OS Contract (`docs/contracts/MEMORY-OS-CONTRACT.md`)

定義 memory entry 的：
- Identity schema（memory_id, kind, provider, content_hash）
- Validity window（published_at, retrieved_at, expires_at）
- Evidence eligibility boundary（`evidence_eligible` default=false）
- 成為 Evidence 的必要條件（provider + published/retrieved time + content hash 完整）
- Retrieval lineage 欄位（run_id, rank, reason）
- 禁止事項：historical memory 不得靜默進入 Evidence/scoring

### FR-2: Task Skill Contract (`docs/contracts/TASK-SKILL-CONTRACT.md`)

定義 task skill 的：
- Identity schema（skill_id, family, name, version）
- Immutable revision（revision_hash = SHA-256 of canonical JSON）
- Dependency edges（depends_on, required_by）
- Risk classification（read_only, local_write, external_write, deploy_or_release）
- Side-effect class
- Verification contract（pre/post conditions）
- Lifecycle status（draft, staged, active, frozen, retired）
- 與現有 5 outer-policy family 的關係（共存不覆寫）
- 禁止事項：skill outputs 不可覆寫 Trust Kernel / security / cost / deployment policy

### FR-3: Tool Capability Contract (`docs/contracts/TOOL-CAPABILITY-CONTRACT.md`)

定義 tool capability 的：
- Identity schema（tool_id, name, version）
- Side-effect classification（read_only, local_write, external_write, deploy_or_release）
- Evidence class（none, context_only, candidate_evidence, trusted_evidence）
- Approval requirement（never, always, conditional）
- Invocation audit fields（input_hash, output_hash, status, error, evidence_refs）
- Timeout / retry policy
- Owner / maintainer
- 禁止事項：unknown tool + missing policy → fail closed;
  `write_external` / `deploy_or_release` → always require human approval

### FR-4: Context Manifest Contract (`docs/contracts/CONTEXT-MANIFEST-CONTRACT.md`)

定義 per-run context manifest 的：
- Manifest identity（manifest_id, run_id, created_at, content_hash）
- Included references（snapshot, question, memory, skill, tool, policy）
- Excluded references（stale, over-budget, approval-required, evidence-ineligible）
- Token budget 計算
- Immutability guarantee（existing manifest 不受後續 memory/skill/tool/policy 更新影響）
- 與 Report / Admin summary 的揭露義務

### FR-5: Backlog Index (`docs/backlog/AGENT-OS-BACKLOG.md`)

新增 H-33–H-38 backlog entries，每筆包含：
- ID、title、status、issue ref、priority
- Dependencies
- Safety boundary notes

### FR-6: Cross-linking

四份 contract 互相引用（wikilink 或 markdown anchor），且各自 link back to：
- Epic #914
- 開發計畫文件
- `TRUST-KERNEL-BOUNDARY.md`（明確列出 Agent OS 不可碰的邊界）

## 非功能需求

- **NFR-1: 純文件** — 本 issue 不產生任何 Python/TS 程式碼、schema 或 migration
- **NFR-2: fail-closed 預設** — 所有 contract 的預設行為為拒絕/不允許
- **NFR-3: 向後相容** — 不修改既有 outer-policy / skill 行為
- **NFR-4: 可機器讀取** — contract 中的 schema 定義使用 YAML/JSON-like pseudo-code 格式

## 驗收條件

1. 四份 contract 文件存在且互相 cross-link
2. 每份 contract 定義 identity、revision/hash、lineage、lifecycle、fail-closed 預設
3. Historical memory 不可靜默進入 Evidence/scoring（明確寫入 contract）
4. High-risk skill/tool actions 需要 approval（明確寫入 contract）
5. Backlog index links 正確解析
6. `docs/` 下的 markdown lint 通過
7. 完整 pre-push gate 通過（不觸碰程式碼故主要是 docs check）
