# Hermes 等級 Memory / Skill 能力開發計劃

> 日期：2026-07-29<br>
> 作者：Isabella<br>
> 來源報告：[`docs/reports/HERMES-LEVEL-MEMORY-SKILL-CAPABILITY-GAP-2026-07-29.md`](../reports/HERMES-LEVEL-MEMORY-SKILL-CAPABILITY-GAP-2026-07-29.md)<br>
> 狀態：待 CEO 審查後拆 issue / PR 執行<br>
> 範圍：Memory OS、Skill Registry / Loader、Context Builder、Tool Capability Registry、Agent OS UI、approval-gated self-improvement governance。<br>
> 非範圍：本計劃不修改 Trust Kernel、Trust weights、Evidence scoring、production deployment、secret / IAM、模型訓練或自動啟用策略。

---

## 1. 開發目標

本計劃把 2026-07-29 能力缺口報告轉成可拆 issue、可驗收、可逐 PR 落地的工程計劃。目標不是宣稱 TrustForge 已等同 Hermes Agent，而是建立可治理的 Agent OS 基礎層：

1. **Memory OS**：長期記憶分層、檢索、注入與 lineage，並嚴格區分 historical context 與 Evidence。
2. **Skill Registry / Loader**：保留現有 outer policy family，同時新增任務級 skill metadata、revision、dependency、risk 與 verification contract。
3. **Context Builder**：每輪 run 自動凍結 memory、skill、tool、policy、snapshot、question 的 context manifest。
4. **Tool Capability Registry**：登錄 tool 的 side effect、approval、evidence class、timeout / retry 與 invocation audit。
5. **Agent OS UI**：在 HermesDashboard / Admin view 顯示 memory / skill / tool / context rail，讓使用者看懂「脈絡、候選證據、正式 Evidence、proposal」的差異。

成功標準：TrustForge 能對每份報告回答「本次用了哪個 snapshot、哪些 memory、哪些 skill、哪些 tool、哪些 policy；哪些只作脈絡、哪些可以作 Evidence；哪些能力需要 sandbox / 人審才可 activation」。

---

## 2. 原則與邊界

### 2.1 必守原則

| 原則 | 要求 |
|---|---|
| Historical context is not Evidence | 歷史回答、對話、SOP、偏好預設不可進 Evidence / Trust scoring；只能作 continuity / coverage context。 |
| Immutable run manifest | run 開始後 memory / skill / tool / policy refs 必須凍結，後續 active pointer 變更不得改寫既有 run。 |
| Approval before activation | 高風險 skill / tool / upgrade 只能 observe → propose → sandbox → human approval → activation。 |
| Evidence-first | 可作 Evidence 的資料必須有 provider、URL 或來源 ref、license / terms、published_at、retrieved_at、content_hash 與 PIT eligibility。 |
| No false autonomy | UI / 文件不得宣稱 AI 可自行修改 production、Trust weights、Evidence binding、deployment 或 secrets。 |
| Backward compatible | 既有 question RAG、outer skill registry、upgrade queue 不重寫成黑箱；以 migration / adapter 漸進收斂。 |

### 2.2 明確不做

- 不把 Hermes Agent 或外部專案程式碼直接搬進 TrustForge。
- 不讓 memory 修改 deterministic Trust scoring。
- 不讓 task skill 繞過 Trust Kernel / Evidence boundary。
- 不讓 tool registry 成為任意執行 production side effect 的後門。
- 不把 AgentCore optional memory port 宣稱為產品內建 Memory OS。
- 不在缺少 CPO / CISO / codex-review / pre-push gate 時宣稱 release-ready。

---

## 3. Issue / PR 拆分總覽

建議拆成 7 個 issue、至少 9 個 PR。每個 PR 必須小到能獨立驗證；不得用一個巨型 PR 同時改 schema、runtime、UI 與 approval policy。

| Issue | 優先 | 主題 | 建議 PR | 核心驗收 |
|---|---:|---|---:|---|
| AGOS-01 | P0 | Architecture contracts | 1 | 四份 contract 文件存在並交叉連到來源報告與本計劃。 |
| AGOS-02 | P0 | Memory OS schema + migration | 2 | `memory_entries` / `memory_links` schema、migration、tests；預設不可作 Evidence。 |
| AGOS-03 | P0 | Memory retrieval lineage + question RAG migration adapter | 2 | question RAG / dialogue memory 可映射到正式 memory kind；run log 留 retrieval lineage。 |
| AGOS-04 | P0 | Task Skill Registry / Loader MVP | 2 | task skill metadata / revisions / dependencies / frozen skill manifest；高風險 skill 不可 auto activate。 |
| AGOS-05 | P0 | Context Builder MVP | 1 | 每輪 run 產生 immutable `context_manifest`；hash 可重讀；memory 不進 scoring input。 |
| AGOS-06 | P0 | Tool Capability Registry MVP | 1 | tool capabilities / invocations schema；side effect / evidence class / approval / hashes 可查。 |
| AGOS-07 | P1 | Agent OS UI / Admin IA | 2 | memory / skill / tool / context rails；UI 明確標示 context-only / candidate / Evidence / proposal。 |

---

## 4. Phase 0：文件與契約先行（P0）

### 4.1 交付物

新增四份 architecture contract：

| 檔案 | 內容 |
|---|---|
| `docs/architecture/MEMORY-OS-CONTRACT-2026-07-29.md` | memory kind、evidence eligibility、PIT rules、retrieval lineage、migration from question RAG。 |
| `docs/architecture/SKILL-REGISTRY-CONTRACT-2026-07-29.md` | outer policy family vs task skill registry、metadata、revision、dependency、verification、approval class。 |
| `docs/architecture/CONTEXT-BUILDER-CONTRACT-2026-07-29.md` | context manifest schema、token budget、refs / excluded refs、immutability、hashing、report disclosure。 |
| `docs/architecture/TOOL-CAPABILITY-REGISTRY-CONTRACT-2026-07-29.md` | tool metadata、side effect class、evidence class、approval required、invocation audit、retry / timeout。 |

同步更新：

- `docs/architecture/HERMES-CONTINUOUS-INTELLIGENCE-2026-07-16.md`：補 Agent OS extension 段落，但不改既有 continuous pipeline contract。
- `docs/plans/HERMES-AGENT-DELIVERY-BACKLOG-2026-07-13.md`：新增 H-33～H-38 駐列，標記為 Agent OS 能力層；舊 H-07 / H-23 不改成未完成，只註明「已完成第一版，下一階段升級由 H-33～H-38 承接」。
- `docs/README.md`：加入本開發計劃與四份 contract 索引。

### 4.2 驗收

- 文件明確引用來源報告。
- 每份 contract 都包含：schema 草案、non-goals、approval boundary、test requirements、UI disclosure requirements。
- `historical_context != Evidence` 至少在 Memory OS contract 與 Context Builder contract 各出現一次，且文字一致。
- `python` / markdown lint 或可用文件檢查通過；`git diff --check origin/main...HEAD` 通過。

---

## 5. Phase 1：Memory OS MVP（P0）

### 5.1 Scope

建立正式 memory 層，不取代 Evidence store，不取代 immutable snapshot。Memory OS 只負責長期脈絡、對話、程序與可治理知識；Evidence memory 只能在通過 Evidence contract 後作為候選 Evidence。

### 5.2 Schema 草案

```text
memory_entries(
  memory_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  scope TEXT NOT NULL,
  subject TEXT NOT NULL,
  content TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  created_at TEXT NOT NULL,
  valid_from TEXT,
  valid_until TEXT,
  evidence_eligible INTEGER NOT NULL DEFAULT 0,
  confidence REAL,
  content_hash TEXT NOT NULL
)

memory_links(
  memory_id TEXT NOT NULL,
  run_id TEXT,
  snapshot_id TEXT,
  question_id TEXT,
  relation TEXT NOT NULL,
  retrieved_rank INTEGER,
  retrieval_reason TEXT,
  created_at TEXT NOT NULL
)
```

`kind` 初始允許值：

| kind | 用途 | evidence_eligible 預設 |
|---|---|---:|
| `episodic` | 歷史對話、run、操作結果 | false |
| `semantic` | 專案事實、規則、長期知識 | false |
| `procedural` | SOP / skill 使用規則 | false |
| `evidence` | source row / content hash / PIT-safe raw evidence pointer | false，通過 contract 才能 true |
| `preference_policy` | 使用者偏好、approval policy、風險界線 | false |

### 5.3 實作步驟

1. 新增 SQLite migration 與資料存取層。
2. 新增 memory kind / evidence eligibility dataclass 或 pydantic-like contract。
3. 將現有 `analysis_conversation` / question RAG 結果透過 adapter 映射為 `episodic` / `semantic` memory ref，不直接破壞既有 API。
4. retrieval 時寫入 `memory_links`，包含 rank 與 reason。
5. run execution log 增加 `retrieval.memory` event。
6. 報告 payload 增加 memory disclosure：`historical_context_count`、`evidence_memory_count`、`used_as_evidence_count`。

### 5.4 測試

- `test_memory_entries_default_non_evidentiary`：新增 memory 預設不可作 Evidence。
- `test_evidence_memory_requires_source_contract`：缺 provider / published_at / retrieved_at / content_hash 時不得 evidence_eligible。
- `test_question_rag_retrieval_writes_memory_lineage`：召回歷史問題時寫入 `memory_links`。
- `test_retrieved_memory_not_in_trust_scoring_input`：歷史回答被召回，也不出現在 scoring input / Evidence list。
- `test_chinese_retrieval_not_whitespace_only`：中文查詢可用 char n-gram 或等價策略召回。

---

## 6. Phase 2：Skill Registry / Loader MVP（P0）

### 6.1 Scope

保留 `src/trustforge/skills.py` 的五個 outer policy family，新增 task skill registry。outer policy family 是受控外框 policy artifact；task skill 是程序知識 / SOP / workflow，不可覆蓋核心判斷權。

### 6.2 Schema 草案

```text
skills(
  skill_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  trigger_query TEXT,
  family TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  side_effect_class TEXT NOT NULL,
  input_schema TEXT,
  output_schema TEXT,
  verification_contract TEXT NOT NULL,
  current_revision TEXT,
  status TEXT NOT NULL
)

skill_revisions(
  skill_id TEXT NOT NULL,
  revision_hash TEXT NOT NULL,
  content TEXT NOT NULL,
  author TEXT NOT NULL,
  created_at TEXT NOT NULL,
  sandbox_status TEXT NOT NULL,
  approved_by TEXT,
  activated_at TEXT,
  PRIMARY KEY(skill_id, revision_hash)
)

skill_dependencies(
  skill_id TEXT NOT NULL,
  depends_on_skill_id TEXT NOT NULL,
  version_constraint TEXT
)
```

### 6.3 實作步驟

1. 新增 task skill schema / repository。
2. 實作 skill discovery：依 `trigger_query`、family、risk、status 回傳候選。
3. run 開始時 freeze selected skill revisions，寫入 skill manifest。
4. skill output 只可產生 instruction / context / checklist / proposal，不可直接改 Trust Kernel / Evidence scoring。
5. skill update 走既有 proposal → sandbox → approval → activation 控制面，新增 skill-specific diff 與 sandbox result 欄位。
6. stale detection：skill revision 若測試失敗、依賴失效或文件超過指定有效期，標記 `stale`，不得自動載入。

### 6.4 測試

- `test_outer_policy_family_still_immutable`：既有 outer skill hash / active revision 行為不退步。
- `test_task_skill_loader_freezes_revision_per_run`：active pointer 改變不影響既有 run。
- `test_high_risk_skill_requires_approval`：高風險 skill 沒 sandbox / approval 不可 activation。
- `test_skill_dependency_resolution_records_manifest`：依賴 skill revision 被記入 manifest。
- `test_skill_cannot_override_trust_kernel`：任何 skill family 不得設定 core / deploy / security / cost override。

---

## 7. Phase 3：Context Builder MVP（P0）

### 7.1 Scope

Context Builder 是 run 的「組裝器」，負責把 immutable snapshot、question、memory、skill、tool、policy 與 token budget 凍結成 context manifest。它不負責 scoring，也不替代 Evidence assembly。

### 7.2 Schema 草案

```text
context_manifests(
  run_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  question_id TEXT,
  memory_refs TEXT NOT NULL,
  skill_refs TEXT NOT NULL,
  tool_refs TEXT NOT NULL,
  policy_refs TEXT NOT NULL,
  excluded_refs TEXT NOT NULL,
  token_budget INTEGER NOT NULL,
  assembly_reason TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
)
```

### 7.3 實作步驟

1. 新增 `ContextBuilder` service，輸入：run_id、snapshot_id、question_id、mode、coin。
2. 從 Memory OS 取 relevant context-only refs；從 Skill Registry 取 applicable skills；從 Tool Registry 取 active capabilities；從 policy registry 取 evidence / approval policy。
3. 建立 `excluded_refs`，明確記錄為何某些 memory / skill / tool 未納入（例如 stale、over budget、approval required、evidence ineligible）。
4. 計算 deterministic content hash。
5. run log 記錄 `context_manifest.created`。
6. report / Admin API 可讀 context manifest summary。

### 7.4 測試

- `test_context_manifest_is_immutable_after_run_start`。
- `test_memory_skill_policy_updates_do_not_change_existing_manifest`。
- `test_context_manifest_hash_reproducible`。
- `test_context_builder_excludes_evidence_ineligible_memory_from_evidence_inputs`。
- `test_report_discloses_historical_context_count_without_evidence_claim`。

---

## 8. Phase 4：Tool Capability Registry MVP（P0）

### 8.1 Scope

登錄 TrustForge 內部可被 agent / worker / scheduler 呼叫的能力，明確說明是否可讀 production、是否有 side effect、是否需要 approval、輸出是否可作 Evidence。

### 8.2 Schema 草案

```text
tool_capabilities(
  tool_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  input_schema TEXT,
  output_schema TEXT,
  side_effect_class TEXT NOT NULL,
  evidence_class TEXT NOT NULL,
  approval_required INTEGER NOT NULL,
  timeout_sec INTEGER NOT NULL,
  retry_policy TEXT NOT NULL,
  owner_module TEXT NOT NULL,
  status TEXT NOT NULL
)

tool_invocations(
  invocation_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  tool_id TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  output_hash TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL,
  error_class TEXT,
  evidence_refs TEXT NOT NULL
)
```

`side_effect_class`：`read_only`、`write_local_state`、`write_external`、`deploy_or_release`。
`evidence_class`：`none`、`context_only`、`candidate_evidence`、`trusted_evidence`。

### 8.3 首批登錄工具

| Tool 類型 | 初始能力 | 預設 evidence_class | 預設 approval |
|---|---|---|---|
| Source crawler | 讀取外部資料來源、產生 source rows | `candidate_evidence` | 依來源憑證 / terms |
| Snapshot builder | 建立 immutable snapshot | `trusted_evidence` when contract passes | no |
| Question retrieval | 召回歷史問題 / 對話 | `context_only` | no |
| Report worker | 產出報告 | `none`，Evidence 由 assembly refs 決定 | no |
| Replay worker | PIT replay | `candidate_evidence` | no，若昂貴則需 cost approval |
| Upgrade sandbox | 測試候選 artifact | `none` | no |
| Activation API | 啟用 approved revision | `none` | yes |
| Deployment / release | 推進 production | `none` | yes + release gate |

### 8.4 測試

- `test_tool_capability_defaults_fail_closed`：未知 tool 不可執行。
- `test_write_external_tool_requires_approval`。
- `test_tool_invocation_hashes_input_output`。
- `test_candidate_evidence_requires_contract_before_report_evidence`。
- `test_context_only_tool_output_never_enters_evidence`。

---

## 9. Phase 5：Agent OS UI / IA（P1）

### 9.1 IA 設計

| 區塊 | 顯示內容 | 目的 |
|---|---|---|
| Left rail：Memory | recalled memory、kind、evidence eligibility、lineage、為何被選中 | 避免歷史脈絡與本次證據混淆。 |
| Center：Report | selected complete report、Evidence、Trust explanation | 保持核心報告主視覺，不被治理 rails 擠掉。 |
| Right rail：Skill / Tool / Trust decomposition | active skill revision、tool side effect、approval status、trust decomposition | 讓 agent 做法與工具執行可審查。 |
| Bottom rail：Run lineage | stage telemetry、queue、context manifest hash、snapshot / question / replay | 證明 run 可重現。 |
| Admin：Approval / Sandbox | proposal、sandbox proof、decision、activation、rollback | 高風險能力的人審與回退控制。 |

### 9.2 實作步驟

1. Admin API：新增 context manifest / memory refs / skill refs / tool refs summary endpoint。
2. HermesDashboard：在現有控制面新增 collapsible rails，避免擠壓核心報告。
3. UI badge：`context_only`、`candidate_evidence`、`trusted_evidence`、`proposal`、`approved_not_activated`。
4. Mobile：rails 預設收合，核心報告優先。
5. Eye scan：desktop 與 mobile 各至少一張截圖或文字驗證，檢查 overflow、loading、empty、error、retained-last-good。

### 9.3 測試

- Frontend unit：badge、rail collapse、empty state、long hash overflow。
- API test：manifest summary 欄位齊備且不洩漏 secret。
- Browser smoke：desktop / mobile 都能看見 report；治理 rails 不遮擋核心內容。
- Honesty copy test：不得出現「AI automatically upgrades production」或「memory used as evidence」等禁語。

---

## 10. Review / Security / Cost Gate

| 變更類型 | Gate |
|---|---|
| 文件與 architecture contract | CPO review；`git diff --check`；必要時 docs smoke。 |
| Memory / context schema | CPO + harper review；migration backward compatibility tests；pre-push。 |
| Skill registry / loader | CPO + harper review；sandbox / approval tests；pre-push；/codex-review。 |
| Tool registry | harper 必審；side effect / approval fail-closed tests；/codex-review。 |
| UI rails | CPO review；frontend tests / build；desktop + mobile eye scan。 |
| Activation / deployment 相關 | release governance gate；不得自動啟用；需人工 approval evidence。 |

所有 PR 必須記錄：

- 基準 SHA。
- 變更檔案清單。
- 測試命令與實際輸出摘要。
- 未執行 gate 與原因。
- reviewer finding / fix / disposition。
- 若涉及安全或成本，明確列出 harper review 狀態。

---

## 11. Milestone 驗收門檻

### M0：Plan accepted

- 本計劃入庫並連到來源 gap report。
- README 索引可發現。
- CEO 決定是否拆 issue。

### M1：Contracts accepted

- 四份 architecture contract 入庫。
- H-33～H-38 駐列入 backlog。
- 任何對外說法仍維持「下一階段收斂」，不宣稱已完成。

### M2：Backend Agent OS primitives

- Memory OS、Skill Registry、Context Builder、Tool Registry schema 與 repository tests 通過。
- retrieved historical conclusions 不能進 Evidence / Trust scoring 的 regression test 通過。
- run manifest 能固定 memory / skill / tool / policy refs。

### M3：Runtime integration

- continuous analysis run 會產生 context manifest。
- tool invocations 會寫入 audit。
- skill selected reason / revision 被記入 execution log。
- existing question RAG / dialogue memory 不退步。

### M4：UI / Admin visibility

- 使用者可在 UI 分辨 historical context、candidate evidence、trusted Evidence、proposal。
- desktop / mobile eye scan 通過。
- UI 不宣稱 false autonomy。

### M5：Release candidate

- `.githooks/pre-push` 通過。
- `git diff --check origin/main...HEAD` 通過。
- `/codex-review` finding 全部處置。
- CPO / CISO 必要審查完成。
- release note 明確列出已完成、未完成與外部 gate。

---

## 12. 建議執行順序

```text
PR-0 docs plan
  -> PR-1 architecture contracts + backlog index
  -> PR-2 Memory OS schema + repository
  -> PR-3 Memory retrieval lineage adapter
  -> PR-4 Task Skill Registry schema + loader
  -> PR-5 Context Builder + manifest hash
  -> PR-6 Tool Capability Registry + invocation audit
  -> PR-7 Runtime integration across analysis run
  -> PR-8 Admin API + UI rails
  -> PR-9 replay / E2E / release-hardening
```

可以平行：

- PR-2 Memory OS 與 PR-4 Skill Registry 可在 contracts 定稿後分支平行開發。
- PR-6 Tool Registry 可與 PR-5 Context Builder 平行，但 runtime integration 要等兩者 API 穩定。
- UI PR 不應早於 manifest / summary API，避免 mock-only UI 被誤認為完成。

不可平行或不可提前：

- Context Builder 不可早於 Memory / Skill / Tool refs 的最小 contract。
- Activation API 變更不可早於 approval policy 與 harper review。
- Release candidate 不可早於 backend primitives + UI disclosure + regression tests。

---

## 13. Open questions for CEO / CPO

1. 是否把這條線列為比賽後核心產品主線，或只作架構投影片與後續 roadmap？
2. Skill Registry 是否要與 HurricaneSoft SkillHub 對接，還是先保持 TrustForge repo 內獨立 registry？
3. Memory OS 是否要支援跨部署 export / import，或先限於單一 SQLite deployment？
4. Tool Registry 的 `write_external` / `deploy_or_release` 是否一律 require human approval，或允許某些 read-only external write（例如 append-only audit）在 sandbox 後放行？
5. UI rails 第一版要放在現有 HermesDashboard，還是拆 Admin-only 入口避免干擾評審 demo？

---

## 14. 本文件邊界

本文件是開發計劃，不是 completion report。本文入庫後代表 gap report 已轉成可執行路線；不代表 Memory OS、Skill Registry、Context Builder、Tool Registry 或 Agent OS UI 已完成。
