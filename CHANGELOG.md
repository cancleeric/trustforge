# Changelog

## v0.14.4 — 2026-07-14

- Stop a coin-scoped connector's remaining calls for the current cycle after
  an explicit HTTP 429, while retaining every deferred stale target in the
  scheduler failure record.
- Add production evidence for the four-worker prefetch path and its first
  observed provider reliability gaps.

## v0.14.3 — 2026-07-14

- Fix the packaged Hermes scheduler service to pass `TRUSTFORGE_HOME`, so its
  bounded offline quality measurement can read the shipped sample data.

## v0.14.2 — 2026-07-14

- Start the Hermes scheduler immediately after installation and keep a bounded
  autonomous cycle running when individual source refreshes degrade.
- Make the shared DynamoDB idempotency-lease bootstrap repeatable when TTL is
  already enabled; validate the deployed service environment contract.
- Strengthen local release smoke by isolating its cache backend and executing
  a complete Hermes analysis with Evidence, five nodes, and Execution Log.

## v0.14.1 — 2026-07-14

- Add a verifiable, append-only cost-ledger archive workflow: JSONL/CSV export,
  integrity manifest, non-overwriting restore drill, DynamoDB PITR verification,
  and versioned off-table archive evidence.
- Provision the DynamoDB-backed shared analysis lease required for cross-instance
  idempotency, including TTL and least-privilege instance-role policy.
- Require provider, license, publication time, actual retrieval time, and a
  deterministic content hash for historical-backfill documents.

## v0.14.0 — 2026-07-14

- Refresh TrustForge into a compact Hermes market desk: fixed-named trust and information-completeness metrics, source-cache health, and a direct operational analysis entry point.
- Make the report's primary gauge always represent calibrated information completeness; show trust score independently so a decision state never changes the meaning of a percentage.
- Put the data-driven five-node Hermes execution journey, source outcomes, document counts, durations, and run-bound report/Evidence/Log downloads directly after the conclusion.
- Improve supporting operational pages with consistent headings, human-readable timestamps, and preserved paginated cost-ledger history.
- Archive the superseded 2026-07-01 delivery checklist and update the authoritative Hermes delivery backlog and UX release evidence.

## v0.13.9 — 2026-07-13

- Label the analysis gauge's visible primary metric so a raw trust score is not mistaken for calibrated information completeness.

## v0.13.8 — 2026-07-13

- Keep the package runtime version synchronized with release metadata so the immutable release QA contract remains valid.

## v0.13.7 — 2026-07-13

- Make the release smoke server use the repository virtual environment when available and CI's installed `python3` otherwise.
- Fail fast with the server startup log when the local release preflight cannot start, keeping a failed preflight out of production.

## v0.13.6 — 2026-07-13

- Preserve the append-only cost ledger while exposing its complete history through bounded pages instead of a 50-run-only view.
- Require a locally started TrustForge server to pass health, overview, and paged-cost smoke checks before production CD.
- Add the TrustForge-owned Hermes Execution Journey skill specification.

## v0.13.5 — 2026-07-13

- Reclaim dead local JSON analysis leases after a service restart and keep conflict responses free of internal dedup keys.

## v0.13.4 — 2026-07-13

- Render the header release from the live health contract and inject the commit SHA at frontend build time; remove the stale hard-coded version badge.

## v0.13.3 — 2026-07-13

- Treat a partially unavailable source refresh as a logged degraded Hermes node, while continuing the bounded workflow with available evidence.
- Split privileged AWS bootstrap from OIDC release CD, so a release tag deploys through the pre-provisioned production infrastructure.

## v0.13.2 — 2026-07-13

- Correct the production Hermes timer Skill Registry root to `skills/hermes`, so its baseline policy set resolves before each autonomous cycle.

## v0.13.1 — 2026-07-13

Production delivery repair for the Hermes Agent release.

- Isolate the zero-cost question-bank check from the normal backend QA budget contract.
- Package skills and executable deployment scripts, and resolve the deployed Skill Registry root from its artifact location.

## v0.13.0 — 2026-07-13

Hermes Agent 可稽核自動化與受控外框進化。

- 五節點 Execution Log、來源級耗時／結果、240 題原創驗證題庫。
- 五年 OHLCV lineage、source archive、歷史 replay 與 T+1/T+7/T+14 outcome diagnostic。
- Outer Skill Registry：source / analysis / report / evaluation / improvement 五類 hash artifact，stage / sandbox / approve / rollback。
- Hermes 預取 timer、cache freshness artifact、受 15 分鐘預算控制的來源並行化。
- Historical Backfill 基礎：以 `published_at <= T` 回填，明確標示 `backfilled_archive`，拒絕 future leakage。
- release tag 版控閘：package / changelog / tag 必須一致，dirty release 直接拒絕。

## v0.12.0 — 2026-07-12

信任引擎技術債收口 + 可觀測性 + 測試/CI 品質閘。本輪多為 follow-up issue 的深化修復。

### 信任引擎正確性
- **D1.1 聰明錢背離改讀結構化數值欄位**（#150）：不再用正則從句子抽報酬值，漏抓不再靜默回 `None`。
- **D1.4 來源自我矛盾加時間窗閘**（#149）：跨時間窗翻轉不再誣告來源自我矛盾。
- **`_directional_word_polarities` 最長優先去重**（#142）：消除子串交叉誤殺真正同向佐證。

### 可觀測性 / 安全
- **SEC EDGAR FTS 可觀測性**（#141）：失敗不再靜默吞錯，加降級旗標與計數，監管訊號流失可見。
- **請求入口解析 config 快照下傳**（#115）：消除 live/real 跨快照不一致（請求內多點重算）。
- **HOYA BIT 連接器 interface/stub 契約**（#154）：定義連接器介面與 stub 契約（真實資料接線待 7/13 工作坊 spec，追蹤於 #167）。

### 測試 / CI 品質閘
- **CI Bedrock timeout 回歸測試 + 覆蓋率下限 75%**（#91）：pytest-cov gate。
- **DynamoDB 400KB 邊界 + 生產限流常數鎖**（#110）：補齊技術債斷言覆蓋。
- **復活 deploy gate**（#118）：修 `test_deploy_ec2.sh` probe call 編號漂移（既有 9 FAIL）。

## v0.11.0 — 2026-07-11

deadline-aware pipeline + 跨快取單調性 + 洞察可解釋性 + 公網 live demo 安全基線。

### 核心 / 信任引擎
- **D2.5 deadline-aware pipeline**（#5, #78, #76）：真實 worst-case accounting + durable lease。
- **跨快取單調性通用修**（#56）：doc 層級「時光不倒流」，跨快取一致。
- **D1.3 洞察可解釋性面板**（#24）：兩貢獻來源對照 + 深層數值溯源。
- **三態誠實合約全表面鎖定**（#106）。
- **corroboration 虛抬雙修**（#15, #4）：否定詞語意偵測 + token-overlap 閘。
- **SEC EDGAR FTS 深化**（#133）：詞表擴充 + 型別防禦 + 單詞失敗隔離。
- **repo-wide canonical source identity + scoring dedup 收口**（#72）。

### 安全
- **公網 live demo 安全基線**（D0.5, #121/#75/#104）。

### 前端 / 措辭
- **移除殘留「信心」措辭**（#3）：types.ts 等，CI `noLegacyConfidenceWording` 前端測試轉綠。

## v0.10.0 — 2026-07-10

安全清理 + 跨源訊號透明度。

### 安全
- **移除 live token `?token=` query fallback**（#134, PR #136）：只認 `X-Live-Token` header；codex 對抗審抓到的 429/502 錯誤頁 retry 連結 token 反射（web.py/lambda 三處）一併修復（`_sanitized_retry_href`）；lambda header 查找改大小寫不敏感；openapi/docstring 清除過時 query token 說法。

### 跨源訊號透明度
- **單一來源主導徽章**（#21, PR #135）：`detect_cross_source_signal` 新增純展示欄位 `sentiment_source_count`（sent_sources 與 stance_pairs 來源聯集、正規化去重）；前端 `CrossSourceSignalPanel` 於 count==1 顯示「單一來源主導」徽章；validator 選填相容舊快照。同源大小寫/空白灌水去重測試 + validator 整鏈前端測試補齊（別名 canonicalization 留 #72）。

### 版控整理
- 刪除 8 支已被取代的殘枝分支（origin 6 + PR 分支 2）與 Gitea 鏡像 7 支殘枝；分支模型收斂為 develop / main / release/*。


## v0.9.0 — 2026-07-08

生產上線 + 上線後優化 + 信任品質與資料真實產出。

### 安全 / 部署
- **runtime token 讀取**（PR-A/B）：admin/live token 改 app 啟動期從 SSM Parameter Store SecureString 讀，**不落 systemd unit 檔**（消除 systemctl-show 洩漏面）；opt-in 旗標守零設定離線不變式；deploy 退場 #119 部署期 token 搬運。
- **CISO High 限流+HTTPS**（#1）：Lambda 跨實例限流從 process-dict 改 DynamoDB 共享原子計數（多實例生效、fail-closed）；live token 傳輸層強制 HTTPS（含 admin 動態設 token 的 HTTP 守門）。

### 前端部署自動化
- **CI 前端 gate**（#125）：合併前必過 `build/test/lint`；順帶修 3 個既有隱形 CI 紅（pythonpath、py3.11）。
- **CD 自動部署**（#128）：workflow_dispatch + OIDC + environment required reviewers + concurrency + **git sha 版本標記**（線上 bundle 對應哪個 commit 一 curl 可驗）。
- **前端渲染測試機制**（#126）：@testing-library/react + 邊界值測試，含雷達 regression guard。

### UI 正確性
- **信任雷達絕對刻度**（#124）：`PolarRadiusAxis domain=[0,1]`，每維度畫在真實位置、跨幣可比較。
- **% 文字 clamp 統一**（#126）：異常值不再顯示 >100%。

### 信任誠實性 / 資料
- **獨立來源去重不變量**（#106）：同源大小寫/空白灌水不再虛增獨立計數、不再讓「該棄權的判斷沒棄權」；3 處統一共用正規化函式。
- **SEC 監管 feed 真實產出**（#9）：改 EDGAR 全文檢索，加密相關命中 **0→20** 真實 filing；content_reference 帶命中詞（使用者看得出為何判定加密相關）。

_全程 GLM-5.2 產碼 + 副手雙審（CISO/QA/VP-Eng）+ CEO 親測（含 Chrome Playwright、moto 併發壓測）。_

## v0.8.0 — 2026-07-07

管理控制台（運行時設定）+ SecureString token 傳遞。開真 Bedrock 前的線上調控與部署安全前置。

### 管理控制台（新）
- **設定儲存層**（#111）：`admin_config` — DynamoDB 保留字 item 存 config，CAS 樂觀鎖、審計軌跡（token 遮罩）、15s TTL 快取 write-through。
- **admin API + 認證**（#112）：獨立 `TRUSTFORGE_ADMIN_TOKEN`（header-only、compare_digest、未設→404 fail-closed、admin≠live token 拒啟）、失敗 lockout + 全域 backstop、GET/PUT/audit 三端點。
- **三層 cap + live 閘動態化**（#114）：每日 cap `config → env → DEFAULT($3)` 三層；`HAS_BEDROCK`/`LIVE_TOKEN` 從模組級常數改每請求動態；kill-switch 完整化（env cap≤0 凌駕 config、`bedrock_enabled=false` 統一擋 online-stance、budget 負快取）。
- **React /admin 頁**（#116）：token 閘（記憶體儲存禁 localStorage）、cap/Bedrock 開關（二次確認）、live token 一次性顯示、來源徽章（config/env/default）、審計表格。
- **部署銜接**（#117）：三 env 進 systemd（`${VAR-}` fail-closed）、nginx `/api/admin/` 硬化（X-Real-IP 覆寫、no-store）、react-http 明碼模式技術封鎖 admin。

### 安全
- **SecureString token 傳遞**（#119）：admin/live token 改經 SSM Parameter Store SecureString——值不進 command history/user-data/argv，遠端 `get-parameter --with-decryption` + trap 清理（失敗可見）。IAM 窄範圍（前綴 ARN + `kms:ViaService`）。

_全程副手雙審（CISO + QA/VP-Eng）+ CEO 親測（含 Chrome Playwright）。_

## v0.7.0 — 2026-07-07

開真 Bedrock 前的護欄工程、前後端架構定案、W3 資料前置與 AI 友善介面。

### 核心 / 信任引擎
- **SSR `/analyze` 防重複計費**（#51 #87）：`/analyze`、`/analyze.json`、`/api/analyze` 三路由共用同一把 in-flight dedup key space，同參數跨路由並發只執行一次；fail-open 頻率告警（incident 週期追蹤 + monotonic clock + 冷卻防洗版，#93）。
- **stance 快取預建**（#84）：冪等去重（version+label 雙判準）、逐呼即時入帳的原子預留 budget guard。
- **跨幣信任排行 + 操縱風險徽章**（#86）：操縱風險採 worst-case 主訊號（不被平均稀釋），資料不足顯式「未評分」。
- **獨立來源去重**：跨源分歧來源按 source 去重（信任誠實性）。

### 前端 / UX
- **架構定案方案 B**（#81）：web.py 降純 `/api/*` API + React SPA 獨立部署（nginx serve 靜態 + `/api/*` 反代 127.0.0.1:8080），SSR 凍結僅供緊急回滾。
- **資訊完整度重定位**（#90 #101 #12）：全站「信心」措辭改「資訊完整度」（衡量資訊充足度非預測準確率）、首頁/內頁主角數字統一口徑、比較頁去重複巢狀。
- **信任趨勢嵌入**（#89）：分析頁嵌入信任趨勢，今日對比誠實標注日期（缺資料標「較前次快照」不假裝較昨日）。

### 資料 / 平台
- **W3 前置**（#107）：連接器擷取公開 author 累積帳號維度資料（供未來協同操縱偵測），公開端點過濾不外洩、90 天 TTL、隱私聲明。
- **AI 友善介面**（#108）：OpenAPI 3.1 spec（`GET /api/openapi.yaml`）、agent 指南（`GET /llms.txt`）、`/api/status.docs`——AI agent 可直接消費信任情報，缺鍵語意「未評估≠零」為正式契約。

### 安全
- **CISO hardening**（#2 #11）：live token 改走 `X-Live-Token` header（query 保留 deprecation 相容）、Bedrock IAM ARN region 白名單且每次部署 reconcile、`_safe_href` 控制字元防護、token 零回吐自我連結。

### 基建
- **文件重整**（#97）：docs/ 五分區 + archive 歸檔制。
- **測試加速**（#109）：conftest 全域鎖 `CACHE_BACKEND=json`，全量 pytest 15:50 → 27s。

_全程 PR gate：eye 影響面掃描 + 對抗式窮舉審查 +（安全項）CISO 雙審 + CEO 親測。_
