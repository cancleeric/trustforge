# TrustForge 核心優化開發計劃—現況版

> 現況日期：2026-07-26  
> 母計劃：[Agent Platform Extraction Development Plan](AGENT-PLATFORM-EXTRACTION-PLAN-2026-07-22.md)  
> 架構基線：[Agent Platform Extraction Feasibility Assessment](../architecture/AGENT-PLATFORM-EXTRACTION-FEASIBILITY-2026-07-22.md)  
> 文件性質：日期綁定的現況稽核與後續開發計劃，不代表 production deployment 授權  
> 本文件不啟用 AgentCore、不變更核心演算法、不修改 production

## 1. 結論

7/22 母計劃的三層方向仍正確，但「implementation has not started」已不符合
repository 現況：

```text
目標依賴方向

trustforge_app
├── agent_platform_kit
└── trustforge_core

agent_platform_kit  ──X──> trustforge_core / trustforge_app
trustforge_core     ──X──> agent_platform_kit / trustforge_app
```

目前實際狀態是：

- `trustforge_core` 已建立，已有公開契約、純演算法、決定性與外部 consumer 測試。
- production orchestrator 已呼叫 `run_kernel()`，但正式 `Report` 仍由 legacy
  `score()`／`aggregate()` 結果生成；Kernel output 目前主要進 execution log，
  尚未成為唯一真相源。
- provider registry、ports、adapters 與部分 resolver 已存在，但 production
  orchestration 仍直接 import/使用 `BedrockClient`。
- generic platform 能力仍散落在 `trustforge.*`，尚未形成
  `agent_platform_kit` package。
- `composition_root.py` 目前只集中 runtime mode 與 cache backend 類型，尚未
  組裝 providers、stores、gates、telemetry、policies 與 runtimes。
- AgentCore 僅達到非生產 routing/response-shape 測試；實際 runtime API
  mapping、正式 endpoint、認證、錯誤分類、成本與 production evidence 均未完成。

因此，本輪最高優先事項不是立即 `agentcore deploy`，而是先建立
release-level A/B rollback，再消除正式信任結果的雙軌計算，建立唯一核心入口，
最後完成 application composition seam。AgentCore 應留在可替換 runtime
adapter，不得進入 Trust Kernel。

核心正式切換的硬性順序是：

```text
immutable release A（上一個 approved production）
  → deploy release B（候選核心完整版本）
  → shadow parity
  → canary
  → 人工核准切換
  → production verification
  → regression 時將流量切回 release A
  → 對 A 重新執行 health + golden canary + 真實使用流程
```

這裡的 A/B 是**完整 release artifact/container**，不是在同一 process 內動態
熱載入兩套 Python 核心。Kernel contract、app mapper、Report/Evidence projection
必須以同一個 release 單位一起升級或回退。

## 2. 狀態判定規則

| 狀態 | 定義 |
|---|---|
| 已完成 | 程式、邊界與回歸測試均存在，正式路徑確實使用 |
| 部分完成 | 介面或程式存在，但正式路徑未完整使用，或仍有明列 migration bridge |
| 未開始 | 目標 package／正式接線或驗證不存在 |
| 阻塞 | 缺外部資料、憑證、服務或真人決策，不能以 stub 宣稱完成 |
| 方向變更 | 現況證據顯示原交付方式應調整，而非照原文字機械執行 |

文件與 Kiro task 的勾選只能作為線索；狀態以當前 import、runtime call path、
測試範圍及 production evidence 為準。

## 3. W0–W8 現況總表

| Workstream | 現況 | 判定 | 主要證據 | 下一個完成條件 |
|---|---|---|---|---|
| W0 行為基線與邊界 | golden、reconciliation、core consumer、import-boundary tests 已存在；仍有 temporary bridges | 部分完成 | `tests/test_*golden*`、`tests/test_architecture_import_boundaries.py` | 建立母計劃 acceptance matrix；每個 bridge 重新驗證 owner 與移除條件 |
| W1 Provider/runtime contracts | ports、backend registry、builtin adapters 存在；部分介面仍帶 TrustForge/Bedrock 形狀 | 部分完成 | `src/trustforge/ports.py`、`backend_registry.py` | 收斂最小 `ModelProvider`／`AgentRuntime` 契約，AWS 型別不得穿越 port |
| W2 Production provider wiring | resolver/pipeline 有接線，但 orchestrator 仍直接使用 `BedrockClient` | 部分完成 | `pipeline.py`、`agent/orchestrator.py` | 正式路徑只由 composition root 建立 provider；execution evidence 記錄實際 provider |
| W3 AgentCore adapter | routing 與 mocked contract tests 存在；production API mapping/evidence 不成立 | 部分完成／未啟用 | `agent/agentcore_adapter.py`、`tests/test_agentcore_adapter.py` | 依正式 AWS SDK 契約實作 session/run/tool/trace；通過非生產 smoke，仍不得繞過 gates |
| W4 Execution/telemetry | execution log、module telemetry 已存在；仍是 Hermes/TrustForge-shaped，instrumentation 非全路徑 | 部分完成 | `execlog.py`、`module_telemetry.py` | generic event contract 與 app projection 分離；正式 lifecycle 全程有證據 |
| W5 Governance primitives | policy、skills、upgrade、budget、security、idempotency 均有實作；尚未 generic 化 | 部分完成 | `policy/*`、`skills.py`、`upgrade_*`、`budget_guard.py` | mechanism 與 TrustForge catalog/rules 分離；fail-closed 行為 parity |
| W6 Trust Kernel | `trustforge_core`、contracts、純演算法、consumer tests 已存在；正式結果仍雙軌 | 部分完成，接近主要里程碑 | `src/trustforge_core/*`、kernel tests | `run_kernel()` 成為 Report/Evidence 判斷的唯一核心結果；移除重複計算 |
| W7 Application composition | 有輕量 `AppContext`，但無 `trustforge_app` package，也非完整 composition root | 未完成 | `composition_root.py` | 所有 runtime dependencies 由單一 root 組裝；offline/live/staging 差異只在 composition |
| W8 第二 consumer／distribution | 有 subprocess consumer regression，沒有第二個真實 consuming project | 未開始 | `tests/test_core_public_consumer_api.py` | 第二個實際 consumer 證明 API；之後才決定 package/repository 發布 |

## 4. 各 Workstream 詳細稽核

### W0：基線與邊界護欄

已具備：

- Kernel、aggregate、per-claim、legacy reconciliation 與 golden 測試。
- AST import-boundary scanner 能拒絕新增的 core → app/platform 依賴。
- `trustforge_core` 可在 subprocess 中執行且不載入 app modules。

尚未完成：

- `tests/test_architecture_import_boundaries.py` 仍允許多個 issue-backed
  temporary bridges；這代表遷移護欄存在，不代表邊界已完成。
- `MIGRATION-BRIDGE-AUDIT.md` 將部分 legacy path 判為相容需求，但沒有取代
  「正式結果只能有一個核心來源」的要求。
- 原計劃的所有 acceptance criteria 尚未形成一張可執行的總矩陣。

決策：保留 compatibility facade，但禁止保留兩套可獨立漂移的正式計算。

### W1–W2：Provider contract 與正式接線

已具備：

- backend registry 與 provider selection 的基礎能力。
- builtin/fake/null 等 adapter 與對應測試。
- 部分 pipeline 路徑已能透過 resolver 改變 provider。

尚未完成：

- `agent/orchestrator.py` 仍直接 import `BedrockClient`，所以 provider
  replacement 不是全正式路徑成立。
- `composition_root.AppContext` 只有 mode/cache type，未實際建立 model
  provider、agent runtime、stores、gates 或 telemetry。
- Bedrock model provider 與 Agent runtime 的能力邊界仍未完全拆清。

完成定義：

1. 正式 entry points 只取得 injected protocols，不自行 `BedrockClient()`。
2. provider identity、fallback、usage 與 failure 均進 execution evidence。
3. offline、live、staging 測試使用相同 contract suite。
4. provider misconfiguration 必須明確 fail-safe/fail-closed，不得靜默改走假結果。

### W3：AgentCore

現有 adapter 能根據 backend registry 切換路由，並以 mock 驗證
`run_id/status/output` shape。但它不能視為 production-ready：

- 程式本身標示實際 boto3 client 與 endpoint 契約仍待確認。
- 測試明確是 non-production，沒有碰網路。
- builtin path 回傳示意文字，不是正式 TrustForge orchestration。
- 尚未證明 session、streaming、tool invocation、trace、timeout、cancellation、
  retry、auth、usage/cost 與 error taxonomy。
- 尚未證明 AgentCore failure 仍受 budget、security、idempotency 與 approval
  gates 約束。

AgentCore 完成必須拆成兩個不同里程碑：

1. **Adapter-ready**：正式 SDK 契約、contract tests、失敗語意與 gates 完整。
2. **Deployment-ready**：經明確授權的非生產 runtime smoke、觀測與成本證據。

production deployment 不屬於本核心優化計劃的自動結果，必須走獨立 release workflow。

### W4–W5：平台能力

repository 已擁有大量可抽取能力，但目前仍是「可重用候選」，不是已完成的
platform package：

- Execution log 帶 Hermes/TrustForge 語意。
- Policy/skill lifecycle 固定產品 family/catalog。
- Upgrade queue/state machine 帶 TrustForge module IDs 與 repository 行為。
- Budget 仍包含產品 request mode 與 provider pricing。
- Security rules 仍是 TrustForge Web/API policy。
- Idempotency 已有抽象，但需與其他 primitives 使用一致的 durable/local
  contract tests。

抽取順序必須是：

```text
contracts
  → fake/null contract tests
  → adapters
  → generic lifecycle mechanisms
  → TrustForge app projections/catalogs
```

不得先搬目錄再補邊界，也不得為尚不存在的第二 consumer 過度抽象。

### W6：Trust Kernel

這是目前進展最多、也最需要收尾的部分。

已具備：

- `KernelInput`、`KernelOutput` 與 versioned contract。
- scoring、aggregation、corroboration、Dawid–Skene、source identity 等純模組。
- 決定性、public API、legacy reconciliation、calibration provenance 測試。
- `agent/orchestrator.py` 已把 claims 映射成 KernelInput 並呼叫 `run_kernel()`。

關鍵未完成：

```text
目前正式路徑

claims
 ├── legacy score() → aggregate() → TrustedBrief → Report / Evidence
 └── run_kernel() ----------------------------→ execution log 比較欄位
```

因此 `run_kernel()`「有被呼叫」不等於「已是唯一核心入口」。只要 Report、
Evidence、abstain 或 direction 仍以 legacy brief 為正式來源，兩條路徑就可能漂移。

目標路徑：

```text
normalized claims
  → KernelInput
  → run_kernel()
  → KernelOutput
  → app mapper
  → Report / Evidence / narrative
```

legacy facade 可以暫時保留給 compatibility consumer，但 production app 不得再
直接組合 core internals。

### W7–W8：Application root 與發佈決策

`trustforge_app` 尚未形成。短期不必先更名整個 package，但必須先讓
composition root 真正負責依賴組裝，否則目錄重命名只會產生假分層。

第二 consumer 測試目前只證明 core API 可被 subprocess 使用；它不是第二個真實
project，不能用來拍板獨立 PyPI package 或拆 repository。

發佈決策維持：

- 先在 monorepo 建立可強制的 dependency direction。
- 再由第二個真實 consumer 驗證 generic API。
- 最後才決定獨立 package、repository、versioning 與 release cadence。

## 5. 更新後的執行順序

### P0：單一核心真相源

#### P0.1 建立 current parity matrix

交付：

- 固定代表性 KernelInput/Output golden vectors。
- 對照 legacy brief、Kernel output、Report、Evidence 的欄位語意。
- 覆蓋 support、contradiction、abstain、sparse、duplicate source、
  manipulation、calibration 與 direction。

驗收：

- 每個差異有明確 disposition：bug、相容需求或預期新語意。
- 禁止用四捨五入後的 UI snapshot 掩蓋核心差異。

#### P0.R 建立 release-level A/B rollback

這是 KernelOutput 接管正式 judgment 前的硬性 gate。沒有通過真實 rollback
演練，不得執行 P0.2 的 production cutover。

交付：

- 每個候選與 production release 都有 immutable artifact/container digest、
  git SHA、Kernel contract version、core revision hash 與 config snapshot。
- production 切換時保留上一個 approved、已親驗的完整 release A。
- 候選 release B 可在不接正式流量的情況下完成 health、golden、shadow parity
  與資料真實性驗證。
- canary 與正式切換使用明確 release/traffic pointer，不以 mutable tag、
  工作目錄或臨時檔案辨識版本。
- regression 時可把流量指回 release A；rollback 不依賴 AgentCore、ModelHub、
  LLM 或其他可能同時失效的外部控制服務。
- activation/rollback receipt 記錄 actor、時間、原因、from/to digest、git SHA、
  config snapshot、驗證結果與 final disposition。
- release A/B 共用向後相容的資料契約；本階段禁止把不可逆 schema/data migration
  與 Kernel cutover 綁在同一 release。

驗收：

- 在非 production 環境完成一次真實演練：
  `A → B → 模擬 regression → A`。
- rollback 後 health、Kernel golden canary、API contract、Report/Evidence、
  snapshot/replay 與一條真實使用者分析流程全部通過。
- rollback target 必須是已核准、content-addressed、可部署的 artifact，不能只是一個
  git branch 名稱或「重新 build 舊 commit」的承諾。
- 記錄 rollback objective 與實測結果；在實測 objective 未建立前，不宣稱
  「立即」或「零停機」。
- 安全與成本敏感，需 harper review、正常 adversarial review 與
  commit-bound reviewer attestation。

#### P0.2 讓 KernelOutput 驅動正式 judgment

交付：

- app mapper 將 KernelOutput 轉成 Report/Evidence 所需的 product projection。
- orchestrator 不再以另一套 aggregate 結果決定正式 direction/confidence/abstain。
- execution log 記錄同一 Kernel output，而非平行計算比較值。
- release B 先走 shadow parity 與 canary，禁止直接全量切換。

驗收：

- production path 只有一次 core judgment。
- API、report、snapshot、comparison 與 replay regression 全綠。
- compatibility facade 的任何保留均有明確 consumer 與移除/長期支援決策。
- P0.R 的 A/B rollback 演練已通過，且 release A 在 cutover 驗證期內保持可部署。
- production verification 發現 regression 時，依 P0.R 切回上一個 approved
  release，不在事故期間現場修改 Kernel。

#### P0.3 強化 import boundary

交付：

- `trustforge_core` 禁止 IO、env、network、AWS、database、UI、skills、
  deployment 與 agent runtime imports。
- temporary bridges 逐項重驗，不再只依 issue number 永久放行。

驗收：

- synthetic prohibited import 必定使測試失敗。
- core import 無 filesystem/thread/network/environment side effect。

### P1：Composition root 與 providers

#### P1.1 建立真正的 application composition

`AppContext` 擴充或由 successor 取代，集中建立：

- ModelProvider
- AgentRuntime
- source/cache/telemetry stores
- budget/security/idempotency gates
- policy/skill/upgrade services

驗收：

- orchestration 不直接 construct provider/client。
- offline/live/staging 差異只存在 configuration/composition。
- misconfiguration 與缺 credential 有明確、可觀測的失敗狀態。

#### P1.2 收斂最小 provider/runtime contracts

驗收：

- model completion 與 agent session/tool runtime 是不同介面。
- Coin、stance、Evidence、Hermes 等產品詞不進 generic contracts。
- usage/cost、provider identity 與 typed errors 能跨 adapter 一致比較。

### P2：Generic platform extraction

依序抽離：

1. execution event/lineage contract；
2. telemetry lifecycle engine；
3. immutable artifact stage/approve/activate/rollback；
4. generic upgrade state machine；
5. quota/security/idempotency interfaces。

每一步都先保留 TrustForge projection，再移動 mechanism；不得讓抽取工作改變
正式信任結果。

### P3：AgentCore adapter 與非生產驗證

前置依賴：P0、P1 完成，至少 budget/security/idempotency gates 已可在 runtime
邊界強制。

交付：

- 依正式 AWS SDK 實作 invoke/session/stream/tool/trace mapping。
- auth、timeout、cancellation、retry、usage/cost 與 typed errors。
- mock contract tests、recorded failure fixtures、明確授權的 non-production smoke。
- CloudWatch/trace evidence 與 runaway session 成本控制。

不包含：

- 自動 production deployment。
- 將 AgentCore 引入 `trustforge_core`。
- 因 AgentCore 部署方便而跳過 TrustForge release/review gates。

### P4：第二 consumer 與 distribution decision

只有實際第二專案使用 generic contracts 後才執行。若第二 consumer 只需要
`trustforge_core`，不代表整個 agent platform 都應獨立發佈。

## 6. 建議 Issue 拆分

| 順序 | 建議 Issue | 範圍 | 估計風險 | 依賴 |
|---|---|---|---|---|
| 1 | Core parity matrix | 文件、fixtures、差異 disposition | 中 | 無 |
| 2 | Release artifact identity and retention | immutable A/B artifact、digest、config snapshot | 高／安全與成本敏感 | 1 |
| 3 | A/B traffic switch and rollback receipts | 明確 pointer、人工 activation、offline rollback | 高／安全與成本敏感 | 2 |
| 4 | Non-production rollback drill | `A → B → regression → A` 與證據 | 高／需環境授權 | 3 |
| 5 | KernelOutput app projection contract | mapper 與相容 fixtures，不切 production | 高 | 1 |
| 6 | Kernel shadow parity and canary | release B 比對、門檻、觀測 | 高 | 4–5 |
| 7 | Kernel judgment production cutover | 唯一 judgment truth；可切回 A | 高 | 6 |
| 8 | Core bridge retirement audit | imports、consumer ownership | 中 | 7 |
| 9 | Composition root v2 | provider/store/gate 組裝 | 高 | 7 |
| 10 | Provider/runtime contract cleanup | 最小 ports、typed errors | 中 | 9 |
| 11 | Generic execution/telemetry split | platform event + app projection | 中 | 9–10 |
| 12 | Generic governance primitives | policy/upgrade/quota/security/idempotency | 高 | 10–11 |
| 13 | AgentCore adapter GA contract | runtime mapping，不部署 production | 高／安全與成本敏感 | 9–12 |
| 14 | AgentCore non-production smoke | 真 AWS evidence | 高／需明確授權 | 13 |
| 15 | Second consumer validation | 真實 consuming project | 中 | 10–12 |

每張 issue 必須有獨立 acceptance criteria；安全或成本敏感項需 harper review，
並保留額外 security/adversarial review 紀錄。

## 7. 全域驗收門檻

- 核心結果：同一正式分析只存在一個 deterministic core judgment。
- 回退：Kernel production cutover 前，完整 release A/B rollback 已在非 production
  真實演練；上一個 approved artifact 可直接部署，不需臨時 rebuild。
- 邊界：`trustforge_core` 不依賴 platform/app/IO；platform 不依賴 TrustForge domain。
- 相容：API、Report、Evidence、snapshot、replay 與 comparison 契約有回歸證據。
- Provider：正式路徑經 composition root；provider identity 與 usage 可追溯。
- 治理：budget、security、idempotency、approval、rollback 在失敗時維持 fail-closed。
- AgentCore：未通過正式 SDK contract 與 non-production smoke 前，只能標示 partial。
- 發佈：沒有第二 consumer 前，不拆 repository、不承諾獨立 distribution。
- 流程：issue → scoped branch → regression tests → local pre-push →
  PR → adversarial review → reviewer attestation；UI 變更另做 desktop/mobile eye scan。
- 部署：production 只走明確 release workflow，且部署後親驗 health 與變更流程。

## 8. 風險與禁止事項

1. **雙軌漂移**：legacy brief 與 KernelOutput 同時計算，數字相近也不能視為安全。
2. **假回退能力**：只有 git history、沒有保留可部署 artifact 與切換演練，
   不能宣稱可立即回退。
3. **不完整回退**：只換 Kernel module、沒有同步回退 mapper、config 與產品契約，
   會產生跨版本不相容。
4. **不可逆 migration 綁定**：Kernel cutover 若同時改不可逆資料契約，release A
   可能無法重新接手流量。
5. **目錄式抽取**：只搬檔案、不改 production imports，不算完成。
6. **語意洩漏**：Coin、stance、Evidence、Hermes 或 TrustForge policy 進入
   generic platform。
7. **AgentCore 假完成**：mock routing 或 resource provision 成功不等於使用者流程可用。
8. **自動 runtime patch 風險**：AgentCore runtime 維運責任不取代 dependency、
   adapter 與產品回歸責任。
9. **過早發佈**：單一 consumer 時凍結 generic API，會把產品偶然性變成公共契約。
10. **治理退化**：遷移期間不得讓成本、資安、冪等或人工核准由 fail-closed 退成
   fail-open。

## 9. 非目標

- 本文件不重新設計 Trust Score 公式或產品定位。
- 不在缺乏真實 outcome evidence 時宣稱市場預測力。
- 不因核心抽取順手修改 UI、資料來源、training backend 或 production infra。
- 不刪除仍有明確 consumer 的 compatibility API。
- 不把 GitHub Actions 重新啟用為 production deployment。
- 不授權 `agentcore deploy` 或任何 production mutation。

## 10. 參考證據

- [原始開發計劃](AGENT-PLATFORM-EXTRACTION-PLAN-2026-07-22.md)
- [可行性評估](../architecture/AGENT-PLATFORM-EXTRACTION-FEASIBILITY-2026-07-22.md)
- [架構總覽](../architecture/ARCHITECTURE-OVERVIEW.md)
- [Trust Kernel Boundary](../architecture/TRUST-KERNEL-BOUNDARY.md)
- [Migration Bridge Audit](../architecture/MIGRATION-BRIDGE-AUDIT.md)
- [Kernel Contract 2.2 Migration](../contracts/KERNEL-CONTRACT-2.2-MIGRATION.md)
- `src/trustforge_core/`
- `src/trustforge/composition_root.py`
- `src/trustforge/agent/orchestrator.py`
- `src/trustforge/agent/agentcore_adapter.py`
- `tests/test_architecture_import_boundaries.py`
- `tests/test_core_public_consumer_api.py`
- `tests/test_agentcore_adapter.py`

## 11. 現階段拍板建議

建議批准以下方向，尚不批准 production deployment：

1. 以本文件取代 7/22 母計劃的「implementation has not started」狀態敘述。
2. Kernel 正式切換前，先完成 release-level A/B rollback 與非 production
   `A → B → A` 真實演練。
3. 優先完成 P0 單一核心真相源，再進入 AgentCore production 評估。
4. `trustforge_core` 永遠保持 deterministic、無 IO、無 AgentCore。
5. AgentCore 僅作為 `AgentRuntime` adapter，由 application composition 選用。
6. 第二 consumer 出現前，所有抽取維持在 monorepo。
