# TrustForge ISO/IEC 42001 AIMS 改善計劃（Issue #1239）

> 日期：2026-08-01
> 狀態：待 CEO 核准後分拆子 issue；本文件本身不代表 ISO 認證或符合性聲明。
> 權威邊界：競賽要求以 `docs/competition/COMPETITION-OFFICIAL.md` 為準；ISO/IEC 42001
> 是自主治理改善方向，不是目前已知的比賽硬性門檻。
> 內容邊界：本計劃使用條文主題與控制領域進行差距規劃，不重製受著作權保護的標準全文。

## 1. 目標與成功條件

目標是建立可稽核、可持續改善的 AI Management System（AIMS），把 TrustForge
既有的證據回溯、信心揭露、安全審查、release gate 與 incident 紀錄，收斂為同一套
治理系統。第一階段追求「具備接受正式差距評估的證據包」，不宣稱取得認證。

成功條件：

1. AIMS 範圍、政策、角色、目標及文件控制均有核准版本與 owner。
2. AI 系統清冊、風險登錄、影響評估、處置計劃與殘餘風險接受能互相追溯。
3. 資料、模型／供應商、開發、運行、事件及變更控制都有實際執行證據。
4. 完成一次獨立內部稽核、矯正措施閉環及管理審查。
5. 所有公開文案只使用經法務／合規核准的說法；取得第三方證書前不得寫
   「ISO/IEC 42001 certified」或無保留的「compliant」。

## 2. 已查證基線與主要缺口

### 2.1 可沿用的現有能力

- 競賽治理：官方命題、合規對照、submission checklist 與 acceptance requirements。
- 可追溯性：Evidence List、Execution Log、artifact lineage、來源與 claim 對應。
- 工程治理：issue → scoped branch → tests → pre-push → PR → adversarial review → merge。
- 安全與品質：CISO/CPO gate、incident report、rollback、production verification。
- AI 輸出治理：來源信任、矛盾處理、不確定性、限制與反方證據揭露。

### 2.2 尚不可視為 AIMS 完成的缺口

| 領域 | 主要缺口 | 優先級 | 最低完成證據 |
|---|---|---:|---|
| 組織情境與範圍 | 缺正式 AIMS scope、內外部議題及利害關係人需求 | P0 | 核准的 scope/context/stakeholder register |
| 領導與責任 | 缺統一 AI policy、AIMS RACI、風險接受權限 | P0 | policy、RACI、approval record |
| 規劃與目標 | 缺統一 AI risk register、可量測 AIMS objectives | P0 | risk methodology/register、KPI baseline |
| AI 影響評估 | 缺一致的個人／群體／社會與誤用影響評估流程 | P0 | assessment template + TrustForge completed case |
| 運作控制 | 現有控制分散，缺 lifecycle control matrix 與例外流程 | P1 | control matrix、exception/acceptance records |
| 資料與供應商 | 缺資料治理清冊、第三方 AI／資料供應商定期評估 | P1 | inventories、due-diligence records |
| 能力與溝通 | 缺 AIMS competency、training、內外溝通計劃 | P1 | competency matrix、training evidence |
| 績效評估 | 缺 AIMS 指標、內部稽核方案與管理審查 | P0 | dashboard、audit report、review minutes |
| 持續改善 | incident 已存在但未統一進 CAPA／不符合閉環 | P0 | CAPA register + closed sample |
| 控制適用性 | 缺 Annex A 控制選用、排除理由與實作證據索引 | P0 | Statement of Applicability（SoA） |

## 3. 條文主題工作包

以下只描述工作包，不替代正版 ISO/IEC 42001 標準。正式符合性評估前，由合規 owner
使用合法取得的最新版標準逐條覆核。

| 工作包 | 條文主題 | 交付物 | 驗收重點 |
|---|---|---|---|
| WP-GOV | 4 組織情境、5 領導 | scope、context、stakeholders、AI policy、RACI | 範圍與 TrustForge 實際服務／責任一致；CEO 核准 |
| WP-RISK | 6 規劃 | risk method/register、objectives、impact assessment、treatment plan | 每項高風險有 owner、期限、處置及殘餘風險決策 |
| WP-SUPPORT | 7 支援 | competency、training、communications、document-control procedure | 文件版本、核准、保存、變更與失效處理可追溯 |
| WP-LIFE | 8 運作 | lifecycle controls、data/model cards、human oversight、incident/exception process | 至少以 TrustForge 正式分析流程完成一次端到端演練 |
| WP-MEASURE | 9 績效評估 | KPI、monitoring plan、audit programme/report、management review | 稽核人員不稽核自己的實作；finding 有分級與期限 |
| WP-CAPA | 10 改善 | nonconformity/CAPA register、root-cause and effectiveness review | 至少一筆真實或演練 finding 完成閉環並驗證有效性 |
| WP-SOA | Annex A 控制領域 | SoA、control owner/evidence index、exclusion rationale | 每一控制均有適用判定、owner、狀態與證據 URI |

## 4. 多 worktree 併發開發設計

### 4.1 開工條件

1. CEO 核准本計劃。
2. 每個工作包建立獨立 child issue，寫明 acceptance criteria、依賴與 reviewer。
3. 每個 child issue 使用獨立 branch/worktree；禁止直接在 `main` 開發。
4. worker 只改 ownership 表列出的檔案。需要跨軌修改時先回報整合 owner，不搶改。
5. DB schema/migration、secret rotation、部署與外部系統接線全部不在本計劃授權內。
6. 客戶 PII 僅能留在 production；本機開發、測試、tabletop、稽核重演及文件證據只能使用
   合成資料，或經核准且不可回復識別的資料。不得用本計劃授權跨環境複製客戶 PII。

### 4.2 建議 worktree 與檔案 ownership

| Track / branch 建議 | Ownership（新檔或指定區域） | 可與誰平行 | 依賴 |
|---|---|---|---|
| `docs/<issue>-aims-governance` | `docs/aims/01-scope/`、`docs/aims/02-policy/` | 全軌 | 無；先定義草案 interface |
| `docs/<issue>-aims-risk-impact` | `docs/aims/03-risk/`、`docs/aims/04-impact/` | SUPPORT、LIFE | GOV 的 scope/RACI 草案 |
| `docs/<issue>-aims-support` | `docs/aims/05-support/` | RISK、LIFE、MEASURE | GOV 的文件 owner 草案 |
| `docs/<issue>-aims-lifecycle` | `docs/aims/06-lifecycle/`、`docs/aims/07-suppliers/` | RISK、SUPPORT | scope + risk taxonomy |
| `docs/<issue>-aims-assurance` | `docs/aims/08-measurement/`、`docs/aims/09-audit/`、`docs/aims/10-capa/` | LIFE | GOV/RISK 的核准版 interface |
| `docs/<issue>-aims-soa-integration` | `docs/aims/README.md`、`docs/aims/soa/`、跨文件連結 | 僅在其他軌交付後整合 | 全軌 |

禁止多軌共同修改單一總表。各軌先產出自己的 manifest；整合軌最後彙總 SoA 與 evidence
index，降低 markdown merge conflict 與過早宣稱控制已完成的風險。

`docs/README.md` 採單一入口策略：GOV 首次加入唯一的 AIMS 導覽連結；中間工作軌不得
反覆修改該檔，只維護各自 ownership 內的 manifest；SOA 整合軌最後才更新完整索引。

### 4.3 批次與整合順序

```text
Batch 0: GOV scope/RACI/interface
              |
Batch 1: RISK ─ SUPPORT ─ LIFE       （三軌平行）
              |        |
Batch 2: MEASURE/CAPA                （以已核准控制與風險為稽核母體）
              |
Batch 3: SoA + evidence index + independent gap review
              |
Batch 4: management review + certification-readiness decision
```

合併順序固定為 GOV → RISK/SUPPORT/LIFE → MEASURE/CAPA → SOA。若下游已開工而上游
interface 改變，下游必須 rebase 並更新 traceability，不得由整合者猜測補值。

### 4.4 每軌交付契約

每個 PR 至少附：

- child issue 與 acceptance criteria 對照；
- 變更檔案 ownership 聲明；
- normative / informative / internal evidence 三種來源標記；
- 「已實作、部分實作、僅計劃、不適用」四態之一，不得以文件存在冒充控制有效；
- evidence URI、owner、review date、next review date；
- evidence manifest、URI、title 與 metadata 不得包含客戶 PII；需驗證 production evidence 時，
  只記錄受控存取位置與非敏感證明，不把原始資料複製到 repo、本機或測試環境；
- reviewer attestation 與 `/codex-review` 結果；
- `git diff --check` 與 repo-local `.githooks/pre-push` commit-bound 證據。

安全敏感內容另需 harper（CISO）與 gray（CPO）review。公開聲明另需合規／法務檢視。

## 5. 分階段里程碑

### M0 — 基線與治理核准（P0）

- 完成 WP-GOV、資產／AI 系統清冊的最低欄位及文件控制規則。
- 把「競賽合規」與「AIMS 改善」放入不同 traceability 欄，避免混用。
- Exit：CEO 核准 scope、policy、RACI；所有後續工作包 owner 明確。

### M1 — 風險與影響閉環（P0）

- 完成方法、風險登錄、影響評估、處置計劃及殘餘風險接受規則。
- 以 TrustForge 的市場分析、第三方來源、Bedrock 使用及錯誤輸出作首批評估對象。
- Exit：所有 P0/P1 風險有 owner；禁止無 owner／無期限的 accepted risk。

### M2 — 運作與支援控制（P1）

- 把現有 SDLC、資料來源、證據鏈、人工監督、incident、供應商與變更 gate 映射到 AIMS。
- 補 competency、training、communication、document retention 與 exception 流程。
- Exit：正式分析流程完成一次 tabletop exercise；證據可由陌生 reviewer 重演查找。

### M3 — 稽核、CAPA 與管理審查（P0）

- 建立監測指標與 audit programme；由未實作該控制的人執行內部稽核。
- 必須記錄 auditor 與受稽控制作者的獨立性；若沒有獨立人員，只能標記為
  readiness exercise／gap review，不得宣稱完成獨立內部稽核。
- findings 進 CAPA，完成 root cause、修正、矯正措施與有效性檢查。
- Exit：內部稽核、CAPA sample、management review 均有 commit-bound evidence。

### M4 — SoA 與外部評估決策

- 完成 SoA、證據索引及獨立 readiness gap review。
- CEO 決定：維持 aligned、委託正式 gap assessment，或進入第三方認證。
- Exit：只有取得正式證據後，才能更新公開符合性聲明。

## 6. 驗證與量測

| 指標 | 初期門檻 |
|---|---:|
| AIMS 文件有 owner／核准／review date | 100% |
| 高風險項目有處置與殘餘風險決策 | 100% |
| 適用控制有 evidence URI | 100% |
| 逾期 P0 CAPA | 0 |
| 供應商／資料來源按期完成 review | 100% |
| 重大 AI incident 完成通報與 root cause | 100% |
| 公開 ISO 聲明經核准 | 100% |

門檻達成只表示內部 readiness，不等同第三方認證。控制有效性需以抽樣、重演、負向測試、
incident/CAPA 結果及內部稽核共同判定。

## 7. 明確非目標與停手點

- 不修改 DB schema、不建立 migration、不執行手寫 SQL。
- 不 rotation 密碼、token、secret，不新增 Secret Manager version。
- 不部署、不變更 AWS/GCP/IAM、不接外部服務。
- 客戶 PII 不得離開 production；本機、測試、tabletop 與稽核重演不得使用可識別客戶資料。
  任何例外均不在本計劃授權範圍，須另走合規、法務與 CEO 明確核准流程。
- 不複製或提交 ISO/IEC 42001 標準全文。
- 不以現有競賽合規文件推論已具 ISO 認證。
- 若後續工作需要上述 DB／secret 權限，必須另開 issue 並依 `AGENTS.md` 取得 Eric
  當次、具目的 token；不得以本計劃作為授權。

## 8. CEO 核准後的下一步

1. 由 CPO 將 WP-GOV、WP-RISK、WP-SUPPORT、WP-LIFE、WP-MEASURE/CAPA、WP-SOA
   分拆成六張 child issues。
2. CEO 核准每張 issue 的範圍、依賴、reviewer 與 ownership 後才建立 worktree。
3. 第一批只啟動 GOV；其 interface 草案確認後，RISK/SUPPORT/LIFE 才平行開工。
4. 每三個 PR 或每個 milestone 回報一次；只有親自驗證的控制才標記完成。
