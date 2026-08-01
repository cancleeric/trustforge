# Hermes 生產修復與漸進啟用計劃

> 日期：2026-07-30
>
> Owner：待 CEO 指派；執行需 CPO（gray）與 CISO（harper）審查
>
> 依據：[`docs/HERMES-CAPABILITIES-REVIEW.md`](../HERMES-CAPABILITIES-REVIEW.md) 與 2026-07-30 公開 production API 實測
>
> 生產目標：AWS `ap-southeast-2`／EC2 `trustforge-demo`（`<EC2_INSTANCE_ID>`）
>
> Baseline：production `v0.27.37`；工作分支 `chore/808-811-closeout`
>
> 狀態：**規劃文件；未授權變更 production 旗標、service、資料或排程。**

---

## 1. 決策摘要

**Hermes 主體已在 production 運行，現在不應把「啟用 Hermes」當成待辦。**已證實運行的是 Hermes 30 分鐘循環、continuous Analysis Flow、DynamoDB cache、research snapshot memory 與 Bedrock capability。待處理的是讓它能以可驗證的資料品質穩定產出，並在獨立審查下漸進啟用兩個附加能力：Three-track learning 與 AGOS runtime。

採取以下順序，任一關卡未通過即停止於前一階段並回滾，不以「旗標已設」宣稱完成：

```text
0. 重新取得控制面真相與建立可比較基準
   → 1. 修復資料取得、快取新鮮度與 Analysis Flow 失敗
   → 2. 證明 Hermes 正常消耗新鮮資料並產出完整溯源
   → 3. Three-track learning 小流量 append-only pilot
   → 4. delayed outcome + versioned dataset 閉環
   → 5. AGOS runtime 獨立 canary
   → 6. 正式營運驗收與持續監測
```

**現在不得直接開啟** `TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED` 或 `TRUSTFORGE_AGOS_ENABLED`：production 已顯示來源快取完全缺失、連接器可靠度不佳、15 筆 dead letter，直接收集或檢索這些資料會擴大低品質資料、難以判讀成效，並提高儲存與治理風險。

---

## 2. 事實基準、限制與正確解讀

### 2.1 已由正式公開 API 觀測到的事實

以下為 2026-07-30 對 production HTTPS endpoint 的只讀查詢結果；它們是本計劃的可比較基準，不替代登入主機後的 systemd、DynamoDB 與檔案層稽核。

| 面向 | 觀測 | 正確解讀 |
|---|---|---|
| 程式版本與服務 | `/api/health`：`ok`、`v0.27.37` | Web process 可用，不等於每個 Hermes 子系統健康。 |
| Analysis Flow | `/api/analysis-flow`：`agent=hermes`、`state=continuous`、5 個 stage 均無 queued/current、`dead_letter_count=15` | daemon 有運行且 queue 目前清空；15 筆失敗工作必須逐筆分類與處理。 |
| Hermes capability | `/api/intelligence-status` 列出 14 個 Hermes tools | tool 已註冊、不是每個 tool 每輪都成功執行，也不證明 AGOS 已啟用。 |
| Research memory | `/api/memory-strategy`：634 snapshots、涵蓋 ARB/BNB/BTC/ETH/SOL/XRP，最新為當日 10:25:55Z | research snapshot 是跨 run、PIT 約束的記憶；它不是 AGOS memory，也不是 training dataset。 |
| 模型能力 | `/api/status`：`bedrock_capable=true`；`/api/costs` 累計 Haiku 4.5 為 83,794 input、32,691 output tokens、USD 0.247249 | Bedrock 確實曾被使用；近期 ledger 大量 `offline=true`、token 0，故不得稱每次自動 run 都使用模型。 |
| 歷史訓練資料 | `/api/training-status`：2,005 筆、146 筆有 direction；BTC 307、ETH 489、SOL/BNB/XRP 各 403 | 舊 training JSONL 已可由 API 正確讀取；這修正了舊 review 中 API 回 0 的觀測缺口。它仍不是即時 pipeline 的 append 目標。 |
| 快取新鮮度 | `/api/status`：150 個 source × coin entries 均 `missing`，`fetched_at=null` | 這是目前最優先的資料品質事故。不能據此推論每個 source 都永久失效，需從 scheduler logs 與 cache key/TTL 逐項驗證根因。 |
| 改善診斷 | `/api/improvement-diagnostics`：30 個 scheduler runs；coingecko-dev/sentiment、cryptoslate、reddit 等 96.67–100% failure；狀態 `attention_required` | Hermes 有產生改善 proposal，但 proposal 不是自動修復或 deployment 授權。 |
| AGOS / upgrade control | `/api/agentcore/status` 為 `inactive`；`/api/intelligence-status` 的 `skills=null`；`/api/hermes-upgrades` 回 `upgrade_control_unavailable` | AGOS runtime 未觀測為 active；upgrade 控制面另有可用性缺口，與 Hermes cycle 成功與否應分開處理。 |
| 報告交付 | `/api/delivery-status` 最新結果多筆 `has_provenance=false`、`has_contrarian_evidence=false`；citation format 回 `ValueError` | 報告產物存在，但未達 TrustForge evidence contract。這是 blocking quality gap，不能以字數或 report count 取代。 |

### 2.2 尚未取得、不可臆測的控制面真相

本次 AWS CLI 的 SSO session 已過期，未讀取 production 主機或 DynamoDB 的私有狀態。因此下列項目必須在 Phase 0 以只讀 SSM 與 AWS 查詢確認，未確認前不得把 repo 安裝腳本視作部署事實：

- `hermes-cycle.timer`、`hermes-cycle.service`、`trustforge-analysis-flow.service` 與 `fetch-scheduler.timer` 的 enabled/active、最近成功/失敗、unit fragment 與 drop-in。
- Hermes autonomy 的最終來源。`autonomy_enabled()` 的優先序是 runtime stop / production guard → DynamoDB `hermes_autonomy_enabled` → `TRUSTFORGE_HERMES_AUTONOMY_ENABLED` → production fail-closed default。
- 目前 deployed systemd unit、runtime override 與 Git release manifest 的 digest。repo 的 `deploy/install_hermes_scheduler.sh` 預設寫入 `TRUSTFORGE_HERMES_AUTONOMY_ENABLED=0`，這與先前「production 為 1」的報告可能是部署 override 或 admin config 所致，**不是可忽略的矛盾**。
- cache table 的實際 key schema、TTL、最近寫入、讀取 region/table、connector error payload 與 scheduler run log。
- dead-letter 15 筆的 stage、error class、重試/隔離狀態與是否已造成相同 job 重複執行。
- Three-track event store 的容量、加密、保留期、存取邊界與現有 event 數量；AGOS storage、frozen skill revision、tool-audit backing store 與 migration status。

### 2.3 四條資料路徑不可混淆

| 資料面 | 寫入者 | 允許用途 | 本計劃的處理方式 |
|---|---|---|---|
| `data/training/*.jsonl` | Backfill / semantic backfill | 歷史 calibration 資料集 | 保持唯讀式既有語義；不得由即時 run 直接 append。 |
| TrustFeatureStore / `analysis_results` / lineage | Analysis Flow | 即時查詢、報告與可追溯 execution | 先修正 delivery/provenance，再用作 quality 衡量。 |
| Research snapshots | Hermes cycle / fetch scheduler | 跨 run 研究記憶、PIT evidence selection | 修復 ingestion、cache freshness 與來源可靠度。 |
| Three-track learning events | completed/dead-letter hook | append-only prediction / quality event，供延遲標註 | 僅在 pilot 後啟用；先有 schema、retention、outcome labeler。 |
| AGOS memory / skills / tool lineage | AGOS runtime | context、frozen skills、retrieval lineage、tool audit | 與 learning events 分案啟用；不得影響 Trust score 或 Evidence eligibility。 |

---

## 3. 目標、非目標與不可違反條件

### 3.1 目標

1. 將「timer/daemon 活著」提升為「每次正式分析使用可觀測、可追溯、符合 freshness contract 的資料」。
2. 讓每個 auto/manual run 可回答：使用哪些 snapshot、哪些 source、是否實際使用 Bedrock、為何降級、是否有相反證據、及其 execution lineage。
3. 建立 Three-track prediction → delayed outcome → versioned dataset 的閉環，不把未標註即時輸出污染既有 training JSONL。
4. 在不改 Trust Kernel、不放寬 Evidence contract、不讓 agent 自行部署的前提下，獨立驗證並啟用 AGOS context / skill / tool audit。
5. 每一個會改 production 行為的步驟都具備：明確 ownership、預檢、可驗證 success、停止條件、回滾操作與審計紀錄。

### 3.2 非目標

- 不把任何外部 LLM 或外部現成市場結論接入 pipeline；模型入口仍唯一是 `src/trustforge/bedrock.py`，模型僅負責帶引文行文及有界語義任務。
- 不讓 Three-track 或 AGOS 自動調整 Trust weights、資料來源信譽、Evidence binding、production flag、merge 或部署。
- 不以開啟 Backfill daemon 取代 delayed outcome labeler，也不把 historical backfill 偽稱為 live learning。
- 不為消除 missing 指標而盲目降低 freshness/TTL、吞掉 connector error、或將 stale data 標為 fresh。
- 不進行 production 直接修補、資料刪除、schema migration 或 flag change；本文件本身沒有變更授權。

### 3.3 不可違反條件（invariants）

1. 所有 market judgement、證據整合與 trust scoring 都由 deterministic TrustForge pipeline 產生；Bedrock 不得成為主要判斷來源。
2. 每個報告的 claim 必須可追到 source、URL/reference、published/fetched/snapshot timestamp、claim id 與 PIT eligibility；缺任何必要欄位時降級/標缺口，不能偽造 provenance。
3. 原始 connector error、failed snapshot、dead letter 與反向/低信任證據必須保留可觀測性；不得透過靜默丟棄使儀表板變綠。
4. production control 預設 fail-closed。未知 flag、DynamoDB config read error、未驗證 release identity、無法讀取 approval state、缺 metric 均不能擴大功能。
5. Three-track 和 AGOS 都是 additive、可關閉、可回退的功能；關閉後不得破壞既有 Analysis Flow 或造成已寫入資料無法讀取。
6. 每個 run 的總時間仍受 900 秒 Hermes budget / 15 分鐘 pipeline 上限約束；connector、labeler、replay 與 retry 必須有各自上限。
7. 不記錄秘密、raw authorization token、未遮罩 prompt 或不必要的使用者識別資訊；append-only audit payload 使用 hash/reference 而非敏感原文。

---

## 4. 根因假設與診斷策略

目前只能確認「cache/read model 顯示完全 missing」與「多個 connector 在過去 30 次失敗」。以下是要驗證的假設，不是已定案根因。

| 優先 | 假設 | 需要的只讀證據 | 對應修復方向 |
|---:|---|---|---|
| P0 | fetch scheduler 未安裝、inactive、或沒有啟用 | `systemctl status/list-timers`、unit files、journal、scheduler run table | 依 release runbook 重新安裝/enable timer；先 dry-run，再觀測兩個週期。 |
| P0 | Hermes cycle 有執行，但 autonomy 被 runtime/config precedence 關閉或部分工具未排程 | service environment、`systemctl show`、DynamoDB admin config、run log 中 tool decisions | 對齊單一授權來源，新增 control-source disclosure；不以 env 覆蓋 emergency guard。 |
| P0 | writer 與 reader 使用不同 region/table/key prefix/coin pool，或 TTL cleanup 過度清除 | deployed config snapshot、DynamoDB key/TTL、cache read/write metrics、sample records | 修正共享 configuration/normalization；以 cache contract tests 防回歸。 |
| P0 | connector 被上游拒絕、憑證/headers 過期、rate-limited、DNS/TLS 改變或 parser drift | redacted HTTP status/error class、response schema hash、bounded manual probe | 逐 connector 修 parser/header/retry/fallback；來源不合規則停用並呈現缺口。 |
| P1 | 6 幣/來源清單不一致（API 顯示 ARB，競賽核心池為 5 幣）造成錯誤 key 或不必要錯誤率 | effective configuration、manifest coin pool、scheduler target list | 將正式範圍明文化；核心交付以 BTC/ETH/SOL/BNB/XRP 為必須，ARB 是否支援另行決策。 |
| P1 | Analysis Flow dead letters 來自舊 release、schema migration、可重試外部失敗或不可重試資料錯誤 | 15 筆 job lineage、attempts、release/version、correlation id | 分類為 retry/quarantine/code fix；不得無條件 requeue。 |
| P1 | report delivery 的 citation `ValueError` 或 provenance false 為 serializer/資料缺欄/讀取路徑問題 | failing payload、stack trace、source/claim cardinality、contract tests | 先修 evidence assembly / serializer；報告 UI 顯示 incomplete，不可宣稱已引證。 |
| P1 | `hermes-upgrades` unavailable 是 migration/control store 不完整 | endpoint dependency health、schema/version、DynamoDB/SQLite store health | 恢復 read-only control surface；仍維持 upgrade activation 人審。 |

---

## 5. 分階段解決方案

### Phase 0 — 控制面、資料面與發布身分稽核（先於任何開關）

**目的：**把可公開觀測的症狀，補成可操作的 production 事實基準，避免錯誤地切換 env 而被 DynamoDB/admin control 覆蓋。

**執行前置：**具有效期限的 AWS SSO/STS 憑證、`AmazonSSMManagedInstanceCore` 可用、明確 issue 與 CEO 核准。只讀檢查不改 systemd、不重啟服務、不改 DynamoDB。

**必做檢查：**

1. 以 SSM 取得四個 unit 的 `is-enabled`、`is-active`、`systemctl show`、`systemctl cat`、最近 48 小時 `journalctl` 與 timer 最近/下次執行時間：
   - `hermes-cycle.timer` / `hermes-cycle.service`
   - `trustforge-analysis-flow.service`
   - `fetch-scheduler.timer` / `fetch-scheduler.service`
2. 讀取 service 的有效環境（不得輸出 secrets）及 config snapshot，明確列出 `CACHE_BACKEND`、region、table names、coin pool、timeouts、budget、Hermes/Three-track/AGOS flag 值。
3. 對 DynamoDB admin config 進行只讀查詢，記錄 `hermes_autonomy_enabled` 值、版本、更新時間及讀取錯誤；不將 config read failure 視為「env 應接管」。
4. 對 scheduler run table、connector cache table、cost ledger 與 analysis SQLite/Dynamo projection 取樣：寫入時間、key/version、TTL、error class、成功率、執行 release digest。採用 hash 或聚合統計，避免將原文證據/敏感輸入帶出主機。
5. 將 deployed artifact manifest、git SHA、`VERSION`、Python 版本及 systemd unit digest 與預期 release identity 對照。若不相符，先走 deployment/release remediation，不在漂移主機直接編輯。
6. 對 15 筆 dead letter 建立 triage 表：job id hash、coin、stage、first/last attempt、error class、是否可安全重試、owner、處置狀態。

**退出條件：**每個 runtime flag 都能指出最後生效的控制來源；每個 missing entry 有相應 writer/read path 及 error/absence 證據；release identity 可驗證。

**停止條件：**SSM 或 config/read evidence 無法取得、release digest 漂移、production guard 已觸發、或發現資料/憑證疑似外洩。停止後維持原旗標，轉安全事件或 deployment rollback 流程。

---

### Phase 1 — Ingestion、cache freshness 與 dead-letter 修復

**目的：**先恢復「有可信 input 才分析」，而非提高報告數量。

**實作切分（每項獨立 issue/PR）：**

| Work item | 變更 | 驗收 | 回滾 |
|---|---|---|---|
| I1：scheduler truth | 修正/補齊 fetch scheduler 安裝、timer enablement、run log 與 status disclosure；不改資料語義 | timer 跨兩個預定週期執行，run log 有 release id、input count、error summary、duration；無超過預算 | 停用新 timer，保留 Hermes cycle 與已存在資料；還原 unit/release artifact。 |
| I2：cache contract | 將 writer/reader 的 region/table/key/coin normalization/TTL 收斂為同一 contract；新增讀寫、TTL、跨 process contract tests | 每個核心幣種至少有已列入 allowlist 的 source fresh/stale/missing 明確狀態；不得把 stale 偽標 fresh | 回復前一 artifact/config；cache schema 使用 versioned prefix，不破壞既有 read path。 |
| I3：connector remediation | 針對 P0 connectors 以 redacted error class 診斷；只修可驗證的 parser/auth/header/rate-limit/fallback；來源條款不允許即停止使用 | 每個被修 connector 連續 7 個受限週期成功，或清楚標為 quarantined with reason；重試有總時限/次數 | feature-level disable 該 connector；保留 error metric，不能吞掉失敗。 |
| I4：dead-letter triage | 可重試者按 idempotency key 有界 requeue；不可重試者 quarantine；程式缺陷以 regression fix 修復 | 不重複交付、不重複成本計帳；dead-letter 計數下降有 lineage；未知錯誤不自動 replay | 停止 requeue worker，保留原 event 與 retry receipt。 |
| I5：delivery evidence | 修復 citation serialization/`ValueError` 與 provenance/contrarian evidence contract | 標準 report 有 claim→evidence refs、反方證據或明確「未取得」；無資料時回 incomplete 而非正常完成 | 回到上一版 delivery formatter；不回填或捏造 citation。 |

**Phase 1 release gate：**

- 連續 7 次 schedule 中，核心五幣每次至少有一個合格來源或有原因可追的缺口；不得以成功率平均掩蓋單一核心幣完全無資料。
- `/api/status` 顯示的 `fresh` / `stale` / `missing` 與 cache table 抽樣一致；`missing=150` 已有明確下降，或每個保留缺口有 owner/ETA/quarantine reason。
- 15 個 dead letters 全數完成分類；未知類型為 0，requeue 前後 idempotency 及 cost ledger 可對帳。
- `/api/delivery-status` 的 citation format 不再 error；抽樣報告各有 provenance 和 contrarian evidence，或標示 evidence unavailable（不宣稱可靠結論）。
- 所有 connector、scheduler、manual analysis 的最差情況仍在 15 分鐘總時限內。

---

### Phase 2 — Hermes 核心營運驗收（不新增學習）

**目的：**在 Three-track/AGOS 仍關閉的狀況，證明既有 Hermes 已能用新鮮資料提供可重現輸出。

1. 於 staging/release candidate 先跑 48 小時或至少 20 個 30 分鐘週期，再選擇低流量 production observation window。
2. 對每個 run 記錄：開始/結束、control source、selected tool、資料時間窗、source/snapshot count、freshness、offline/Bedrock mode、token/cost、結果 status、degradation reason、release/skill manifest hash。
3. 建立 SLO：timer 成功率、end-to-end deadline、freshness coverage、connector failure rate、dead-letter rate、report provenance coverage、contrarian evidence coverage、offline fallback rate、Bedrock spend。每個 SLO 有 owner、窗口、最低樣本與 `insufficient-evidence` 狀態。
4. 只有結構化 pipeline 結果可進 report；Bedrock 的 `extract_claims`、`classify_stance`、`assemble_report` 僅在 budget/rate-limit gate 通過且 evidence selection 已凍結後使用。
5. 若 Bedrock 因預算、未定價模型或錯誤降級為 offline，報告及 run log 必須顯示「本次未使用模型」與原因；不得把歷史 token/cost 當作本次模型輸出證據。

**Phase 2 gate：**連續 20 個週期達到 Phase 1 指標，且至少涵蓋五個核心幣的 successful + degraded + missing-source 情境。CEO/CPO 應親自抽查 report、evidence JSON、execution log 三件交付物互相可對照後，才可開始任何 learning pilot。

---

### Phase 3 — Three-track learning 的小流量、append-only Pilot

**目的：**只收集可審計事件，不改 calibration、不改 Trust score、不修改舊 JSONL、不自動訓練。

**先決條件：**Phase 2 通過；harper 對資料分類、retention、IAM、encryption、容量/成本、失敗模式完成安全與成本審查；gray 確認 product disclosure；CEO 明確批准 pilot scope 與 stop threshold。

**設計：**

- 開關為 `TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED`，僅接受 `1/true/yes/on`；hook 每次 evaluate，未設定或未知值均為 off。
- 在 durable `completed` / `dead-letter` 已落地後，以 fail-soft、idempotent event id 追加 event。event write 失敗不可令已完成 report 改為失敗，也不可隱藏 write failure metric。
- event 最小欄位：`event_id`、`schema_version`、`run_id`、`job_id`、coin、stage outcome、created_at、model/offline mode、trust/confidence snapshot、evidence/snapshot/skill/config hashes、release digest、quality/error code、redacted input/output references。不得存 raw secret、token、可識別使用者或未經治理的完整 prompt。
- 僅允許 append；以 TTL/retention policy、KMS encryption、least-privilege writer/read roles、immutable audit digest 管理。資料庫不可用時進 bounded retry/quarantine queue，不得無限累積 memory。
- 先採 **1 個 canary coin 或 1% 已完成 jobs（取較低者）**、固定 24 小時、明確 event/byte/cost cap；沒有已驗證可做 cohort 的條件時不開啟。

**Production 切換程序：**

1. 在 staging 以 synthetic-but-schema-valid completed/dead-letter jobs 驗證 event count、idempotency、redaction、readback、off-state no-write 與 write-failure fail-soft。
2. 將 schema/migration、dashboard、alert、rollback script 以獨立 PR/release 部署；flag 保持 off。
3. 建立 deployment-bound change ticket，記錄部署 manifest、flag 原值、pilot cohort、窗口、cap、operator、CISO/CPO/CEO 審核與預期 event rate。
4. 由受控 configuration/環境將旗標切至 approved value，立即讀回 effective config；不以 log 中「已設定」取代生效驗證。
5. 於 5、30、120 分鐘及 24 小時檢查 event count、schema validity、duplicate rate、write failures、queue depth、Analysis Flow latency、dead letter、storage/cost、secrets scan、rollback readiness。
6. pilot 到期後自動/人工將 flag 關閉，凍結資料集並生成 immutable summary；是否擴大須為新的批准動作。

**停止與回滾：**

任一條件觸發即關閉 flag、驗證新 event 停止寫入並保留現有事件供稽核：schema violation、敏感資料寫入、重複率超門檻、event write 造成 flow failure/超時、儲存/成本超 cap、資料來源新鮮度再度失守、未授權 control-plane drift。關閉後不刪除資料；清理/修復必須走獨立 retention/security 程序。

**Phase 3 exit criteria：**至少 24 小時且有預先約定最小樣本，事件 100% 可解碼、hash 可回查、無未處理敏感資料/duplicate/timeout，Analysis Flow SLO 無回歸。這只證明「安全收集」，不代表「已學習」或「可重訓」。

---

### Phase 4 — Delayed outcome labeler 與版本化 dataset

**目的：**建立受 PIT 約束的標註和訓練候選資料集，避免自我訓練與 future leakage。

1. 新增獨立、低權限的 delayed outcome labeler，不共用 Hermes cycle 的實時 budget。只處理超過明確 horizon（例如 T+7）且取得官方 OHLCV 的 prediction event。
2. labeler 以 event creation time 當 cutoff；只連結該時點之後、符合定義的 OHLCV outcome。每筆 label 寫入 source file/hash、coverage、price timestamp、computed-at、code/version hash 與 missing/anomaly reason。
3. 使用 append-only `prediction_events` + `outcome_labels` + `dataset_manifest` 概念，依時間切分產生 dataset version。資料集須記錄 event range、label horizon、source hashes、feature schema、exclusions、split policy、generator/release digest。
4. `data/training/*.jsonl` 與現有 calibrator 是 legacy path；若未來要消費新 dataset，需另開 calibration evaluation issue，先做 out-of-time holdout、回放、drift、fairness/coverage 與 rollback comparison。不得因 training status 的 146/100 threshold 已 met 而直接重訓/升級。
5. dataset promotion 只能由人工批准；scheduler/agent 可提出 proposal，不能把 event 直接變成 calibration input。

**Phase 4 gate：**至少一個完整 horizon 的資料能從 prediction event 重播至 outcome label，無 future leakage、無 missing lineage，並在不同時間 split 上產生可審計 evaluation report。尚未達樣本/品質門檻時，結論應是 `insufficient-evidence`。

---

### Phase 5 — AGOS runtime 獨立 Canary

**目的：**啟用 context manifest、frozen skill revision、memory retrieval lineage 與 tool invocation audit；不將 AGOS 與 learning collection 視為同一切換。

**先決條件：**Phase 2 通過；Phase 3/4 可獨立進行但不為 AGOS 前提。完成 harper 對 tool approval/fail-closed、memory isolation、audit storage 的審查，以及 gray 對 UI/用語誠實性的審查。

**設計與驗證：**

- 開關為 `TRUSTFORGE_AGOS_ENABLED=1`；非精確 `1` 均維持 disabled。未知/未核准/未初始化 tool 必須 fail closed。
- 第一階段限制在 read-only context build、frozen skill manifest、memory retrieval lineage 與 invocation audit；禁止任何 `write_external`、deployment、Trust Kernel override、Evidence eligibility override 或自動 activation capability。
- 每個 canary run 產生 immutable context manifest：snapshot refs、memory refs、skill revisions、tool capability refs、policy refs、excluded refs、reason、content hash、created_at。active pointer 在 run 開始後變更，不得改寫既有 manifest。
- UI/API 必須把 `context_only`、candidate evidence、trusted evidence、proposal 明確分開。歷史 memory 或對話召回不得進 Trust score / Evidence list，除非各自通過 evidence contract。
- 先以 shadow/no-op audit mode 對 1% 或單一 canary coin run 產生 manifest，不改變 report selected evidence；與既有 run 做 payload/hash/latency 對照。確認無回歸後才允許對同 cohort 使用 retrieval context。

**停止與回滾：**關閉 `TRUSTFORGE_AGOS_ENABLED`、驗證新 run 回到原 context path；保留既有 manifest/audit。若出現 policy bypass、未核准 tool、memory 進入 scoring、manifest hash 不可重現、latency/cost 超 cap、或敏感資料暴露，立即停止並由 harper 主導調查。

**Phase 5 exit criteria：**足夠 canary 樣本中，100% run 有可讀 immutable manifest、skill/tool 都能追至 approved revision/capability、未知 tool 無執行、任何 context-only item 均不出現在 Evidence / Trust score input；desktop/mobile UI 可清楚顯示差異且 eye scan 通過。

---

### Phase 6 — 正式營運、持續監測與誠實對外口徑

只有前述各 phase 都各自通過時，才可將 cohort 漸進擴至正式範圍。擴大順序應是單 coin → 五個競賽核心幣 → 其他支援資產；每次擴大都需新 window、數據、owner 與 rollback 依據。

對外與 UI 用語必須符合實際：

- 「Hermes scheduled research enabled」不等於「每一個 run 使用 Bedrock」。
- 「Three-track pilot active」不等於「模型正在自動訓練或自行改善」。
- 「AGOS context/audit enabled」不等於「記憶可作 Evidence」或「agent 可自行部署」。
- 來源不足、模型 offline、connector quarantine、資料集未達樣本時，顯示限制與 `insufficient-evidence`，不補強為市場結論或投資建議。

---

## 6. 可觀測性、告警與營運儀表板

### 6.1 必備 metrics

| 類別 | 指標 | 需能回答的問題 |
|---|---|---|
| Runtime | timer due/started/completed/duration/exit、control source、release digest | Hermes 有沒有依時限實際完成？是否被哪一層關閉？ |
| Ingestion | per source/coin attempts、success/failure class、rate limit、fresh/stale/missing、cache write/read/TTL | 是上游失敗、排程未跑、還是 cache contract 不一致？ |
| Analysis Flow | stage queue/current/retry、completed/dead-letter、idempotency/requeue、duration | queue 清空是否掩蓋了失敗或丟失？ |
| Evidence delivery | claim/evidence coverage、provenance completeness、contrarian coverage、citation error | 報告是否真正符合 trust/evidence contract？ |
| Bedrock/cost | per run offline reason、model calls/tokens/cost、daily cap/reservation | 是否在受控成本下使用且可明確解釋降級？ |
| Learning | events attempted/written/duplicate/rejected/quarantined、bytes/retention、label coverage | Pilot 是否只做收集且不污染資料？ |
| AGOS | manifests created/verified、retrieval count、skill/tool revision、denied invocations | context 與工具治理是否可重現、fail closed？ |

### 6.2 告警原則

- 以狀態/趨勢告警，不以單次 connector transient error 直接 page；連續失敗、所有核心幣 missing、deadline breach、dead-letter 增速、provenance/citation regression、control source drift、sensitive-data detector、cost/retention cap 是高優先告警。
- 告警必須附 run/release hash、時間窗、聚合 error class、owner/runbook；不含 secret 或完整市場內容。
- 監測資料不足時告警顯示 `insufficient-observation`，不以綠燈表示健康。

---

## 7. 驗證、review 與 release 證據

### 7.1 每個實作 PR 的最低證據

1. issue 的明確 acceptance criteria、依賴與 non-goals。
2. scoped branch；只包含本 work item 的程式、migration、config、測試與文件。
3. regression tests：flag default-off、runtime precedence、cache contract、idempotency、PIT、redaction、fail-closed、rollback，依所改模組選擇。
4. repository `.githooks/pre-push` 全數通過：backend coverage ≥75%、lint/build/data checks、competition QA、frontend checks（若有 UI）與 `git diff --check`。
5. `/codex-review` 對抗審查；每個 finding 都有 fix 或明確拒絕理由及 reviewer disposition。
6. CPO review；涉及 learning、AGOS audit store、IAM、retention、cost、scheduler 或 production flags 時，必須 harper security/cost review。UI 變更須實際 branch 的 desktop/mobile eye scan。
7. commit-bound evidence：baseline SHA、artifact/manifest digest、檔案清單、命令與輸出摘要、未跑項目與原因、rollback drill 結果。作者不自我批准；以 reviewer attestation 取代虛構 GitHub approval。

### 7.2 Production change 的額外門檻

- 必須由已驗證 release artifact、受控 deploy workflow 與 SSM/config change 進行；不在 EC2 直接手改 Python、unit 或 SQLite/DynamoDB。
- 先取得 flag old/new value、effective source、window、cohort、cap、operator、approval record；變更後立即從 application endpoint + host/control plane 雙側讀回。
- 先測 rollback、再做切換。任何無法在約定時間內完成的驗證，結論一律是 rollback 或 `inconclusive`，不是預設擴大。
- production only 針對已批准 canary cohort；不要為驗證而發送無界、重複或成本未封頂的 live analysis。

---

## 8. 分工、Issue 拆分與建議時程

| Issue | 最大範圍 | 依賴 | Owner / reviewer | 成功定義 |
|---|---|---|---|---|
| HERMES-P0 | 控制面與資料面稽核證據包 | AWS login / SSM access | ops + gray, harper readout | 可說明每個 flag/source/cache path 的 effective state。 |
| HERMES-I1 | scheduler + cache contract | P0 | backend + gray/harper | 核心五幣 freshness 語義正確且跨週期可觀測。 |
| HERMES-I2 | connector reliability / quarantine | I1 | ingestion owner + gray | 7-cycle gate 或誠實 quarantine。 |
| HERMES-I3 | dead-letter + delivery provenance | P0 | analysis/delivery owner + gray | 15 件分類完成、citation/provenance contract 修復。 |
| HERMES-L1 | Three-track schema, audit, pilot control | I1/I3 | backend + harper/gray | default-off、event idempotency、redaction、rollback 皆驗證。 |
| HERMES-L2 | delayed labeler + dataset manifest | L1 | data owner + harper/gray | PIT-safe labelled dataset 可重播；不自動訓練。 |
| HERMES-A1 | AGOS shadow runtime | Phase 2 | agent owner + harper/gray | immutable manifests、tool deny、無 scoring/evidence contamination。 |
| HERMES-A2 | AGOS canary + disclosure UI | A1 | agent/UI owner + gray/harper | canary/retrieval evidence、eye scan、rollback 通過。 |
| HERMES-O1 | SLO/alert/runbook / final disposition | 所有前項 | ops + CEO | on-call 能處理 failure，且對外狀態陳述真實。 |

Phase 0 的只讀稽核可立即開始。Phase 1 的 scheduler/cache、connector、delivery/dead-letter 可平行，但必須共用已固定的 config/cache contract。Three-track 與 AGOS 可在 Phase 2 後並行開發/測試，卻不得在同一個 production window 一起首次啟用；如此才能歸因與安全回滾。

---

## 9. 完成判定與未完成口徑

本計劃完成不代表 Hermes 已完全啟用。各里程碑只在其可驗證 exit criteria 達成後才可標記完成。

| 敘述 | 何時才可使用 | 不可據此延伸的宣稱 |
|---|---|---|
| 「Hermes core healthy」 | Phase 2 的 20-cycle gate、抽樣三件交付物與 SLO 皆通過 | 不能代表 AGOS 或 continuous learning 已啟用。 |
| 「Three-track pilot enabled」 | Phase 3 cohort flag、生效讀回、24h evidence、rollback proof 都具備 | 不能代表模型已訓練/校準/自我升級。 |
| 「Versioned learning dataset available」 | Phase 4 labeler/PIT/dataset manifest/evaluation 可重播 | 不能代表可以自動 promotion 或投入 production calibration。 |
| 「AGOS canary enabled」 | Phase 5 manifest/tool audit/evidence boundary/rollback/eye scan 通過 | 不能代表 memory 可當證據或 agent 可自行部署。 |
| 「production rollout complete」 | 各 phase success，完整 local release gate、review、production evidence 與 CEO 親自驗證都完成 | 不能掩蓋任何仍 quarantined source、dead letter 或 `insufficient-evidence` window。 |

---

## 10. 下一個可執行動作

1. 重新執行 `aws login`，取得短效憑證；由獲授權操作者在 SSM 執行 Phase 0 的只讀稽核，將輸出遮罩後落為 issue evidence。
2. 建立 `HERMES-P0`，先關閉「部署 unit 預設 autonomy=0」與「先前報告 autonomy=1」的控制面差異；未釐清前不碰 flag。
3. 依 dead-letter triage、cache writer/reader trace 與 connector error class，把 Phase 1 分為不超過 12 小時的 scoped issues。
4. 修復後先在 staging 做 bounded schedule/replay；只有 Phase 2 gate 通過才向 CEO 提出 Three-track 或 AGOS pilot 的各自變更申請。

---

## 11. 本文件邊界

本文件只記錄截至 2026-07-30 的分析與建議流程。它沒有修改任何 production flag、service、DynamoDB/SQLite schema、training data、IAM、timer 或 deployment；也不是已完成、已批准或可自動執行的變更報告。任何 production 切換仍須依專案 release、security/cost review、reviewer attestation 與 rollback gate 執行。
