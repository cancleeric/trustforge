# TrustForge Hermes Agent Delivery Backlog

> 唯一權威：本文件列出 2026-07-13 對話中已決定、但尚未完整落地的項目。
> 狀態基準：TrustForge v0.16.0 release candidate。每項完成後須更新本表、
> 測試、Evidence/Execution Log（如適用）與版本紀錄；不可只在對話中宣稱完成。

## 2026-07-17 現行未完成摘要與開發駐列

### 2026-07-17 夜間 CEO issue / PR sweep

本輪優先處理 PR / issue，而不是另開新主線。權威執行計劃見
`docs/plans/CEO-ISSUE-PR-DEVELOPMENT-SWEEP-2026-07-17.md`。

- PR #218 是 v0.16.2 生產 bugfix release blocker；CI 已綠、merge state
  `CLEAN`，但 reviewer approval、eye scan、`/codex-review` 與安全/成本雙審仍是
  merge gate。
- #207、#209、#215 在 `develop` 已有對應 commits 與測試，但 GitHub issue 仍 open；
  下一步先補 evidence / closure，而不是重做已完成工作。
- 每小時 CEO sweep 只產生 triage 與派工建議；互動式 CEO 審查後才可派 owner、
  準備 code work 或補 evidence，且不得自動 merge 或 deploy。

### 2026-07-17 新 Data Engineering issues

| Issue | 狀態 | 已落地／剩餘 |
|---|---|---|
| #208 | 完成 | #209～#215 已逐項落地；後續儲存層升級由 #214 的實際量測門檻觸發 |
| #209 | 完成 | Document/Evidence/Report v1、JSON Schema、契約 artifact、CI compatibility gate（`619f2ba`） |
| #210 | 完成 | source volume/freshness/duplicate/p50/p95/failure/SLO observability（`65ef349`） |
| #211 | 完成 | immutable snapshot→job→stage→result lineage；並修正重複收養造成的雙重分析（`facae3a`） |
| #212 | 完成 | append-only point-in-time Trust Feature Store，正式結果原子 materialize 五項信任特徵（`1f1749a`） |
| #213 | 完成 | schema/null/time/duplicate 品質閘、不可變 quarantine、全壞批次不覆蓋 cache（`1570f2f`） |
| #214 | 路線完成／依門檻啟動 | SQLite→sealed Parquet/DuckDB→S3 Iceberg 的量測門檻、核對與安全邊界已定義 |
| #215 | 完成 | 每次真 fetch 先 append-only `source_events`，成功後才更新 latest cache（`718faf5`） |
| #207 | 完成（desktop 核心） | 模組開啟時卸載隱藏 galaxy、停止背景動畫、降低重複 polling、URL workspace 同步、幣種／右欄原子快照切換（`7e4b949`）；mobile 仍依優先序最後驗收 |

本輪資料主線已從「latest cache」改成可稽核 Bronze → quality → snapshot → lineage →
point-in-time feature 的完整鏈；desktop overlay/display regression 也已銷帳。下一個
本機核心工作回到 H-13a：擴充五年異質來源與 actual coverage；外框 diagnostic
接收上述真實品質與可靠性指標。

使用者確認的優先順序是：**核心功能 → 可自動改善的外框擴充 → 五年歷史資料 →
手機版驗收**。手機版不得卡在核心與資料工作中間；除非出現阻斷使用的 mobile
regression，H-21 viewport evidence 排在本輪最後。

以下只列仍需工作或外部條件的項目。沒有列出的 H-項目維持完成，不重開。

| 優先 | ID | 尚未完成 | 類型 | 下一個可驗收成果 |
|---|---|---|---|---|
| P0 | H-01 | HOYA BIT 正式 endpoint、auth 與真資料 contract 尚未取得 | 外部阻擋 | 以正式規格完成 online connector canary |
| P0 | H-02 | production archive/snapshot 與來源可靠性尚缺連續 7 天 evidence | 持續觀測 | 7 天逐輪 archive、snapshot、freshness、failure artifact |
| P0 | H-05 | 240 題 online QA 尚未在正式憑證／配額下完整執行 | 憑證／成本 gate | 保存獨立 online report、來源 p95 與失敗率 |
| P1 | H-10 | diagnostic 尚未定期取得 online QA；offline/replay/freshness 已接通 | 依賴 H-05 | scheduler 定期產生並消費 online QA artifact |
| P1 | H-20 | CoinGecko／Reddit production reliability gate 尚未達連續 7 次成功 | 外部來源觀測 | 每來源七次真實 attempt 連續成功，不用 freshness skip 灌數 |
| P1 | H-29 | CloudWatch dedup/recent_failures 與 admin cutover 網路／告警證據尚未完整銷帳（#104、#113） | production ops gate | 正式 alarm、X-Real-IP、admin 告警規則與演練 evidence |
| P0 | H-13a | Alternative.me 已匯入 1,826 天 × 5 幣；Blockchain.com 三條官方 BTC 鏈上指標已匯入 1,823 天／5,469 documents。仍缺 SEC filing 全文、其他幣鏈上、CoinGecko range、新聞／Reddit archive | 核心資料＋外部契約 | coverage report 按來源／幣／日量測 actual/missing/gated；逐來源補齊 |
| P2 | H-13b | 新鏈上資料加入後 BTC 已重跑 1,827 日、零 skipped；五幣合計 9,131 runs。其他幣多來源重跑仍依賴 H-13a | 部分完成／依賴 H-13a | 新來源加入後重跑；每 run 維持完整 execution log 且無 T 後資料 |
| P2 | H-13c | 9,131 replay 已完成 T+1/T+7/T+14 labeling；eligible 仍為 0，pipeline 沒把鏈上觀察或情緒值假造為價格方向 | 資料 gate | 取得有正式 directional contract 的異質來源後產生可稽核 outcomes；禁止假造方向 |
| Deferred | H-14/H-16 | calibrator／模型 fitting 暫緩；資料量、holdout、ModelHub ACL/API key 均未過 gate | 明確延後 | H-13 至少 100 筆 eligible outcome 後才重新評估 |

目前可直接繼續開發、不等待外部憑證的順序：`H-31/H-32 runtime core -> H-13a
actual coverage -> H-24/H-25 outer diagnostics consume coverage/replay -> H-13b replay
batch evidence`。H-21 已依要求在上述核心工作後完成，不再列入未完成駐列。
HOYA、CoinGecko plan、新聞／Reddit archive 與 ModelHub 權限不得用假資料繞過。

### 本機可執行工作完成後的剩餘數量

仍開放 **11 個 H-ID**：

- 外部契約／正式環境／連續觀測 6 個：H-01、H-02、H-05、H-10、H-20、H-29。
- 多來源歷史資料鏈 3 個：H-13a、H-13b、H-13c；Alternative.me 加上 BTC
  Blockchain.com 鏈上 archive 已完成 9,131 replay，但其他幣異質來源與 eligible
  directional outcomes 尚未取得。
- 明確 deferred 2 個：H-14、H-16；依使用者決策暫不做 fitting，且資料／ModelHub
  gate 尚未通過。

H-31/H-32 已由本機 merge commit `5664bf7` 合併進 `develop`，不再是本機整合
缺口；遠端 push／PR 與 GitHub issue 最新留言仍需 authenticated
private-repository access。以上外部項目不能由本機程式碼或假資料宣告完成。

### Issue 收件匣與問題回覆駐列

1. `#198`：以 actual coverage report、Alternative.me 與 Blockchain.com BTC
   鏈上匯入證據更新 H-13a；`#197` 仍受 eligible directional outcome gate 阻擋。
2. `#191/#192/#193/#200`：PR #201 的 commit `601d5fa` 已在本地 `develop`；程式
   gate 已完成。取得 authenticated issue access 後附上 commit/test evidence、回答
   新留言並核對逐票 closure，不因程式已合併就猜測 GitHub 狀態。
3. `#104/#113`：保留 production ops 驗收，不以本機結果關票。
4. 新留言、新問題與 owner 回覆：目前工作機沒有 `gh`，GitHub 私有倉庫的匿名 API
   也不可讀；列為 **等待 authenticated issue read access**。取得權限後先同步問題、
   回覆與最後更新時間，再排入上述 canonical issue，不憑舊文件猜測。

## GitHub open issue 對照（2026-07-16）

本節是 GitHub issue 與本文件的唯一對照。2026-07-16 核對共有 **17 個 open
issues**；issue 不可只因列入本表就關閉，必須有合併 commit、測試或外部驗收
evidence。重複票須先留言指向 canonical issue，才可關閉。

| Issues | 對應工作 | 判定 | 下一步／關閉條件 |
|---|---|---|---|
| #200 | H-30；同時作 #191～#199 umbrella | 程式已合併，issue 狀態待同步 | `develop@601d5fa` 已含 stub scan、allowlist 與 CI artifact；authenticated issue sync 後核對 child issues 才關 |
| #167、#199、#154 | H-01 HOYA BIT | #167 保存正式接線驗收；#199 保存技術缺口；#154 為舊 spec-blocked 重複候選 | 取得官方 endpoint/auth/schema 後完成 online canary；先在 #154 留 canonical 連結再關閉重複票 |
| #198 | H-13a | 開發中 | 連接器保存 point-in-time 異質歷史序列，coverage report 可顯示 ready/missing/gated |
| #197 | H-13b、H-14 | 被 #198 與 eligible outcomes 阻擋 | production wiring 前先完成 leakage-safe replay 與 holdout discrimination；無判別力不得啟用 |
| #196 | H-14 | 明確 deferred | 至少 100 筆 eligible outcomes 後比較 logistic/isotonic/conformal，holdout 改善才採用 |
| #195 | H-13c、H-14 | **需修正 issue 敘述，不照原題實作** | 外部 outcome 可校準來源可靠性／abstain；Trust 分數仍是證據可靠性與資訊完整度，禁止宣稱價格方向預測機率 |
| #191 | H-28 | 程式已合併，issue 狀態待同步 | `develop@601d5fa` 已含 source tier、非 Evidence UI、文字限制與 payload tests；登入後附證據／核對留言再關 |
| #192 | H-28 | 回歸測試已合併，issue 狀態待同步 | `develop@601d5fa` 固定 TLS hostname/certificate regression；登入後附證據／核對留言再關 |
| #193 | H-28 | 程式已合併，issue 狀態待同步 | `develop@601d5fa` 已含 JSON data boundary、軟遮蔽與 execution log；登入後附證據／核對留言再關 |
| #8、#153 | H-20 Reddit OAuth | #8 為 canonical 外部阻擋；#153 為重複候選 | 取得 production OAuth/IP 配額並連續 7 次真實成功；先在 #153 留 canonical 連結再關閉重複票 |
| #104、#113 | H-29 | production ops 驗收 | CloudWatch dedup/recent_failures alarm、nginx `X-Real-IP` 與 admin 告警演練全部有正式 evidence |
| #169 | 產品／比賽決策 | 需 Eric 拍板，不是程式缺陷 | 記錄是否投入 AWS Kiro bonus 與理由；決策完成即關閉 |
| #170 | 外部規則確認 | 需 Mars Li／owner 回覆，不是程式缺陷 | 留存 AWS model 限制的權威答覆與日期；確認後關閉 |

### Issue 執行批次

1. **安全批次：** #191、#192、#193 → H-28；完成測試後逐票附 PR/evidence。
2. **歷史資料批次：** #198 → H-13a，再解除 #197；#196、#195 保持資料 gate。
3. **外部 connector 批次：** #167/#199 與 #8；不得用 fixture 冒充 online canary。
4. **營運批次：** #104、#113 → H-29；只接受 production evidence。
5. **治理批次：** #200 → H-30；#154、#153 依 canonical 流程整理重複票。
6. **人員決策：** #169、#170 交由 owner 留下可稽核結論，不混入程式完成率。

## 已完成基線（不重做）

- Hermes 五節點、來源級 execution log、前端來源耗時表、240 題原創題庫與 CI
  24 題 gate。
- 五年 OHLCV lineage、來源 archive schema、每日 snapshot、T+1/T+7/T+14
  outcome diagnostic。
- bounded autonomous cycle、改善提案、skill change append-only log，以及
  release-tag CD workflow 定義。
- continuous SQLite 五階流水線、題目 RAG／Hermes 對話記憶、durable
  retry/DLQ、原子快照發布與 read-only workspace。
- H-28 retrieval/prompt/TLS 邊界與 H-30 stub/空函式 CI gate（`develop@601d5fa`）；
  GitHub issue closure 仍須 authenticated sync，不影響程式完成判定。
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
| H-13a | Historical Backfill Foundation | **第二個真實來源已落地。** Alternative.me 產出 9,130 rows（1,826 日 × 5 幣）。Blockchain.com 官方 Charts API 另產出 BTC 交易數、算力、難度共 5,469 rows／1,823 天；缺日為 2025-11-13～15 與尚未日結的 2026-07-17，均原樣保留。兩源日期聯集使 BTC snapshot coverage 達 1,827/1,827；其他四幣仍為 1,826/1,827。`report_historical_coverage.py` 逐幣、逐來源、逐日量測，且外框診斷只對 capability 聲明支援的幣種報 gap。SEC adapter 仍是 metadata-only；其他幣鏈上、CoinGecko、新聞／Reddit、HOYA 仍受契約或歷史 endpoint gate | 每筆有 provider、published_at、retrieved_at、license/contract、content hash；actual coverage 明列缺日；逐來源補齊且禁止用能力標籤冒充資料 | 歷史來源/API |
| H-13b | Daily Hermes Replay | **新增來源後已全量重跑 BTC。** BTC 1,827 日零 skipped，其他四幣各 1,826 日；五幣合計 9,131 runs，audit `complete=true`、`invalid=0`。live/backfill key 分離，runner 只讀 `backfilled_archive` | 新來源加入後同樣全量重跑；每日 run 有完整 execution log，僅選 `published_at <= T` | H-13a |
| H-13c | Outcome Labeling | **9,131 replay 的 T+1/T+7/T+14 lineage 已完成。** 新增鏈上觀察後仍沒有符合正式 directional contract 的輸出，因此 eligible=0 並維持 unavailable；沒有把資訊完整度、情緒值或鏈上數值冒充方向 | 有正式方向語意的異質來源加入後產生可稽核 directional outcomes；不得把資訊完整度或原始指標冒充方向 | H-13b |

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
| H-21 | Hermes Execution Journey implementation | **完成（2026-07-17）。** `/analyze` 的五節點、來源耗時、run-bound Evidence/Log 與正式下載已驗收；另以一般 Google Chrome CDP 封存 1600×1200 desktop 與 390×844 mobile 歷史頁證據。Mobile 隱藏次要 rail，核心內容與五節點全在 viewport 內 | 證據見 `docs/qa/HERMES-HISTORY-VIEWPORT-EVIDENCE-2026-07-17.md` 與 `docs/qa/PRODUCTION-INTERACTION-CANARY.md`；不改 Trust Layer |
| H-22 | Continuous snapshot analysis matrix | **完成（2026-07-16）。** SQLite snapshot 隔離、五階重疊 worker、持久重試/DLQ、全幣×全模式×活動題目自動排程；UI 只讀原子發布快照 | daemon 重啟續跑、各階段可見 coin/mode/question/snapshot/queue/duration/retry；完整回歸通過 |
| H-23 | Question RAG and Hermes dialogue memory | **完成第一版（2026-07-16）。** SQLite 活動題目、完成結論、run/snapshot lineage 與對話成為可檢索記憶；中英 bigram/token ranking，不依賴外部 embedding；run log 標記 non-evidentiary retrieval | 左側可查看/召回相似問題；歷史結論不進 Trust Evidence；API/測試/OpenAPI 齊備 |
| H-24 | Historical outer-framework diagnostics | **完成第二版（2026-07-17）。** analysis jobs/stage duration/failure/retry/question similarity 加上 SQLite actual historical coverage；Hermes 已從真實缺日、SEC 0 coverage 與 gated sources 產生 `historical-archive-coverage` 候選 | 只產生 approval-gated sandbox proposal，`automatic_apply=false`，不得改核心或自行部署 |
| H-25 | Durable upgrade proposal queue | **完成（2026-07-16）。** diagnostic proposals 與 Bedrock adversarial verdict 寫入共用 SQLite；重啟後保留狀態，WebUI 顯示 durable queue。LLM 永遠 `can_activate=false` | proposal/review 可跨程序重啟查閱；diagnostic refresh 不覆蓋既有 review state |
| H-26 | Local frontend service durability | **完成（2026-07-16）。** `4174` Vite frontend 與 `8799` backend 分別由 launchd KeepAlive；API proxy 已實測 | `curl /` 與 `/api/hermes-upgrades` 回 200，程序退出後由 launchd 重啟 |
| H-27 | Upgrade sandbox / human release gate | **完成第一版（2026-07-16）。** SQLite 持久化 sandbox artifact hash、結果與人工 approve/reject 稽核；只有最新狀態為 `sandbox_passed` 才能核准，終局決策不可覆寫。管理 WebUI 沿用分頁級 Admin Token，核准永遠 `activated=false` | Admin API 與 WebUI 可查狀態、操作者及理由；公開端點無寫權；不允許自動部署 |
| H-31 | Continuous pipeline runtime self-healing | **完成並已合併本機 develop（2026-07-17）。** worker watchdog 會以 immutable snapshot 重建遺失工作；local launchd 每階段 4 workers。H-31/H-32 commits 已由 merge `5664bf7` 進入 `develop`，合併後關鍵回歸 108 passed | 遠端 push／PR 待 authenticated repository access；本機程式與整合已銷帳 |

## 明確不做 / 不可越線

- 不讓跨次市場結論、事後新聞或修正資料進入正式 run。
- 不讓 Agent 自行改 production code、Trust weights、模型、prompt 或自行部署。
- 不將 offline fixture latency 稱為 online crawler SLA，也不將資訊完整度稱為預測機率。
- 不直接引入 Nous Hermes 或其他外部專案程式碼；只借鑑可驗證的架構概念。

## 執行順序（2026-07-17 更新）

`H-31/H-32 core -> H-13a actual archive/coverage -> H-24/H-25 outer improvement inputs -> H-13b -> H-13c`

並行 production／外部 gate：`H-29` 與 `H-01 + H-02 + H-05 -> H-10 + H-20`。
`H-14/H-16` 只有在 H-13 產生足量 leakage-safe outcome 後才解除 deferred。

H-03、H-04、H-06～H-09、H-11、H-12、H-15、H-17～H-21、H-22～H-28、H-30
已完成。H-21 僅剩 production desktop/mobile 截圖 evidence。H-14/H-16
在系統穩定化、資料累積與預算核准前均保持 deferred；任何 P2 項目不得因為急於
「訓練模型」跳過資料累積與 held-out 驗證。
# 2026-07-17 本機 API 資源雪崩

`H-32`：**修復完成並已合併本機 develop（merge `5664bf7`），遠端 PR 待認證。** 一般 Chrome 證實主因是無界 Web request
threads、逐請求 SQLite 連線/schema 初始化與 journey N+1 查詢，輪詢放大後
形成 5 GB 行程雪崩；另補嚴格 local CORS allowlist。已加入 32-request 上限、
共用 SQLite backend、read-only projection、journey 4-query bulk read，並以
七端點並發、15-request 短壓及 BTC/ETH 一般 Chrome 截圖驗收。正式診斷見
[`docs/qa/HERMES-LOCAL-API-FAILED-FETCH-ANALYSIS-2026-07-17.md`](../qa/HERMES-LOCAL-API-FAILED-FETCH-ANALYSIS-2026-07-17.md)。
