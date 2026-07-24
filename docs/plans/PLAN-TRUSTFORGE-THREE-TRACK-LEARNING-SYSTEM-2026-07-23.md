# TrustForge 三軌統一學習架構開發計劃

> 日期：2026-07-23
> 狀態：開發中但 CEO 審查未通過；里程碑 A／B／C 均未完成，禁止往主線或生產整合
> 進度稽核：2026-07-23（第二次複驗）；詳見第 11 節
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
- Repo-local `.githooks/pre-push` gate 全綠；TrustForge 不使用 GitHub Actions，
  `statusCheckRollup=[]` 是預期狀態。
- 實際 branch 執行 eye scan；無 UI 也要記錄「無視覺變更」並檢查 API／報表／錯誤狀態的資料真實性。
- 執行 `/codex-review` 對抗審，修完所有 finding 後重跑；安全、權限、資料污染、artifact provenance 及成本敏感變更另加 harper（CISO）審查。
- PR 留下 findings、修正、測試、eye 與最終 disposition；不得 admin override。
- 合併前無 unresolved finding；合併後在整合分支重跑 repo-local pre-push gate。
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

## 11. CEO 開發進度稽核（2026-07-23，第二次複驗）

### 11.1 稽核結論

目前只能判定「已產生實作分支與 stacked PR」，不能判定任何里程碑完成：

- `main` 尚未包含三軌功能。
- #501–#512 全部維持 OPEN。
- 部分 GitHub PR 顯示 MERGED，但只合併到上一層 feature branch，不代表已進入 `develop`。
- #529 已合併到 `develop`，完成移除未授權 #517；#523 已無 merge conflict，但仍是
  Draft，尚未完成乾淨替代與 #502 驗收。
- #528 已有新 commit `d4e58557b85705139dcd872ef13b2c8806845d18`；#543 所指的
  delayed-price PIT leakage 經隔離重現與 8 項測試複驗已修復，但 PR 仍為 Draft、缺完整
  local pre-push gate 證據，且 stacked 依賴尚未合法整合。
- #532、#533、#535 沒有新 commit，既有 anomaly leakage、安全與 E2E blockers 持續有效。
- 第一輪聚焦驗證為 136 passed、5 failed；第二輪只對 #528 修正範圍跑 8 passed。
  兩輪皆未提供完整 repo-local pre-push gate 全綠證據。
- 里程碑 A 未通過，因此里程碑 B、C 不得往主線整合或用於 ModelHub／production activation。

### 11.2 Issue／PR 真實狀態

| Issue | PR | 2026-07-23 審查狀態 | 下一步 |
|---|---|---|---|
| #501 | #519／#526 | #519 已進 `develop`，但 corrective #526 無新證據即被標 Ready，已退回 Draft；語意尚未拍板 | 完成交易日曆、T+N、缺值、修訂與 `available_time` 規則 |
| #502 | #517／#529／#523 | #529 已合併並移除 #517；#523 現為 CLEAN／Draft，但 head 未更新、缺完整 local pre-push gate 與 reviewer PASS | 將 #523 更新到 revert 後的乾淨 `develop`，完成真正負向 contract tests 後重新雙審 |
| #503 | #521 | 已進 `develop`，Issue 仍 OPEN | 核對唯讀 capability、tenant、artifact provenance 證據後才能關閉 |
| #504 | #522 | 已進 `develop`，但依賴的 #502 正在事故復原 | #502 乾淨替代通過前不得宣稱完成 |
| #505 | #525 | 只合併到 `feat/504-three-track-contract` | 等合法 #504 基線後重審；不授權 migration |
| #506 | #527 | 只合併到 `design/505-learning-event-storage-gate` | 等 #504、#505 合法整合後重審 |
| #507 | #528 | Draft；新 head `d4e5855` 已修復 #543 future-price leakage，隔離測試 8 passed；仍缺完整 local pre-push gate、上游治理未完成 | 保持 Draft，完成合法 base／依賴、local pre-push gate 與整張 PR 重審 |
| #508 | #530 | 只合併到尚未通過的 #507 feature branch | #507 與里程碑 A 親驗通過後才能重審 |
| #509 | #532 | Draft；被 #544 future leakage 阻擋 | baseline 排除未來才 available 的事件並補 leakage 測試 |
| #510 | #533 | Draft；harper CISO 審查 FAIL：activation 可跳過 sandbox／approval，actor／probe evidence 可偽造，rollback 綁定不足 | 完成 #503 證據、authorization/state binding、新 SHA 的 harper CISO 與 `/codex-review` 雙審 |
| #511 | #534 | 只合併到 `feat/504-three-track-contract` | 等合法 #504，驗 reviewer provenance 與歷史答案隔離 |
| #512 | #535 | Draft；上游未完成，E2E 未覆蓋真實 leakage、跨 tenant RAG 與 activation 跳關 | 等 #507、#509、#510、#511 以已審 commit 合併後重建 E2E 基線 |

### 11.3 Blocking findings

1. **#524／PR #517 未授權合併事故：部分解除**
   #529 已合併並移除 #517；#524 仍 OPEN，因 #523 尚未成為通過審查的 #502 乾淨替代。
   #523 合併前，#504 及其下游仍不得以 #502 完成為前提宣稱完成。
2. **#543／PR #528 delayed outcome future leakage：finding 已修，PR 未完成**
   新 commit `d4e58557b85705139dcd872ef13b2c8806845d18` 已讓 start／target price
   同時拒絕 `available_time > as_of_time`，並補 future-start、future-target 與 exact-cutoff
   測試；隔離測試 8 passed。#543 仍 OPEN，應在 #528 合法整合與證據完成後關閉。
3. **#544／PR #532 anomaly future leakage**
   anomaly baseline 會納入未來才 available 的事件；修正前不得作為 diagnostic 基線。
4. **測試與輸出隔離缺陷**
   聚焦測試有 5 項失敗：2 項 calibration path 介面漂移、3 項 backfill contract／fixture
   失敗。Backfill 測試曾直接追加 tracked `data/training`，工程師必須改用隔離的暫存輸出，
   不得污染 repo 訓練資料。
5. **缺完整 local pre-push gate 證據**
   TrustForge 刻意停用 GitHub Actions，空的 status checks 不是 blocker。真正 blocker 是相關
   PR 只有局部測試，尚未留下 `.githooks/pre-push` 全套 tests／lint／build／data checks／
   `git diff --check` 全綠證據；不得只憑局部測試或 GitHub `MERGED` 標籤改為完成。
6. **#533 activation authorization／狀態機可繞過**
   `activate_wrapper_artifact()` 未證明 sandbox 與 proposal approval 已通過；human actor
   採字串黑名單而非 authenticated principal／role／approval record，存在核准偽造。
   caller 只需提供最小 `{"status":"verified"}` 即可開啟 ModelHub gate；`config_snapshot`、
   activation event、artifact 與 rollback target 亦未可靠綁定。harper CISO 已對 commit
   `141fdcbda810c9b9ef1c557b89c28a9d6f446cf7` 給出 FAIL，修正後必須以新 SHA 重新雙審。
7. **#535 E2E 未覆蓋真實阻擋路徑**
   雖 #528 已補自身 delayed-price 測試，#535 head 未更新，仍未驗該修正與 #544 anomaly
   event 的整合 PIT 排除；RAG 未驗 cross-tenant
   negative retrieval；activation 未驗 human-like spoof、未授權 role 或跳過 sandbox；
   rollback 未驗 config restore 與錯誤 target。
8. **相關 PIT 基線 #520 尚未整合**
   #520 仍為 Draft／OPEN 且 merge state DIRTY；即使已有舊 commit-bound APPROVED，也缺少
   完整 local pre-push gate 證據，不能視為 timezone／naive timestamp 邊界已進入合法基線。

### 11.4 三軌能力判定

| 軌道 | 現況 | CEO 判定 |
|---|---|---|
| Question RAG | 已有 SQLite 字元 bigram 歷史問題檢索與 lineage；embedding index、reranker、完整 gold-set 流程未整合 | 部分完成 |
| 分析異常偵測＋信心校準 | #528 delayed-price PIT finding 已修；但 #532 anomaly PIT leakage、校準測試漂移、合法資料集整合與完整 local pre-push gate 尚未完成 | 未完成 |
| Wrapper 受控升級 | 已有 ModelHub package、sandbox 與人工 activation gate；明確禁止自動套用 | 部分完成，安全整合未通過 |

「外框自我升級」在本計劃永遠指受控候選升級，不是模型自行批准、遞迴修改或自行上線。

### 11.5 恢復開發與重新送審順序

1. #529 revert 已完成；下一步更新並審查 #523，恢復可信 #502 基線。
2. 完成 #526，正式拍板 #501 outcome 語意。
3. 依合法依賴重整 #504 → #505 → #506；不得沿用未審 stacked merge 當完成證據。
4. #528 的 #543 finding 已修；待 #501、#502、#504–#507 合法整合後，CEO 親驗里程碑 A
   的事件、重放、timestamp 與 PIT 邊界。
5. 里程碑 A 通過後才重審 #508，再修正及重審 #544／#532。
6. #533 完成 harper CISO＋`/codex-review` 雙審；#534 重新核對 RAG 隔離。
7. 最後將 #535 更新到全部已審、已合併的上游 commit，執行三軌 E2E 與安全負向驗收。

每個工程師修正後，必須在原 PR 回覆修正 commit、測試命令與結果，並重新標記
Ready for review。沒有新 commit／證據而只切換 Ready 狀態，視為無效重送並退回 Draft。
CEO 重新審查通過前，Issue 保持 OPEN、PR 必須維持 Draft。
