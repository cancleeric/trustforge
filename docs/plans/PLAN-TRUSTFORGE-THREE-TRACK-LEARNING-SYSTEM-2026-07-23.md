# TrustForge 三軌統一學習架構開發計劃 

> 日期：2026-07-23  
> 狀態：CEO 審查通過的開發基準；尚未授權實作、DB 異動、ModelHub 寫入或生產發布  
> 依據：`docs/architecture/TRUSTFORGE-THREE-TRACK-LEARNING-SYSTEM-ANALYSIS-2026-07-23.md`

## 1. 目標與不可變邊界

依序建立 Question RAG 品質、分析異常偵測＋信心校準、wrapper controlled upgrade 三軌。三軌可共用事件版本、provenance、dataset manifest 與候選 registry，但資料分類、truth label、評估及啟用權限必須隔離。

不得修改或繞過 Trust Kernel、point-in-time time boundary、Evidence binding、人工 production activation、稽核及回滾門檻。歷史答案永久為 `historical_non_evidentiary`；OHLCV outcome 不得偽裝成 Evidence 或當時已知資訊；wrapper 不得自行批准或啟用候選。

## 2. 範圍

### 本計劃包含

- 三軌 canonical schema、資料分類、版本與 provenance。
- `analysis-quality.v1` immutable event。
- T+1／T+7／T+14 延遲 outcome labeler。
- 信心校準資料集與 leakage guard。
- 可解釋的分析異常偵測 baseline。
- wrapper 候選 sandbox、人工啟用與回滾治理。
- RAG feedback、版本化 gold set 與歷史答案隔離。
- contract、unit、integration、security、replay 與 regression tests。

### 不包含

- Trust Kernel、Evidence 資格或 time boundary 改動。
- 自動 production activation、線上自訓或無人審核的 prompt／模型替換。
- 把五年 OHLCV 宣稱為五年分析樣本。
- ModelHub 寫入、修復、部署或健康宣告。
- 生產回填、破壞性重算、部署或發布。

## 3. 開工前決策與依賴

1. 每個 PR 先建立有 acceptance criteria／dependencies 的 issue，再開 scoped branch。
2. PR1 必須先鎖定現有 Trust Kernel、Evidence binding 與 time-boundary contract tests。
3. PR3 開工前，CEO／產品責任人必須書面拍板 outcome 定義：交易日曆、T+N 算法、報酬／方向／風險欄位、公司行動及缺值規則；開發者不得自行推定。
4. RAG gold set 必須有具名 reviewer 與版本紀錄。
5. ModelHub 僅可先做唯讀 health、capability、tenant scope、權限與 artifact provenance 複驗。複驗前一律 `unverified`／disabled，且 wrapper 回滾不得依賴 ModelHub 在線。
6. 任何 DB schema／migration 工作必須停在設計階段，等 Eric 親建當次 purpose token；沒有 token 不得撰寫、套用或繞過 migration。
7. Secret／token rotation 或外部服務寫入接線，須另取得 Eric 主對話授權。

## 4. Canonical 資料契約

### 4.1 共用識別與分類

- 識別：`analysis_id`／`run_id`、`question_id`、`answer_id`、`evidence_snapshot_id`、`model_artifact_id`、`wrapper_version`、`activation_id`、`rollback_target`。
- 時間：event time、available time、`as_of_time`、`outcome_horizon`、`outcome_observed_at`。
- 分類：`evidentiary`、`historical_non_evidentiary`、`delayed_outcome`、`human_gold_label`、`candidate_diagnostic`。
- 跨分類預設拒絕；禁止原地改寫分類。

### 4.2 `analysis-quality.v1` 最低欄位

- 分析快照、coin、mode、question type；
- raw／calibrated confidence、direction、decision state；
- supporting／contrarian count、evidence count、average trust、independent source count、source distribution；
- freshness、conflict、missingness、completeness；
- model、prompt、policy、rule、schema version；
- stage latency、failure、retry；
- 完整 provenance 與 immutable identity。

核心事件只追加且不可被 outcome 更新。修訂 outcome 必須建立新版 observation，並保留原版本與 available time。

## 5. 里程碑與 PR 切分

### 里程碑 A：資料可信基座（PR1–PR3）

#### PR1：三軌契約與不變量護欄

內容：canonical schema、分類、版本策略、serializer 與現況 characterization／contract tests。

驗收：歷史答案不能通過 Evidence binding；outcome 不能出現在其 available time 之前；未知 schema fail closed；相同事件重放一致；既有 Trust Kernel 行為不變。

測試：schema、序列化、分類負向、point-in-time、replay／idempotency、完整回歸、lint、build、`git diff --check`。

#### PR2：`analysis-quality.v1` immutable event

內容：每次分析產生唯一事件，保存第 4.2 節完整欄位及 Evidence 快照；後續 outcome 只能追加關聯事件。

驗收：一次分析對應唯一 `analysis_id`；重送不重複；核心欄位不可更新；缺時間或 provenance 時 fail closed；重放不能讀取未來資料。

測試：immutable update rejection、duplicate delivery、clock boundary、future-data exclusion、failure／retry／partial failure、provenance 偽造與越權測試。

#### PR3：T+1／T+7／T+14 outcome labeler

開工閘門：第 3 節 outcome 定義已書面拍板。

內容：依核准日曆及資料版本產生附加 observation，不改原分析；處理 maturity、缺值、停牌、公司行動及行情修訂。

驗收：未成熟不產生；缺資料標記 pending／unavailable；修訂只建立新版；來源、版本、available time 可追溯；重跑 idempotent。

測試：固定 fixture、週末／假日／停牌／缺值／公司行動、look-ahead leakage、dry-run、重跑與來源失效。

里程碑 A 完成後，CEO 親驗事件、重放與時間邊界並主動回報；未通過不得進入資料集建置。

### 里程碑 B：分析品質學習基線（PR4–PR5）

#### PR4：信心校準資料集

內容：只把真實 immutable analysis 與已成熟 outcome 依版本化規則 join；建立時間切分、eligibility、manifest 與 checksum。本 PR 不訓練或啟用 production 模型。

驗收：無 `analysis_id` 不得入樣；五年 OHLCV 不得展開成分析樣本；每列可追到 analysis、outcome 及版本；train／validation／test 時間隔離；manifest 可重建。

測試：join cardinality、eligibility、leakage guard、temporal split、缺失／多版本 outcome、manifest reproducibility。

#### PR5：分析異常偵測 baseline

內容：先以可解釋規則／統計方法偵測信心漂移、Evidence 缺漏、來源集中、分布 outlier 與 pipeline 異常；只產生 diagnostic。

驗收：告警附原因、輸入版本、基線及可重現查詢；小樣本回報 insufficient data；不觸發啟用；基線版本化且可回滾。

測試：known anomaly、正常負向、小樣本、缺資料、分布切換、去重、降級及重跑一致性。

里程碑 B 完成後，CEO 親驗 dataset manifest、leakage 負向測試與異常 fixture。

### 里程碑 C：受控改善（PR6–PR7）

#### PR6：Wrapper 候選評估與 controlled upgrade

內容：實作 `diagnostics → proposal → candidate build → sandbox/replay → review → human activation → monitoring → rollback` 狀態機；proposal 綁定診斷、候選、資料集、風險及 artifact checksum。

驗收：不可跳關、倒序或自我核准；未人工啟用永不進 production；ModelHub artifact 只有候選輸入身分；每次 activation 有具名決策及 rollback target；ModelHub 未複驗時保持 disabled。

測試：transition table、unauthorized activation／approval spoofing、sandbox isolation、checksum／版本錯配／provenance 缺失、activation failure 與 rollback 演練。

此 PR 涉及安全與啟用權限，必須由 harper（CISO）及 `/codex-review` 雙審。

#### PR7：RAG feedback 與版本化 gold set

內容：蒐集檢索結果、回饋及人工審查；建立 reviewer provenance；評估 retrieval、citation alignment 與 abstention。

驗收：gold label 與歷史答案嚴格隔離；gold set 變更有 reviewer、理由及版本；Evidence 不足時 abstain／降級；答案重複或高票仍不能成為 Evidence。

測試：historical-answer isolation、gold-set versioning、retrieval regression、citation binding、insufficient-evidence abstention、prompt-injection／惡意 feedback 污染。

里程碑 C 完成後，CEO 親驗狀態機、未授權啟用負向路徑、回滾及 RAG 隔離。

## 6. 每個 PR 的共同門檻

- 指定具名 reviewer；作者不得偽造自我批准，採 commit-bound reviewer attestation。
- 本機 tests、lint、build、`git diff --check` 與 required CI 全綠。
- 實際 branch 執行 eye scan；無 UI 也要記錄「無視覺變更」並檢查 API／報表／錯誤狀態的資料真實性。
- 執行 `/codex-review` 對抗審，修完所有 finding 後重跑；安全、權限、資料污染、artifact provenance 及成本敏感變更另加 harper（CISO）審查。
- PR 留下 findings、修正、測試、eye 與最終 disposition；不得 admin override。
- 合併前無 unresolved finding；合併後驗證 post-merge CI。
- 每個里程碑或累積超過三個 PR，先向 CEO 回報，不等全部完成。

## 7. 回滾

- 資料採 append-only；錯誤 labeler 停用並產生新版 observation，不刪改歷史。
- Dataset 以 manifest、checksum 與產製版本重建。
- 三軌各自有 feature flag／kill switch；labeler、builder、detector 可獨立停用。
- Wrapper 保存前一核准版本與設定快照；回滾不依賴 ModelHub 在線。
- provenance、Evidence binding 或 time boundary 驗證失敗時 fail closed。
- 生產發布另走 release workflow；部署前完成可驗證備份，部署後由 CEO 親驗 health 與變更旅程。

## 8. 主要風險

| 風險 | 防線 |
|---|---|
| OHLCV 冒充分析樣本 | 僅接受有 immutable `analysis_id` 的樣本 |
| future leakage | event／available time 契約、時間切分與負向測試 |
| outcome 被誤稱 truth | Evidence、outcome、human gold label 分類隔離 |
| 歷史回答自我污染 | 永久 `historical_non_evidentiary`，禁止升格 |
| wrapper 自我授權 | 狀態機、具名人工 activation、CISO 雙審 |
| ModelHub 狀態未知 | 唯讀複驗前 `unverified`／disabled；本地可回滾 |
| artifact 被替換 | checksum、固定版本與 provenance |
| 小樣本誤報 | insufficient-data 狀態與版本化基線 |
| DB 越權異動 | Eric purpose token 前停在設計階段 |

## 9. 執行授權邊界

本文件完成「規劃」，不等於授權七個 PR 自動開工。進入執行時，仍須依序建立 issue、由 CEO 確認當輪範圍、派背景副手執行並逐 PR 審查；任何 DB、secret、ModelHub 寫入或 production 行為須遵守各自額外授權門檻。

## 10. Issue 追蹤與依賴（2026-07-23）

每張新 issue 均標示預估工時且不超過 12 小時；wrapper 狀態機沿用既有 #414，避免重複開單。

| Issue | 工時上限 | 依賴 |
|---|---:|---|
| #501 Outcome 語意與資料規則 | 6h | 無 |
| #502 Kernel／Evidence／PIT 現況契約 | 10h | 無 |
| #503 ModelHub 唯讀複驗 | 8h | 無 |
| #504 Canonical contract／分類／serializer | 12h | #502 |
| #505 Persistence 相容性與 migration 閘門 | 8h | #504 |
| #506 `analysis-quality.v1` immutable event | 12h | #504、#505 |
| #507 Delayed outcome labeler | 12h | #501、#506 |
| #508 Calibration dataset／temporal manifest | 12h | #507、里程碑 A 親驗 |
| #509 分析異常偵測 baseline | 12h | #508 |
| #510 Wrapper artifact／activation／rollback | 12h | #414、#503；整合 diagnostic 時加 #509 |
| #511 RAG feedback／gold set | 12h | #504、具名 gold-set reviewer |
| #512 三軌 E2E／安全負向驗收 | 10h | #507、#509、#510、#511 |

可平行起跑的前置工作只有 #501、#502、#503；其餘必須等列出的上游完成並合併，不得依賴未合併分支。#505 不授權 DB 異動：若需要 migration，仍須 Eric 當次 purpose token。
