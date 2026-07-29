# 實作任務：Agent OS Architecture Contracts

> Issue: #915 | Epic: #914

## Task 1: 建立 docs/contracts/ 目錄與 Memory OS Contract

- [x] 建立 `docs/contracts/` 目錄
- [x] 撰寫 `docs/contracts/MEMORY-OS-CONTRACT.md`
  - 定義 memory_entry schema（identity, kind, provider, hash, timestamps）
  - 定義 evidence_eligible 預設行為（fail-closed, default=false）
  - 列出成為 Evidence 的必要條件
  - 明確禁止 historical conclusions 進入 scoring
  - 定義 memory_link schema（relationship edges）
  - 定義 retrieval lineage 欄位（run_id, rank, reason）
  - 定義 validity window 語義
- [ ] Cross-link to TRUST-KERNEL-BOUNDARY.md
- [ ] Cross-link to Epic #914

## Task 2: 撰寫 Task Skill Contract

- [x] 撰寫 `docs/contracts/TASK-SKILL-CONTRACT.md`
  - 定義 skill identity schema（skill_id, family, name, version）
  - 定義 immutable revision（content-addressed SHA-256）
  - 定義 dependency edge schema（requires, optional, conflicts）
  - 定義 risk classification 四級（read_only → deploy_or_release）
  - 定義 side-effect class 與 verification contract
  - 定義 lifecycle status enum（draft → staged → active → frozen → retired）
  - 明確列出與既有 5 outer-policy family 的共存關係
  - 明確禁止 skill output 覆寫 Trust Kernel / security / cost / deploy
- [ ] Cross-link to Memory OS Contract + Tool Capability Contract
- [ ] Cross-link to existing `SKILL-CHANGE-CONTROL.md`

## Task 3: 撰寫 Tool Capability Contract

- [x] 撰寫 `docs/contracts/TOOL-CAPABILITY-CONTRACT.md`
  - 定義 tool_capability schema（identity, side-effect, evidence class, approval）
  - 定義 invocation audit schema（input/output hash, status, error, evidence_refs）
  - 定義 approval requirement rules
  - 明確 unknown tool → fail closed
  - 明確 `external_write` + `deploy_or_release` → always human approval
  - 明確 `context_only` output 不可進入 Evidence
  - 定義 timeout / retry policy 欄位
- [ ] Cross-link to Task Skill Contract + Context Manifest Contract

## Task 4: 撰寫 Context Manifest Contract

- [x] 撰寫 `docs/contracts/CONTEXT-MANIFEST-CONTRACT.md`
  - 定義 manifest schema（identity, run_id, content_hash, token budget）
  - 定義 included_refs 結構（snapshot, question, memory, skill, tool, policy）
  - 定義 excluded_refs 結構（stale, over_budget, approval_required, evidence_ineligible）
  - 定義 immutability guarantee（freeze-on-create）
  - 定義 deterministic hash 計算方式
  - 定義 Report / Admin summary 揭露義務
- [ ] Cross-link to all other three contracts

## Task 5: 建立 Backlog Index

- [x] 建立 `docs/backlog/` 目錄
- [x] 撰寫 `docs/backlog/AGENT-OS-BACKLOG.md`
  - H-33: Memory OS Schema & Repository (#916)
  - H-34: Task Skill Registry (#917)
  - H-35: Tool Capability Registry (#918)
  - H-36: Memory Retrieval / RAG Adapter (#919)
  - H-37: Skill Loader / Governance (#920)
  - H-38: Context Builder (#921)
- [ ] 每筆含 ID, title, status, issue ref, priority, dependencies, safety boundary
- [ ] Link to Epic #914 and development plan

## Task 6: Cross-linking 與驗證

- [ ] 四份 contract 互相 cross-link 確認（相對路徑正確）
- [ ] 各 contract link back to `TRUST-KERNEL-BOUNDARY.md`
- [ ] 各 contract link back to Epic #914
- [ ] Backlog entries link to 對應 contract
- [ ] 確認 markdown lint 通過
- [ ] 確認 pre-push gate 通過
