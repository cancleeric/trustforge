# Hermes 等級 Memory / Skill 能力缺口報告

> 日期：2026-07-29<br>
> 類型：架構缺口報告 / 下一階段產品藍圖<br>
> 範圍：TrustForge 對標 Hermes Agent 等級的 memory、skill、context assembly、tool orchestration、lineage、approval-gated self-improvement。<br>
> 結論摘要：TrustForge 已有連續分析、SQLite lineage、question RAG、外框 policy family、升級治理與 AgentCore memory port 的雛形；但距離 Hermes 等級仍缺少正式 Memory OS、Skill Registry / Loader、Context Builder、Tool Capability Registry 與跨 session 可治理的助理級工作記憶。

---

## 1. 第一性結論

如果 TrustForge 的目標只是「產生 crypto trust report」，現有 continuous analysis pipeline 已經接近核心需求；但如果目標是 **Hermes 等級的 trust intelligence agent framework**，目前還不能只靠 pipeline、RAG 與報告 worker 支撐。

Hermes 等級代表系統具備：

1. **可搜尋、可注入、可治理的長期記憶**。
2. **可載入、可版本化、可審查的技能 / 程序知識**。
3. **每次任務自動組裝上下文，而不是只讀單一 prompt 或單一 RAG 結果**。
4. **工具、資料來源、模型與 worker 的能力目錄與權限邊界**。
5. **跨 run / session / snapshot 的 lineage 與 audit trail**。
6. **只能提出改善、經 sandbox 與人審後才能啟用的受控 self-improvement**。

TrustForge 目前已具備部分基礎，但能力仍散落在不同模組，尚未形成完整「Agent OS」。

---

## 2. 現有可對應能力

| Hermes 等級能力 | TrustForge 已有證據 | 判斷 |
|---|---|---|
| 連續分析服務 | `AnalysisFlow`、snapshot / stage / journey API、pipeline worker 概念；文件要求 UI polling 不可 enqueue work | 已有基礎，需強化矩陣併發與觀測證據 |
| 問題 / 對話記憶 | `question-rag` / `rag-index` / `rag-reranker` 被納入 upgrade control module；SkillHub 契約要求 SQLite dialogue memory 與 run/snapshot lineage | 有方向，但尚未等同完整 Memory OS |
| 外框 Skill / Policy family | `src/trustforge/skills.py` 定義 `source`、`analysis`、`report`、`evaluation`、`improvement` 五個 family，artifact hash 與 active revision | 有受控 policy artifact，不是完整 Hermes skill system |
| 升級治理 | `upgrade_queue.py`、`upgrade_control.py`、`OUTER-FRAMEWORK-UPGRADE-GOVERNANCE` 定義 proposal / review / sandbox / decision / activation / rollback | 架構成熟，但需補實際閉環證據 |
| AgentCore memory port | `src/trustforge/agent/agentcore_memory.py` 以 env gate 建立 optional memory session manager | 目前是可選 port，預設 disabled，不是產品內建 memory |
| Evidence / snapshot lineage | TrustForge 核心文件持續要求 immutable snapshot、content hash、published_at / retrieved_at、Evidence 不可被歷史結論污染 | 核心觀念正確，需延伸到 memory / skill / tool lineage |

---

## 3. 缺口總覽

| 缺口 | 嚴重度 | 為什麼重要 | 目前風險 |
|---|---:|---|---|
| Memory OS 未成型 | P0 | Hermes 等級助理必須知道「過去做過什麼、哪些可作上下文、哪些不能作 Evidence」 | question RAG 容易被誤認為完整記憶；歷史結論若治理不清，可能污染 Trust scoring |
| Skill Registry / Loader 不完整 | P0 | Skill 是程序知識，不只是 policy artifact；需要 trigger、版本、依賴、風險、驗收與 stale detection | 目前 outer skills 只覆蓋五個 policy family，缺任務級 SOP 與技能可發現性 |
| Context Builder 缺席 | P0 | 每輪分析應自動組 memory + skill + tools + live data + policy + question | 現況容易變成「prompt + RAG」而非真正 agent context |
| Tool Capability Registry 缺席 | P0 | Agent 需要知道工具能做什麼、是否有 side effect、是否需要 approval、輸出能否當 Evidence | worker / API / data source 雖存在，但未形成統一可治理能力目錄 |
| Memory / Skill / Tool lineage 未統一 | P1 | 報告要能解釋「用了哪份記憶、哪個 skill、哪個 tool、哪個 snapshot」 | 現有 snapshot lineage 強，agent 決策 lineage 不足 |
| Human approval policy 未產品化到所有高風險能力 | P1 | Hermes 等級不是自動亂改，而是 observe → propose → sandbox → approval → activation | upgrade 方向正確，但 memory / skill / tool 層的 approval class 仍需明文化 |
| UI 資訊架構未完全對應 Agent OS | P1 | 使用者要看懂 memory、skill、tool、queue、Evidence、snapshot 與 proposal 的關係 | 現有 HermesDashboard 偏控制面，缺 agent context / memory / skill 可視化 |

---

## 4. P0-1：Memory OS

### 4.1 現況

TrustForge 已經有 question RAG / dialogue memory 的架構語意，並且文件要求：

- 使用 SQLite 持久化問題、回答、run / snapshot lineage。
- 支援中文檢索，不能只靠 whitespace tokenization。
- retrieved historical conclusions 只能提供 continuity / coverage context。
- 歷史結論不得進入 Evidence，不得改 deterministic Trust scoring。

另外，AgentCore memory port 已存在，但目前是 optional、env-gated：

```text
TRUSTFORGE_AGENTCORE_MEMORY_ENABLED=true
TRUSTFORGE_AGENTCORE_MEMORY_ID=...
```

未啟用時 `build_memory_session_manager()` 回 `None`。

### 4.2 缺口

目前 memory 還不像 Hermes 的完整持久記憶層，缺少明確分層：

| Memory 類型 | 用途 | 是否可進 Evidence | 需要 lineage |
|---|---|---:|---:|
| Episodic memory | 歷史對話、run、操作結果、使用者問題 | 否 | 是 |
| Semantic memory | 長期知識、專案事實、規則、來源能力 | 視來源而定，預設否 | 是 |
| Procedural memory | 做事方法、SOP、skill 使用規則 | 否 | 是 |
| Evidence memory | source rows、content hash、published_at、retrieved_at | 可，但必須 PIT-safe | 是 |
| Preference / policy memory | 使用者偏好、approval policy、風險界線 | 否 | 是 |

### 4.3 必要設計

建議新增 `memory_entries` 與 `memory_links` 類型契約：

```text
memory_entries(
  memory_id,
  kind,
  scope,
  subject,
  content,
  source_type,
  source_ref,
  created_at,
  valid_from,
  valid_until,
  evidence_eligible,
  confidence,
  content_hash
)

memory_links(
  memory_id,
  run_id,
  snapshot_id,
  question_id,
  relation,
  retrieved_rank,
  retrieval_reason
)
```

最低要求：

1. 每筆 memory 必須標示 `kind` 與 `evidence_eligible`。
2. 預設 memory 不可作為 Evidence。
3. Evidence memory 必須有 provider、URL、license / terms、published_at、retrieved_at、content_hash。
4. 每次注入 context 的 memory 都要留下 retrieval lineage。
5. UI 要清楚標示「歷史脈絡」與「本次證據」不同。

---

## 5. P0-2：Skill Registry / Loader

### 5.1 現況

TrustForge 已有 `src/trustforge/skills.py`，目前定位是 **immutable outer-skill registry for Hermes formal runs**。它支援：

- 五個 outer policy family：`source`、`analysis`、`report`、`evaluation`、`improvement`。
- artifact hash。
- active revision resolution。
- 禁止 `deploy`、`core`、`security`、`cost` family 透過外框自動執行。
- run 開始時固定 skill manifest，避免中途 active pointer 改寫 audit trail。

這是好的治理基礎，但它更像「policy artifact」，還不是 Hermes / SkillHub 那種「任務技能」。

### 5.2 缺口

完整 skill system 應該包含：

| 能力 | 目前狀態 | 缺口 |
|---|---|---|
| Skill metadata | family + rules | 缺 description、trigger、risk、inputs、outputs、verification |
| Skill discovery | 無正式查詢語意 | 缺搜尋 / 排序 / 適用條件 |
| Skill dependency | 未見 dependency graph | 缺 depends_on 與版本相容性 |
| Skill loader | 可 resolve active family artifact | 缺依任務動態載入 task skill |
| Skill execution trace | run manifest 有基礎 | 缺「哪個 skill 為何被選中、產出什麼」 |
| Stale detection | 未制度化 | 缺 skill 失效、測試失敗、文件過期提醒 |
| Skill update proposal | 外框 proposal 有方向 | 缺 skill-specific proposal / diff / sandbox / approval |

### 5.3 必要設計

建議把現有 outer policy family 保留為「受控外框 policy」，另新增 task skill registry：

```text
skills(
  skill_id,
  name,
  description,
  trigger_query,
  family,
  risk_level,
  side_effect_class,
  input_schema,
  output_schema,
  verification_contract,
  current_revision,
  status
)

skill_revisions(
  skill_id,
  revision_hash,
  content,
  author,
  created_at,
  sandbox_status,
  approved_by,
  activated_at
)

skill_dependencies(
  skill_id,
  depends_on_skill_id,
  version_constraint
)
```

關鍵規則：

1. Skill 可以建議行為，但不得繞過 Trust Kernel / Evidence boundary。
2. 高風險 skill 必須 human approval。
3. Skill 被載入與輸出都要進 execution log。
4. 任何 skill update 必須走 diff、sandbox、review、activation。
5. 生產報告要能回溯使用過的 skill revision。

---

## 6. P0-3：Context Builder

### 6.1 問題

TrustForge 如果沒有 Context Builder，就會退化成：

```text
User question + current data + RAG snippets → report
```

這不是 Hermes 等級。Hermes 等級應該是：

```text
User question
+ current immutable snapshot
+ selected coin / mode / active question package
+ relevant memory entries
+ applicable skills
+ active tool capability manifest
+ evidence eligibility policy
+ approval / side-effect policy
+ model / route constraints
+ historical lineage
→ bounded agent context
```

### 6.2 必要輸出

每次 run 應該產生 `context_manifest`：

```text
context_manifest(
  run_id,
  snapshot_id,
  question_id,
  memory_refs[],
  skill_refs[],
  tool_refs[],
  policy_refs[],
  excluded_refs[],
  token_budget,
  assembly_reason,
  content_hash
)
```

最低驗收：

1. 同一 run 的 context manifest immutable。
2. 後續 memory / skill 更新不會改寫既有 run 的 context。
3. 報告可顯示「本次使用歷史脈絡 N 筆，但 0 筆作為 Evidence」。
4. 測試必須證明 retrieved memory 不會進 Trust scoring input。

---

## 7. P0-4：Tool Capability Registry

### 7.1 問題

TrustForge 已有多種 worker、API、資料來源、replay、report、upgrade tool，但缺少統一的 tool registry。缺 registry 時，agent 很難安全回答：

- 這個工具能不能讀 production？
- 會不會寫資料？
- 輸出能不能當 Evidence？
- 需要 admin token 嗎？
- 失敗怎麼 retry？
- 誰呼叫過？

### 7.2 建議 schema

```text
tool_capabilities(
  tool_id,
  name,
  description,
  input_schema,
  output_schema,
  side_effect_class,
  evidence_class,
  approval_required,
  timeout_sec,
  retry_policy,
  owner_module,
  status
)

tool_invocations(
  invocation_id,
  run_id,
  tool_id,
  input_hash,
  output_hash,
  started_at,
  completed_at,
  status,
  error_class,
  evidence_refs[]
)
```

### 7.3 Evidence class

| evidence_class | 說明 |
|---|---|
| `none` | 只能作操作資訊，不可引用 |
| `context_only` | 可作歷史脈絡，不可作當前 Evidence |
| `candidate_evidence` | 可進 Evidence queue，但需驗證來源 / hash / PIT |
| `trusted_evidence` | 已通過 Evidence contract，可進報告 |

---

## 8. P1：UI / IA 對應

Hermes 等級能力如果只藏在 API，使用者仍感覺不到。建議 HermesDashboard / Admin view 增加四個區塊：

| 區塊 | 放什麼 | 目的 |
|---|---|---|
| Memory rail | 本次 recalled memory、kind、evidence eligibility、lineage | 避免歷史脈絡與本次證據混淆 |
| Skill rail | active skills、revision、為何被選中、risk level | 讓 agent 做法可審查 |
| Tool rail | 本次工具呼叫、side effect、approval 狀態、輸出 hash | 讓工具執行可稽核 |
| Context manifest | snapshot、question、memory、skill、policy 的 frozen hash | 證明 run 可重現 |

這些不應該擠掉核心報告。建議 IA：

```text
Left：Question / Dialogue / Memory recall
Center：Selected complete report / Evidence / Trust explanation
Right：Trust decomposition / tool + skill manifest / risk gates
Bottom：stage telemetry / queue / run lineage / replay
Admin：proposal / sandbox / decision / activation / rollback
```

---

## 9. 建議路線圖

### Phase 0：文件與契約先行

- [ ] 把本報告拆成 issue / acceptance criteria。
- [ ] 補 `Memory OS`、`Skill Registry`、`Context Builder`、`Tool Capability Registry` 四份 architecture contract。
- [ ] 明確定義 `historical_context != Evidence` 的 DB constraint / test。

### Phase 1：Memory OS MVP

- [ ] 建立 memory schema 與 retrieval lineage。
- [ ] 將 question RAG / dialogue memory 遷到正式 memory kind。
- [ ] 實作中文 char n-gram 或等價檢索策略。
- [ ] UI 標示 memory kind 與 evidence eligibility。

### Phase 2：Skill Registry MVP

- [ ] 保留 outer policy family，新增 task skill metadata。
- [ ] 建立 skill revision、dependency、risk、verification contract。
- [ ] run 開始時產生 frozen skill manifest。
- [ ] skill update 走 proposal → sandbox → approval → activation。

### Phase 3：Context Builder

- [ ] 每輪 run 產生 immutable context manifest。
- [ ] 測試 memory / skill / policy 更新不改寫既有 run。
- [ ] 報告與 admin API 可讀 context manifest。

### Phase 4：Tool Registry

- [ ] 登錄 data source、crawler、analysis、report、replay、upgrade tools。
- [ ] 每次 tool invocation 留 input/output hash、side effect、approval 狀態。
- [ ] Evidence output 必須通過 contract 才能進 Trust report。

### Phase 5：Agent OS UI

- [ ] HermesDashboard 加 memory / skill / tool / context rails。
- [ ] Admin view 加 approval class 與 sandbox proof。
- [ ] 以 replay 證明 retrieved historical conclusions 不進 Evidence。

---

## 10. 驗收門檻

最低驗收不應只是 unit tests 綠，而要能證明：

1. **Memory isolation**：歷史回答被 recalled 時，只能進 context，不能進 Evidence / Trust scoring。
2. **Skill immutability**：run 開始後 skill active pointer 改變，不影響該 run。
3. **Context reproducibility**：同一 run 的 context manifest 可重讀、可 hash、可解釋。
4. **Tool auditability**：每次 tool call 都有 side-effect class、input/output hash 與 run lineage。
5. **Approval boundary**：高風險 skill / tool / upgrade 沒有人審不能 activation。
6. **UI truthfulness**：使用者能看懂「目前是歷史脈絡、候選證據、正式 Evidence、或外框 proposal」。
7. **No false autonomy**：文件與 UI 不宣稱 AI 會自行修改 production、Trust weights、Evidence binding、deployment 或 secrets。

---

## 11. 對外可用措辭

建議用：

> TrustForge 已具備 Hermes-style continuous analysis、snapshot lineage、question memory、outer policy family 與 approval-gated improvement control plane 的基礎。下一階段會把這些能力收斂成 Memory OS、Skill Registry、Context Builder 與 Tool Capability Registry，讓 TrustForge 從單一信任分析產品升級為可治理、可追溯、可人審啟用的 trust intelligence agent framework。

避免用：

- 「TrustForge 已經等同 Hermes Agent」。
- 「AI 可以自動自我升級 production」。
- 「歷史記憶可以直接當市場證據」。
- 「所有 skill 都能自動執行」。
- 「AgentCore memory 已完整產品化」。

---

## 12. 本次報告邊界

本報告是架構缺口與產品藍圖，不是 implementation completion report。本文新增後沒有修改 Trust Kernel、production code、deployment、secret、IAM、Trust weights 或 Evidence scoring。
