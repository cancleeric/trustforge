# P0 開發計劃：核心 A/B 回退與單一真相源

> 日期：2026-07-26  
> 狀態：Draft，待 gray（CPO）覆核與 CEO 批准  
> 母計劃：[核心優化開發計劃—現況版](CORE-OPTIMIZATION-CURRENT-STATE-PLAN-2026-07-26.md)  
> 範圍：release-level A/B rollback、Kernel shadow parity、單一正式 judgment  
> 不包含：production deployment、AgentCore 啟用、不可逆資料 migration、UI 改版

## 1. 目標

在 `KernelOutput` 接管正式 Report/Evidence judgment 前，先建立可演練、可稽核的
完整 release A/B rollback。候選核心必須依序通過 parity、shadow、canary 與
人工核准；發生 regression 時能切回上一個 approved release，且回退後重新驗證
health、核心 golden canary 與真實分析流程。

本 P0 完成後，正式分析只能有一個 deterministic core judgment：

```text
normalized claims
  → KernelInput
  → run_kernel()
  → KernelOutput
  → app projection
  → Report / Evidence / narrative
```

## 2. 已確認現況

- `src/trustforge_core/` 已進 git，Kernel contract version 為 `2.2.0`。
- production orchestrator 已呼叫 `run_kernel()`，但 Report/Evidence 仍主要由
  legacy `score()`／`aggregate()` brief 生成。
- 核心 upgrade channel 是 `reviewed-core-release`，狀態為 `release-locked`；
  沒有 core runtime version pointer。
- 現有 wrapper artifact rollback 不等於核心 release rollback。
- repository 同時保留 Lambda 與 EC2/systemd/nginx 部署文件，開始實作前必須
  以 production evidence 確認唯一實際拓樸。
- `zero_downtime_restart.sh` 啟動的 canary 與 primary 目前都指向
  `/opt/trustforge`，不是兩個 immutable release，因此不能作為 A/B rollback。
- frontend atomic symlink/cutover 已有可參考的 transactional rollback 模式，
  但不能直接當作 backend/core rollback 已完成。

## 3. 不可妥協的設計原則

1. A/B 單位是完整 application release，不是單獨替換 Python module。
2. A 與 B 都由 immutable digest、git SHA、版本、core hash、config snapshot 識別。
3. rollback target 必須預先存在且已核准；事故時不得現場 rebuild 舊 commit。
4. rollback 不依賴 LLM、AgentCore、ModelHub 或外部 upgrade service。
5. Kernel cutover release 不包含不可逆 schema/data migration。
6. shadow 不影響使用者結果；canary 不得繞過 budget/security/idempotency gates。
7. activation 與 rollback 都必須有人類操作者、receipt 與事後驗證。
8. 未完成真實 `A → B → A` 演練前，不宣稱立即或零停機回退。

## 4. 拓樸決策 Gate

第一張 issue 只做 read-only discovery，不部署。

### Lambda 路徑

若 production 是 Lambda：

- 每個 release 使用 published Lambda version，不使用 mutable `$LATEST`。
- production/canary 使用不同 alias 或 weighted alias。
- rollback 是把 production alias 指回上一個 approved version。
- 保留 function configuration、environment、layer/package digest 與 IAM
  compatibility evidence。

### EC2/systemd/nginx 路徑

若 production 是 EC2：

- release 解壓到 content-addressed/versioned directory。
- A/B systemd units 各自 pin 到不同 release path，不共用 `/opt/trustforge`
  mutable working tree。
- nginx upstream 或明確 symlink/pointer 控制 active release。
- 切換採 transaction lock、preflight、post-switch verification 與 ERR rollback。

只有 production evidence 能決定採哪條路；禁止同一 issue 同時實作兩套。

## 5. Issue 與依賴

```text
P0-0 production topology + rollback objective
  ├── P0-1 core parity matrix
  └── P0-2 immutable release identity/retention
          → P0-3 A/B activation + rollback receipt
                  → P0-4 non-production A→B→A drill

P0-1 → P0-5 KernelOutput app projection contract
P0-4 + P0-5 → P0-6 shadow parity
P0-6 → P0-7 canary and production cutover readiness
P0-7 → P0-8 legacy bridge retirement audit
```

### P0-0：確認 production 拓樸與 rollback objective

交付：

- 以現行 release record、projected version、AWS resource identity 與服務設定確認
  production 是 Lambda 或 EC2。
- 記錄目前 active release、上一個 approved release、artifact 保存位置與缺口。
- 定義可量測 rollback objective：觸發點、開始/完成時間、最大容許中斷與驗證集合。

驗收：

- 結論只有一個 production topology，不以 README 推測。
- 全程 read-only，不修改 AWS、DNS、service、alias 或流量。
- rollback objective 經 CEO/ops 拍板；未拍板前 P0-2 不開工。

### P0-1：建立 core parity matrix

交付：

- legacy brief、KernelOutput、Report、Evidence 的欄位語意與 golden fixtures。
- support、contradiction、abstain、sparse、duplicate-source、manipulation、
  calibration、direction 與 failure cases。
- 每個差異標記為 bug、compatibility requirement 或 approved semantic change。

驗收：

- 測試比較 structured exact values，不只比 UI snapshot。
- fixtures 不使用 live provider 或 production data mutation。
- baseline 綁定 exact commit SHA。

### P0-2：immutable release identity 與 retention

交付：

- release manifest：artifact digest、git SHA、app version、Kernel contract version、
  core hash、config snapshot identity、build timestamp。
- 保存 active A 與 candidate B；部署 B 不覆寫 A。
- retention policy 保證 cutover/觀測期內 A 可直接重新啟用。

驗收：

- mutable tag、branch name 或 working directory 不能成為 release identity。
- manifest mismatch、artifact 缺失或 config drift 時 fail-closed。
- security/cost review 通過。

### P0-3：A/B activation、rollback 與 receipt

交付：

- 依 P0-0 選定拓樸實作單一 pointer/traffic switch。
- transaction lock、防併發、preflight、post-switch verification 與 automatic
  rollback-on-failure。
- append-only activation/rollback receipt。
- rollback 可在外部 AI/upgrade services 不可用時執行。

驗收：

- 不合法 target、非 approved target、digest/config drift、lock contention 均拒絕。
- 切換失敗與 rollback 失敗使用不同狀態/exit code。
- rollback receipt 含 from/to identity、actor、reason、timestamps 與 verification。
- harper review、adversarial review、commit-bound attestation 完成。

### P0-4：非 production A→B→A 演練

交付：

- 部署 approved A。
- 部署帶可辨識 semantic canary 的 B。
- 模擬 B regression，執行 rollback 回 A。
- 保存完整 release、commands、timestamps、health、workflow 與 final disposition。

驗收：

- A 回來後 projected version/digest 與預期完全一致。
- health、Kernel golden、API contract、Report/Evidence、snapshot/replay 通過。
- 至少一條真實使用者分析流程通過；不得只測 `/healthz`。
- 實測 rollback objective 達標，否則修正設計後重演。

### P0-5：KernelOutput app projection contract

交付：

- 純 mapper 將 KernelOutput 投影為產品 judgment 所需結構。
- direction、confidence、abstain、reason codes、support/contrarian mapping 契約。
- compatibility fixtures，不改 production active result。

驗收：

- mapper 無 IO、provider 或 deployment side effect。
- API/Report/Evidence 契約差異全部有 approved disposition。
- Eye blast radius 與 breaking-change evidence 附在 PR。

### P0-6：Kernel shadow parity

交付：

- 正式請求仍回傳 A/legacy 結果；同一 normalized input 執行候選 Kernel projection。
- 記錄結構化差異、版本、core hash，不將 shadow 結果展示為正式判斷。
- 明確 parity threshold 與 sample window。

驗收：

- shadow 不增加未受控 LLM/API 成本。
- shadow failure 不污染正式 Report，但必須可觀測。
- 所有差異在 cutover 前有 disposition，不能只看平均值。

### P0-7：canary 與正式 cutover readiness

交付：

- 小比例/限定流量 canary、停止條件與人工 promotion。
- promotion 前確認 P0-4 rollback drill 仍有效，release A 仍存在。
- production verification 與 rollback runbook。

驗收：

- canary regression 自動停止 promotion，不自動核准 production。
- promotion 需 CEO/authorized operator 明確授權。
- 發現 regression 時切回 A，不在事故期間修改 Kernel。
- 本 issue 完成只代表 deployment-ready；實際 production cutover 另需 release 授權。

### P0-8：legacy bridge retirement audit

交付：

- 用 Eye、AST boundary tests 與 consumer inventory 查 legacy imports/callers。
- 每個 bridge 決定保留 compatibility facade 或移除。
- production 不再有第二套正式 core judgment。

驗收：

- `run_kernel()` 是正式 judgment 唯一入口。
- compatibility facade 有明確 consumer、owner、contract 與長期/移除決策。
- full regression、pre-push 與 post-merge gate 全綠。

## 6. Eye 使用規範

每張 implementation issue 至少記錄：

```text
開工前：
  eye <file>:<symbol> --human
  eye explain <symbol> --hops 2 --callees --format human

PR 前：
  eye detect-changes --base main --json
  eye breaking-changes --from <base> --to HEAD --severity all --human
  eye cycles . --human
```

若 caller 數或 blast radius 超出 issue 預期，停止擴大修改，回到計劃重新拆分。
Eye 結果是影響證據，不取代 tests、pre-push、browser eye scan 或
`/codex-review`。

## 7. 測試與驗證矩陣

| 層級 | 必要驗證 |
|---|---|
| Core | contracts、golden、determinism、PIT、calibration、reconciliation |
| App projection | API、Report、Evidence、comparison、snapshot、replay |
| Release | manifest/digest、config drift、retention、pointer transaction |
| Failure | bad artifact、failed health、lock contention、rollback failure |
| Workflow | local release smoke、非 production真實分析、projected version |
| Governance | security/cost review、adversarial review、operator receipt |

## 8. PR 與里程碑切分

- M0：P0-0 完成，CEO 拍板 topology 與 rollback objective。
- M1：P0-1～P0-3，具備可切但尚未切 production 的 A/B 基礎。
- M2：P0-4 真實 rollback drill 達標。
- M3：P0-5～P0-6，Kernel projection shadow parity 收斂。
- M4：P0-7 deployment-ready，等待獨立 production authorization。
- M5：cutover 後另開 P0-8 收斂 legacy bridges。

每個 milestone 或累積超過三張 PR 時回報 CEO。所有 PR 均需 issue、scoped
branch、local pre-push、adversarial review、reviewer attestation；P0-2～P0-7
屬安全/成本敏感，需 harper review。

## 9. CEO／CPO 待拍板

1. 是否批准「完整 release A/B」而非 module hot-swap。
2. P0-0 查證後選定 Lambda 或 EC2 作為唯一 production 實作拓樸。
3. rollback objective 與最大容許中斷時間。
4. shadow sample window、parity threshold 與 canary promotion/stop conditions。
5. P0-7 完成只代表 deployment-ready；production cutover 必須另行授權。
