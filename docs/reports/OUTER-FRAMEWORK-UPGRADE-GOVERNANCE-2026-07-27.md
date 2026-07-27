# 外框模組與升級治理

> 日期：2026-07-27<br>
> 類型：開發用架構／治理報告<br>
> 範圍：Hermes Outer Framework、外框模組、approval-gated upgrade pipeline、sandbox、人審 gate、artifact pointer、rollback。<br>
> 對應 public docs 草稿來源：`TrustForge-devlog/docs/17-outer-framework-upgrade-governance.html`

## 1. 對外說法邊界

TrustForge 已具備外框控制面與 approval-gated upgrade pipeline；目前不能宣稱「全自動自我升級」或「AI 可自行修改 production」。

AI 可以：

- 觀測 durable analysis metrics。
- 診斷反覆失敗、retry、duration、coverage、question similarity 等問題。
- 產生候選改善 proposal。
- 建議 sandbox / replay 實驗。

AI 不可以：

- 自行核准 production 變更。
- 自行修改 Trust weights。
- 自行修改 Evidence binding / time boundary。
- 自行修改正式報告結論。
- 自行改 production code、deployment、secret、IAM、成本上限或安全遮罩。

## 2. 外框層定位

TrustForge 的核心信任管線是：

```text
Ingestion → Claim → Trust Scoring → Evidence → Report
```

Hermes 外框層位於核心之外，負責持續觀測、改善提案、候選 policy 驗證與升級治理。

```text
使用者 / 排程問題
  ↓
Continuous Analysis Flow（snapshot、job、stage、result）
  ↓ durable metrics：failure / retry / duration / coverage / question similarity
Improvement Diagnostics（只產生診斷與候選提案）
  ↓
Upgrade Control Plane（review → sandbox → human gate）
  ├─ active policy artifact：正式 run 使用
  └─ staged candidate artifact：沙盒候選，不影響 production
```

外框的價值不是替代 Trust Kernel，而是讓來源策略、分析策略、報告格式、評測規則與改善診斷可以獨立演進，同時保留可稽核邊界。

## 3. 模組數量與分層

目前控制面註冊 **31 個模組**，分布在 4 個 plane：

| Plane | 數量 | 用途 |
|---|---:|---|
| DATA PLANE | 7 | 資料來源、連接器、cache、archive 與資料品質。 |
| INTELLIGENCE | 11 | Claim extraction、prompt、tool routing、Trust 相關觀測與分析策略。 |
| DELIVERY | 5 | Evidence 組裝、報告敘事、引用格式與 UI 交付。 |
| OPERATIONS | 8 | 排程、改善診斷、proposal queue、sandbox、rollback 與觀測。 |
| **合計** | **31** | 控制面治理顆粒度。 |

依升級通道分：

| 升級通道 | 數量 | 說明 |
|---|---:|---|
| `sandbox-policy` | 16 | 可透過外框 policy artifact 走 sandbox / 人審升級。 |
| `reviewed-release` | 10 | 需走正式 release review，不由外框直接啟用。 |
| `model-gate` | 4 | 與模型、校準器或模型安全 gate 相關，不能自動放行。 |
| `core-adjacent-release` | 1 | 靠近核心信任或安全邊界，必須正式 release。 |

## 4. 可獨立升級的 policy family

真正可獨立替換的外框升級單位是 **5 個 outer policy family**，不是 31 個模組各自任意改 production。

| Family | 可調整範圍 | 典型改善 |
|---|---|---|
| `source` | 來源 timeout、retry、fallback、連接器頻率。 | 降低 timeout、調整 stale source fallback、改善資料覆蓋率。 |
| `analysis` | 主張抽取 budget、反方搜尋策略、tool routing、LLM call 上限。 | 增加 contrarian search、降低失敗重試成本。 |
| `report` | 報告章節、語氣、Evidence 呈現、限制條件格式。 | 補強新手摘要、把反方證據固定前置。 |
| `evaluation` | 評測題庫、replay sample size、QA 門檻。 | 擴充回放題庫、調整通過門檻。 |
| `improvement` | 診斷規則、proposal 數量、stage-only 策略。 | 把常見 failure pattern 轉成候選改善。 |

實作對照：

- `src/trustforge/skills.py`
- `src/trustforge/policy/schema.py`
- `src/trustforge/policy/executor.py`

## 5. 升級流程

```text
Observe
  ↓
Measure
  ↓
Diagnose / Propose
  ↓
LLM Review
  ↓
Sandbox Replay
  ↓
Human Approval
  ↓
Activate Pointer
  ↓
Rollback（如需要）
```

| 階段 | 做什麼 | 是否會影響 production |
|---|---|---|
| Observe | 收集分析 job、失敗、retry、latency、coverage、question similarity。 | 否 |
| Measure | 從 durable SQLite / queue / stage log 讀取可重播 metrics。 | 否 |
| Diagnose / Propose | 把反覆失敗、慢階段或覆蓋缺口轉為候選改善。 | 否 |
| LLM Review | 審查 proposal 是否合理、是否越界、是否需要人類補資料。 | 否 |
| Sandbox Replay | 用候選 artifact 跑 replay / QA，不切換正式 active pointer。 | 否 |
| Human Approval | 管理者核准或拒絕 proposal。 | 尚未改；只做決策。 |
| Activate Pointer | 把 active pointer 指到已核准、已通過 sandbox 的 artifact。 | 是，受控啟用。 |
| Rollback | 回到先前已核准的 known-good artifact。 | 是，受控回復。 |

## 6. Artifact、active pointer 與 sandbox attestation

外框 policy 不直接修改 mutable config，而是產生內容雜湊 artifact。每個 artifact 以 `sha256:<hash>` 識別；同內容同 hash，不同內容不同 hash。

```text
outer-report
  active → sha256:abc123   # 正式 run 使用
  staged → sha256:def456   # sandbox candidate，不影響正式結果
```

正式分析開始時會解析當下 active policy revision，並寫入 execution log；同一輪 run 不會中途換 policy。

Sandbox attestation 必須綁定：

- proposal id
- candidate family
- candidate revision / artifact hash
- run id 與 runner version
- details checksum
- completed_at 與 proof

這避免「sandbox 測 A，最後啟用 B」。

## 7. 安全邊界：可以改與不能改

外框可以改善工作方式，但不能改 Trust Kernel 的信任真相、證據邊界、成本護欄或部署權限。

| 允許外框提案 | 禁止外框自動修改 |
|---|---|
| 來源 timeout / retry / fallback | TrustScore 權重與核心評分公式 |
| Prompt / report policy 的候選版本 | Evidence binding、time boundary、正式報告結論 |
| 評測題庫與 replay 門檻候選 | 模型供應商、模型安全 gate、成本 cap |
| 改善診斷規則與 proposal 數量 | production code、部署、secret、IAM、安全遮罩 |

程式契約固定為：

```text
approval_required = true
automatic_apply = false
```

## 8. 目前運作狀態

截至本文件整理時，外框控制面與升級資料結構已存在，但尚未看到 production 級自動改善循環完成一次。

| 項目 | 狀態 | 說明 |
|---|---|---|
| 31 個控制面模組 | 已註冊 | 控制面可列出 Data / Intelligence / Delivery / Operations 模組。 |
| 5 個 outer policy family | 已定義 | `source`、`analysis`、`report`、`evaluation`、`improvement`。 |
| Observe / Measure | 控制面 ready | 具備 durable metrics 讀取與狀態顯示。 |
| Proposal | 等待資料 | 需先有足夠 analysis jobs / stage runs / failures 才能產生有意義候選。 |
| LLM Review | 尚未執行 | 目前不是 active review loop。 |
| Sandbox | idle | 尚未有 candidate artifact 進 sandbox。 |
| Activation | 無紀錄 | 尚未發生外框 artifact activation。 |

文件用語：可寫「approval-gated outer-framework improvement supported」。不可寫「Hermes 已全自動自我升級」或「AI 可自行修改 production」。

## 9. API 與 UI 對應

| 介面 | 用途 | 權限 |
|---|---|---|
| `GET /api/hermes-upgrades` | 公開唯讀升級控制面摘要。 | 公開讀取，不能決策。 |
| `GET /api/admin/hermes-upgrades` | 管理端查看 proposal、sandbox、decision 與 activation 狀態。 | Admin token。 |
| `POST /api/admin/hermes-upgrade-sandbox` | 對 candidate proposal 執行 sandbox / replay。 | Admin token。 |
| `POST /api/admin/hermes-upgrade-decision` | 核准或拒絕 proposal；核准後才可啟用 pointer。 | Admin token。 |

前端對應：

- `HermesDashboard`
- `HermesUpgradeShip`
- `StageBar`
- `StageDrilldown`

UI 負責呈現狀態與管理決策，不應成為 analysis execution 的 owner。

## 10. 操作與驗收

```bash
# 查看公開升級控制面
curl https://trustforge.hurricanesoft.com.tw/api/hermes-upgrades

# 查看分析 flow / journey
curl https://trustforge.hurricanesoft.com.tw/api/analysis-flow
curl https://trustforge.hurricanesoft.com.tw/api/analysis-journey

# 管理端 sandbox / decision 需 Admin Token；文件不得暴露 token 值
curl -X POST https://trustforge.hurricanesoft.com.tw/api/admin/hermes-upgrade-sandbox \
  -H "Content-Type: application/json" \
  -d '{"proposal_id":"..."}'
```

驗收時至少要證明：

- UI polling 不會建立新 job。
- 每輪 run 固定使用開始時的 active policy revision。
- candidate artifact sandbox 前不影響 production。
- human approval 前 `automatic_apply` 仍為 false。
- rollback 只能回到先前已核准的 known-good artifact。
- retrieved historical conclusions 不能進入 Evidence，也不能改 deterministic Trust scoring。
