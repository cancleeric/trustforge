# TrustForge Hermes Agent Delivery Backlog

> 唯一權威：本文件列出 2026-07-13 對話中已決定、但尚未完整落地的項目。
> 狀態基準：`develop@94eda81`（2026-07-16，PR #190 五年來源回填與 PR #194
> rolling-upgrade payload 相容性均已合併）。每項完成後須更新本表、
> 測試、Evidence/Execution Log（如適用）與版本紀錄；不可只在對話中宣稱完成。

## 2026-07-16 現行未完成摘要

以下只列仍需工作或外部條件的項目。沒有列出的 H-項目維持完成，不重開。

| 優先 | ID | 尚未完成 | 類型 | 下一個可驗收成果 |
|---|---|---|---|---|
| P0 | H-01 | HOYA BIT 正式 endpoint、auth 與真資料 contract 尚未取得 | 外部阻擋 | 以正式規格完成 online connector canary |
| P0 | H-02 | production archive/snapshot 與來源可靠性尚缺連續 7 天 evidence | 持續觀測 | 7 天逐輪 archive、snapshot、freshness、failure artifact |
| P0 | H-05 | 240 題 online QA 尚未在正式憑證／配額下完整執行 | 憑證／成本 gate | 保存獨立 online report、來源 p95 與失敗率 |
| P1 | H-10 | diagnostic 尚未定期取得 online QA；offline/replay/freshness 已接通 | 依賴 H-05 | scheduler 定期產生並消費 online QA artifact |
| P1 | H-20 | CoinGecko／Reddit production reliability gate 尚未達連續 7 次成功 | 外部來源觀測 | 每來源七次真實 attempt 連續成功，不用 freshness skip 灌數 |
| P1 | H-21 | Execution Journey 正式 desktop/mobile viewport evidence 尚未封存 | 可直接執行 | 正式 viewport 截圖與互動紀錄進 `docs/qa/` |
| P2 | H-13a | 五年多來源仍缺 SEC filing 全文、CoinGecko range、on-chain 歷史、新聞／Reddit archive；目前只有 Alternative.me 完整歷史與 SEC metadata-only | 開發＋外部契約 | coverage report 按來源／幣／日顯示 ready、missing、gated |
| P2 | H-13b | runner 已完成，但尚未用足量五年 raw-source archive 跑完整 daily replay | 依賴 H-13a | 每日 run 具完整五階 execution log 且無 T 後資料 |
| P2 | H-13c | outcome 程式已完成，尚待 H-13b 產生足量 eligible runs | 依賴 H-13b | T+1/T+7/T+14 lineage 與可稽核 coverage |
| Deferred | H-14/H-16 | calibrator／模型 fitting 暫緩；資料量、holdout、ModelHub ACL/API key 均未過 gate | 明確延後 | H-13 至少 100 筆 eligible outcome 後才重新評估 |

目前可直接繼續開發、不等待外部憑證的順序：`H-21 viewport evidence -> H-13a
coverage report -> SEC filing 受控下載/全文 adapter -> H-13b replay batch evidence`。
HOYA、CoinGecko plan、新聞／Reddit archive 與 ModelHub 權限不得用假資料繞過。

## 已完成基線（不重做）

- Hermes 五節點、來源級 execution log、前端來源耗時表、240 題原創題庫與 CI
  24 題 gate。
- 五年 OHLCV lineage、來源 archive schema、每日 snapshot、T+1/T+7/T+14
  outcome diagnostic。
- bounded autonomous cycle、改善提案、skill change append-only log，以及
  release-tag CD workflow 定義。
- continuous SQLite 五階流水線、題目 RAG／Hermes 對話記憶、durable
  retry/DLQ、原子快照發布與 read-only workspace。
- 31 個外框模組與獨立版本化 Trust Kernel、sandbox evidence、人工 release
  gate、active pointer／rollback；模組卡同時顯示 release version 與 content hash。
- 五年來源 capability matrix、Alternative.me 歷史 adapter、SEC quarterly
  metadata-only adapter、同日多 provider 合併，以及 rolling frontend/backend
  payload 相容性。

## P0：比賽與上線前必須完成

| ID | 待辦 | 現況 / 缺口 | 驗收條件 | 依賴 |
|---|---|---|---|---|
| H-01 | HOYA BIT 真實 connector | **程式完成，待官方 endpoint/憑證。** 新 connector 僅在 `TRUSTFORGE_HOYABIT_TICKER_URL` 設定後啟用，SSRF-safe、cache/scheduler/Execution Log/Evidence 齊備；未設定維持 disabled stub | 取得官方 contract 後以允許測資/真資料驗證 | 官方規格/憑證 |
| H-02 | 正式資料預取部署 | **已進入 production 觀測期。** `v0.14.3` 已部署；EC2 已確認 `TRUSTFORGE_HOME=/opt/trustforge`、`hermes-cycle.timer=enabled+active`。2026-07-14 手動驗證完整 cycle 在 49.83 秒以 exit 0 完成；不再有封裝後找不到 sample data 的問題。cycle 誠實回報 calibration 資料目前 60/100，未自行訓練或改動核心。仍須連續七天 archive/snapshot 與來源可靠性 evidence | 連續 7 天每輪 archive/snapshot 可查、來源失敗率與 freshness 有 evidence | AWS/runtime 觀測 |
| H-03 | AWS release CD 啟用 | **完成。** `v0.13.3` 已完成 GitHub OIDC、QA、SSM backend 與 nginx frontend 的正式 CD；`v0.13.6` 起另加 local server smoke gate | 每個 release tag 可重複通過 | GitHub/AWS 管理權限 |
| H-04 | 版本治理自動化 | **完成。** `scripts/release_version.py` 強制 tag=`pyproject`=`CHANGELOG`，拒絕 dirty release；CD gate 已接入 | release tag 驗證通過 | H-03 |
| H-05 | 線上資料 QA 基準 | 240 題全綠僅代表 offline fixture；AWS 本機登入也未恢復 | 憑證/配額確認後跑 `--online --all`；輸出與 offline 結果分開保存，來源 p95/失敗率可稽核 | H-01、AWS/provider 憑證 |
| H-06 | Wiki 正式同步 | **完成。** Wiki page `3145` 已建立並記錄同步時間 | 已驗證 | Wiki token |

## P1：讓 Hermes 真正以外框持續進步

| ID | 待辦 | 現況 / 缺口 | 驗收條件 | 依賴 |
|---|---|---|---|---|
| H-07 | Outer Skill Registry | **完成。** 五類 immutable hash artifact，正式 run 開始時凍結 revision 並寫入 Execution Log/manifest；核心 override 被拒絕 | 已測試 | H-04 |
| H-08 | Skill staging sandbox | **完成。** `scripts/run_skill_sandbox.py` 對候選 artifact 跑題庫，選用 replay，並以 `--proposal-id` 將真實 pass/fail、artifact hash 與 runner evidence 寫回 durable SQLite queue；不會自行 activation | 已測試 | H-07 |
| H-09 | Rollback 實際生效 | **完成。** 人工 decision 僅核准、不啟用；另由 authenticated activation API 將已核准 artifact 寫入 append-only active pointer，rollback 只接受先前核准 revision。runtime resolver 與新 run manifest 讀取選定 hash | 已測試 | H-07、H-08 |
| H-10 | 自動改善例行輸入 | **部分完成。** Hermes cycle 已自動產生 bounded 24 題 regression、各幣 replay、cache freshness 與 connector reliability artifact，再由 diagnostic 消費並建立 approval-gated proposal；online QA 保持 H-05 的明確 quota/credential gate | 排程定期產生 online QA 與各幣 replay reports；diagnostic 自動消費並建立 proposal queue | H-02、H-05 |
| H-11 | 來源預取並行化 | **production 路徑與 deterministic 對照完成。** `v0.14.3` 正式 cycle 使用 4 個 source-owner workers，在 25.40 秒內完成 32 個成功目標、131 筆文件，之後才寫入五幣快照 5/5；完整 cycle 49.83 秒。新增時序回歸測試證明三來源並行不會退化為序列。證據見 `docs/qa/PREFETCH-PARALLELISM.md`；因本輪已有 429，不為了 benchmark 強制重打線上來源 | 壓力測試證明快於序列且可回溯 | H-02 |
| H-12 | Cache freshness dashboard | **完成（資料 artifact）。** `scripts/cache_freshness_dashboard.py` 產出五幣×來源 fresh/stale/missing、age、document count、scheduler failure labels；Hermes cycle 自動執行 | 已測試 | H-02 |

## P2：五年歷史回填與核心研究

> 優先序調整（2026-07-14）：模型訓練（H-14、H-16）屬資料與成本成熟後的
> 後續工作，暫不排入目前系統開發主線。先完成正式資料預取、線上 QA、互動
> smoke、durable lease production 驗證與來源可靠性觀測；保留既有 gate/送件包，
> 但不啟動任何付費訓練或模型送件。

> 執行決策（2026-07-13）：不等待未來 archive 自然累積。以五年前第一個
> OHLCV 日期開始，回填每個交易日可取得的歷史資料，再依 `published_at <= T`
> 做日級 Hermes replay。回填資料必須標為 `backfilled_archive`；它不能聲稱
> 擁有當年 `fetched_at`，也不得與正式即時 archive 混用。無法取得歷史授權或
> 時間戳的來源保留為 missing，不可用今天的搜尋結果補造。

| ID | 待辦 | 目前工作 | 驗收條件 | 依賴 |
|---|---|---|---|---|
| H-13a | Historical Backfill Foundation | **來源矩陣與兩個公開歷史 adapter 完成，資料回填進行中。** importer 強制 provider、`published_at`、actual `retrieved_at`、license、content hash，並標記 `backfilled_archive`；同日多 provider 會合併而不互相覆蓋。Alternative.me 完整歷史 exporter 可執行；SEC 官方 quarterly master index adapter 已完成但明確標為 metadata-only partial coverage，全文 filing Evidence 尚待受控下載 adapter；CoinGecko range 受方案憑證限制；近期 RSS／Reddit／current-state on-chain 明確標為 archive 或歷史 endpoint 必要。既有本地資料為五年 OHLCV、3 天完整來源封存與 13 天信任快照，三者不可互相冒充 | 每筆有 provider、published_at、retrieved_at、license/contract、content hash；拒絕時間不明資料；控制台顯示各來源 ready/gated/blocked | 歷史來源/API |
| H-13b | Daily Hermes Replay | **Runner 完成，待 H-13a 資料。** `run_daily_hermes_replay.py` / `run_historical_replay_batch.py` 只讀 archive，執行 claim -> trust -> Evidence -> report，缺 snapshot 誠實記錄 | 每日 run 有完整 execution log，僅選 `published_at <= T` | H-13a |
| H-13c | Outcome Labeling | **程式完成，待 H-13b 資料。** `label_replay_outcomes.py` 對 T+1/T+7/T+14 接官方 OHLCV outcome；缺未來 bar 時誠實標為 unavailable | 每個 eligible run 可追溯 outcome window 與資料 lineage | H-13b |

| ID | 待辦 | 啟動門檻 | 驗收條件 |
|---|---|---|---|
| H-13 | Raw-source historical replay | H-13a/H-13b 完成 | 對歷史日 T 以當時 archive 實跑完整 source -> claim -> trust -> evidence workflow；拒絕任何 T 後資料 |
| H-14 | 小型 confidence calibrator | **Gate 與非連網送件包完成，待資料與 ModelHub 送件通道。** `prepare_calibrator_training.py` 固定 eligible rows hash、80/20 chronological split、feature contract、rollback 與 submission draft；僅在至少 100 筆 eligible outcome 時轉為 `ready_for_modelhub_dry_run`。候選為 `sklearn-logreg` / isotonic，不是 LLM | 比較 logistic regression/isotonic；只在 holdout 改善 calibration 時採用；不稱作 LLM 預測能力 |
| H-15 | Dawid-Skene offline fallback | **完成。** `trust.dawid_skene` 已接入離線且無 entailment 的動態來源信譽路徑；來源數不足三個、或 item 樣本不足時回退先驗 `0.5` | deterministic EM 收斂、樣本不足守門、既有 Bedrock stance 路徑不回歸；只改善統計共識，不宣稱方向預測 |
| H-16 | LLM/小模型訓練評估 | **ModelHub 登記路徑已盤點，尚不可送件。** 先累積數千筆人工檢核 trajectory 與清楚任務標註；不可因已有 ModelHub 而跳過 H-14 的可解釋小模型 | 先做 teacher/student 或 Bedrock customization feasibility study；成本、資料授權、區域、holdout safety 全部通過才訓練 |

### ModelHub 訓練登記準備（H-14 / H-16 共用）

已於 2026-07-14 透過 Headscale 讀取 `hurricanecore/modelhub` Wiki 與集團
ModelHub SOP。ModelHub 是訓練需求、Kaggle 調度、驗收與版本 registry 的正式
中心；TrustForge 未來訓練不可繞過此路徑直接把候選模型升上 production。

| 項目 | 已知做法 | TrustForge 啟動前仍需完成 |
|---|---|---|
| 送件入口 | UI `:3950/submit`；現行服務整合優先使用 `POST /api/submissions/`（X-Api-Key）；舊訓練排程文件另列 `POST /api/v1/models/{slug}/training` | 由 ModelHub `:8950/docs` / 當前原始碼確認唯一正式契約，禁止猜測或同時送兩次 |
| 資料合約 | `backfilled_archive`、replay、outcome 及特徵須保留 provider、published/retrieved time、license、content hash 與 OHLCV lineage | H-13a/H-13b 產出至少 100 筆 eligible、時間分離的 outcome；LLM 類另需數千筆人工檢核標註 |
| 候選模型 | H-14 僅允許 `sklearn-logreg` 與 isotonic；ModelHub 已支援可登記的小模型架構 | 建立可重現 dataset manifest、feature contract、chronological train/holdout split 與 baseline 指標 |
| 驗收與回退 | 只有 holdout calibration 改善的候選可進 registry；正式 run 仍由 deterministic Trust Layer 決定 | 登記 validation metrics、資料版本、模型 hash、skill revision；保留上一版 active calibrator 與 rollback pointer |
| 網路/權限 | Headscale 可讀 Wiki；本機 `apple-32` 到目前 `eric-mac` 的 ModelHub `:8950` 尚無可用 ACL，未取得 TrustForge 專用 X-Api-Key | 由 HurricaneCore 開放 `apple-32 -> ModelHub TCP 3950/8950`，核發最小 scope key 並存 Vault；完成 health、Swagger、dry-run submission 驗證 |

訓練資源優先序為 Kaggle GPU（每次不超過 9 小時）→ Lightning AI → 內網 CUDA
主機 → 本機 MPS。訓練完成後必須保留輸出、驗收結果與 registry version；不得把
未通過 holdout 的模型或資料洩漏模型登記為 active。

## 新增缺口（2026-07-13 production audit）

| ID | 待辦 | 為何必須做 | 驗收條件 |
|---|---|---|---|
| H-17 | Production interaction smoke / zero-downtime deploy | **完成。** `local_release_smoke.sh` 覆蓋 health/overview/cost pagination、BTC analyze、Evidence、五個 Hermes nodes 與 Execution Log。正式 nginx 使用 8080 primary + 8081 health-checked backup；`v0.14.8`、`v0.14.9`、`v0.14.10` 三次 CD 共保存 96 筆部署前/中/後公開 health 探測，全部 HTTP 200、curl exit 0，無中斷。證據見 `docs/qa/ZERO-DOWNTIME-DEPLOY.md` | 已驗證 |
| H-18 | 成本帳本保留、備份與匯出 | **完成。** DynamoDB PITR 已啟用；716 筆 JSONL export/hash verify/non-overwrite restore drill 已完成；AES256 + S3 versioning off-table archive evidence 已記於 `docs/qa/COST-LEDGER-DURABILITY.md` | DynamoDB PITR/backup、保留年限、CSV/JSONL export、restore drill、帳本完整性 hash 全部有 SOP/evidence |
| H-19 | Production durable lease backend | **完成。** 2026-07-14 已確認 `trustforge-analyze-leases` 為 ACTIVE/PAY_PER_REQUEST、TTL=`ttl` ENABLED；`trustforge-ec2` 最小 policy 僅含 Get/Put/Delete；真 DynamoDB backend contention/release/reacquire 全綠。`v0.14.6` 隔離的 service-level concurrent canary 兩個請求皆 HTTP 200，且共用同一 `run_id=hermes-ec4c16d8f648`，證明沒有重複分析。證據見 `docs/qa/PRODUCTION-INTERACTION-CANARY.md` | 已驗證 |
| H-20 | Connector reliability policy | **政策與自動量測完成，production gate 尚未全綠。** 來源內 cooldown 與 CoinGecko provider-wide 協調已上線；Hermes cycle 現自動從 durable scheduler runs 產出每來源 attempted/success/failed、failure rate、最新失敗與連續成功輪數，只有七次實際嘗試連續成功才過 gate，freshness skip 不灌成功；diagnostic 自動把未達標來源寫入 approval-gated proposal。2026-07-14 production 仍觀察到 CoinGecko/Reddit 429，故不可宣稱完成 | 每來源 owner、憑證、quota、retry/backoff、failure budget、fallback 記錄可查；受影響來源連續七次實際嘗試成功 |
| H-21 | Hermes Execution Journey implementation | **實作、正式 API 與下載 artifact 驗收完成，正式 viewport 截圖待補。** `/analyze` 已把資料驅動五節點、來源結果/耗時、run-bound Evidence/Log 下載置於結論後第一段；`v0.14.6` 正式 bundle 已確認下載名為標準 `execution-log.json`，正式分析回傳 13 筆 Evidence 與完整 execution。2026-07-14 瀏覽器控制通道初始化衝突，故不冒充已有 production desktop/mobile 截圖 | 補正式 desktop/mobile 截圖；互動/API evidence 見 `docs/qa/PRODUCTION-INTERACTION-CANARY.md`；不改 Trust Layer |
| H-22 | Continuous snapshot analysis matrix | **完成（2026-07-16）。** SQLite snapshot 隔離、五階重疊 worker、持久重試/DLQ、全幣×全模式×活動題目自動排程；UI 只讀原子發布快照 | daemon 重啟續跑、各階段可見 coin/mode/question/snapshot/queue/duration/retry；完整回歸通過 |
| H-23 | Question RAG and Hermes dialogue memory | **完成第一版（2026-07-16）。** SQLite 活動題目、完成結論、run/snapshot lineage 與對話成為可檢索記憶；中英 bigram/token ranking，不依賴外部 embedding；run log 標記 non-evidentiary retrieval | 左側可查看/召回相似問題；歷史結論不進 Trust Evidence；API/測試/OpenAPI 齊備 |
| H-24 | Historical outer-framework diagnostics | **完成第一版（2026-07-16）。** analysis jobs/stage duration/failure/retry/question similarity 正式接入 bounded diagnostic | 只產生 approval-gated sandbox proposal，`automatic_apply=false`，不得改核心或自行部署 |
| H-25 | Durable upgrade proposal queue | **完成（2026-07-16）。** diagnostic proposals 與 Bedrock adversarial verdict 寫入共用 SQLite；重啟後保留狀態，WebUI 顯示 durable queue。LLM 永遠 `can_activate=false` | proposal/review 可跨程序重啟查閱；diagnostic refresh 不覆蓋既有 review state |
| H-26 | Local frontend service durability | **完成（2026-07-16）。** `4174` Vite frontend 與 `8799` backend 分別由 launchd KeepAlive；API proxy 已實測 | `curl /` 與 `/api/hermes-upgrades` 回 200，程序退出後由 launchd 重啟 |
| H-27 | Upgrade sandbox / human release gate | **完成第一版（2026-07-16）。** SQLite 持久化 sandbox artifact hash、結果與人工 approve/reject 稽核；只有最新狀態為 `sandbox_passed` 才能核准，終局決策不可覆寫。管理 WebUI 沿用分頁級 Admin Token，核准永遠 `activated=false` | Admin API 與 WebUI 可查狀態、操作者及理由；公開端點無寫權；不允許自動部署 |

## 明確不做 / 不可越線

- 不讓跨次市場結論、事後新聞或修正資料進入正式 run。
- 不讓 Agent 自行改 production code、Trust weights、模型、prompt 或自行部署。
- 不將 offline fixture latency 稱為 online crawler SLA，也不將資訊完整度稱為預測機率。
- 不直接引入 Nous Hermes 或其他外部專案程式碼；只借鑑可驗證的架構概念。

## 執行順序（2026-07-16 更新）

`H-21 evidence -> H-13a coverage/full-text -> H-13b -> H-13c`

並行外部 gate：`H-01 + H-02 + H-05 -> H-10 + H-20`。
`H-14/H-16` 只有在 H-13 產生足量 leakage-safe outcome 後才解除 deferred。

H-03、H-04、H-06～H-09、H-11、H-12、H-15、H-17～H-19、H-22～H-27
已完成。H-21 僅剩 production desktop/mobile 截圖 evidence。H-14/H-16
在系統穩定化、資料累積與預算核准前均保持 deferred；任何 P2 項目不得因為急於
「訓練模型」跳過資料累積與 held-out 驗證。
