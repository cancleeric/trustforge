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
| H-02 | 正式資料預取部署 | **部分完成。** EC2 timer 已安裝；需連續觀察五幣 archive/snapshot 與 degraded connector 結果，確認不是只完成安裝 | 連續 7 天每輪 archive/snapshot 可查、來源失敗率與 freshness 有 evidence | AWS/runtime 觀測 |
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
| H-11 | 來源預取並行化 | **程式完成，待 AWS 壓測。** scheduler 有 `--parallelism`、900s total budget、source-owner worker 與 join-before-snapshot 邊界 | 壓力測試證明快於序列且可回溯 | H-02 |
| H-12 | Cache freshness dashboard | **完成（資料 artifact）。** `scripts/cache_freshness_dashboard.py` 產出五幣×來源 fresh/stale/missing、age、document count、scheduler failure labels；Hermes cycle 自動執行 | 已測試 | H-02 |

## P2：五年歷史回填與核心研究

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
| H-17 | Production interaction smoke / zero-downtime deploy | API smoke 已納入 release；仍需瀏覽器層驗證 analyze、conflict recovery、Hermes log 與成本翻頁，且 deploy restart 不可讓使用者撞到 502 | staging + production canary 截圖/API evidence；rolling 或 maintenance-safe 切換不產生公開 5xx |
| H-18 | 成本帳本保留、備份與匯出 | **完成。** DynamoDB PITR 已啟用；716 筆 JSONL export/hash verify/non-overwrite restore drill 已完成；AES256 + S3 versioning off-table archive evidence 已記於 `docs/qa/COST-LEDGER-DURABILITY.md` | DynamoDB PITR/backup、保留年限、CSV/JSONL export、restore drill、帳本完整性 hash 全部有 SOP/evidence |
| H-19 | Production durable lease backend | **程式與 bootstrap 完成，待 AWS 建表／production 驗證。** 新增 PAY_PER_REQUEST + TTL table、最小 IAM 與 service env 接線 | 建表/IAM、`TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND=dynamodb`、multi-instance contention test 全綠 |
| H-20 | Connector reliability policy | **Policy 完成，待 online observation。** `docs/qa/CONNECTOR-RELIABILITY-POLICY.md` 定義 owner、credential boundary、quota/backoff、failure budget 與 degrade response；仍需 H-02/H-05 的真實觀測 evidence | 每來源 owner、憑證、quota、retry/backoff、failure budget、fallback 記錄可查 |
| H-21 | Hermes Execution Journey implementation | **實作完成，待 release production QA。** `/analyze` 已把資料驅動五節點、來源結果/耗時、run-bound Evidence/Log 下載置於結論後第一段；無事件時有誠實 empty state | release 後以真實資料完成 desktop/mobile 截圖與互動 smoke；不改 Trust Layer |

## 明確不做 / 不可越線

- 不讓跨次市場結論、事後新聞或修正資料進入正式 run。
- 不讓 Agent 自行改 production code、Trust weights、模型、prompt 或自行部署。
- 不將 offline fixture latency 稱為 online crawler SLA，也不將資訊完整度稱為預測機率。
- 不直接引入 Nous Hermes 或其他外部專案程式碼；只借鑑可驗證的架構概念。

## 執行順序

`H-01 -> H-02 -> H-03/H-04 -> H-05/H-06 -> H-07 -> H-08 -> H-09 -> H-10/H-11/H-12 -> H-13a -> H-13b -> H-13c -> H-14/H-15 -> H-16`

H-03 與 H-04 可並行；H-05 與 H-06 可並行。任何 P2 項目不得因為急於「訓練模型」跳過資料累積與 held-out 驗證。
