# TrustForge Hermes Agent Delivery Backlog

> 唯一權威：本文件列出 2026-07-13 對話中已決定、但尚未完整落地的項目。
> 狀態基準：`feature/hermes-agent-observability` 工作樹。每項完成後須更新本表、
> 測試、Evidence/Execution Log（如適用）與版本紀錄；不可只在對話中宣稱完成。

## 已完成基線（不重做）

- Hermes 五節點、來源級 execution log、前端來源耗時表、240 題原創題庫與 CI
  24 題 gate。
- 五年 OHLCV lineage、來源 archive schema、每日 snapshot、T+1/T+7/T+14
  outcome diagnostic。
- bounded autonomous cycle、改善提案、skill change append-only log，以及
  release-tag CD workflow 定義。

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
| H-08 | Skill staging sandbox | **完成。** `scripts/run_skill_sandbox.py` 對候選 artifact 跑題庫，選用 replay；不會 activation | 已測試 | H-07 |
| H-09 | Rollback 實際生效 | **完成。** approved/rollback pointer 被 runtime resolver 讀取，新 run manifest 顯示選定 hash | 已測試 | H-07、H-08 |
| H-10 | 自動改善例行輸入 | **部分完成。** Hermes cycle 已自動產生 bounded 24 題 regression measurement 與各幣 replay artifact，再由 diagnostic 消費；online QA 保持 H-05 的明確 quota/credential gate | 排程定期產生 online QA 與各幣 replay reports；diagnostic 自動消費並建立 proposal queue | H-02、H-05 |
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
| H-13a | Historical Backfill Foundation | **程式契約完成，待匯入授權資料。** importer 強制 provider、`published_at`、actual `retrieved_at`、license、content hash，並標記 `backfilled_archive`。2026-07-14 已盤點本機：僅有五年 OHLCV，沒有可用 historical raw-source archive，OHLCV 不可冒充來源資料 | 每筆有 provider、published_at、retrieved_at、license/contract、content hash；拒絕時間不明資料 | 歷史來源/API |
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
| H-20 | Connector reliability policy | **來源內 cooldown 已在 production 驗證；CoinGecko provider-wide 協調已上線並通過無 429 正常路徑。** `v0.14.5` cycle 的 CoinGecko price 與 stale SOL/BNB/XRP detail 全部成功，沒有 429；provider 序列化 fetch 43.46 秒、完整 cycle 68.37 秒、五幣快照 5/5、exit 0。首個 429 後阻止其他 worker 發出第二次 HTTP 的分支不對 live API 人為觸發，由雙 worker concurrency regression 保證。仍需累積連續七輪來源失敗率 evidence | 每來源 owner、憑證、quota、retry/backoff、failure budget、fallback 記錄可查 |
| H-21 | Hermes Execution Journey implementation | **實作、正式 API 與下載 artifact 驗收完成，正式 viewport 截圖待補。** `/analyze` 已把資料驅動五節點、來源結果/耗時、run-bound Evidence/Log 下載置於結論後第一段；`v0.14.6` 正式 bundle 已確認下載名為標準 `execution-log.json`，正式分析回傳 13 筆 Evidence 與完整 execution。2026-07-14 瀏覽器控制通道初始化衝突，故不冒充已有 production desktop/mobile 截圖 | 補正式 desktop/mobile 截圖；互動/API evidence 見 `docs/qa/PRODUCTION-INTERACTION-CANARY.md`；不改 Trust Layer |

## 明確不做 / 不可越線

- 不讓跨次市場結論、事後新聞或修正資料進入正式 run。
- 不讓 Agent 自行改 production code、Trust weights、模型、prompt 或自行部署。
- 不將 offline fixture latency 稱為 online crawler SLA，也不將資訊完整度稱為預測機率。
- 不直接引入 Nous Hermes 或其他外部專案程式碼；只借鑑可驗證的架構概念。

## 執行順序

`H-01 -> H-02 -> H-05 -> H-10/H-11 -> H-17 -> H-19 -> H-20 -> H-13a -> H-13b -> H-13c -> H-14/H-16`

H-03、H-04、H-06、H-07～H-09、H-12、H-15、H-17～H-19 已完成。H-21
僅剩 production desktop/mobile 截圖 evidence。H-14/H-16
在系統穩定化、資料累積與預算核准前均保持 deferred；任何 P2 項目不得因為急於
「訓練模型」跳過資料累積與 held-out 驗證。
