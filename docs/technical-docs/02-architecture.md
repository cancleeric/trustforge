# 02 — 系統架構總覽

[← 01 Workshop 等級導覽 ](01-workshop-overview.md)[文件首頁 ](README.md)[03 部署指南 → ](03-deployment.md)

## 02 — 系統架構總覽

TrustForge Architecture Overview · 三層管線設計、AWS 拓樸、核心元件對照表

**目錄 **

- [專案定位與設計原則 ](#overview)

- [三層管線（Layer 1–3） ](#layers)

- [AWS 部署拓樸 ](#aws-topology)

- [核心元件對照表 ](#components)

- [端到端資料流 ](#data-flow)

- [安全設計總覽 ](#security)

- [三種運作模式 ](#modes)

- [每輪分析交付物 ](#deliverables)

### 1. 專案定位與設計原則

TrustForge（對外品牌名 **Hermes **）是「加密市場分析 AI Agent」，核心差異不在「再問 AI 一次幣價」，而在 **「多源資訊進 LLM 之前，先做信任評分與溯源」 **。

**競賽硬規則： **僅限 AWS 基礎模型 → 全程走 Amazon Bedrock，不呼叫任何其他供應商 LLM。

#### 四項設計原則

| 原則 | 說明 | 實作對照 |
| --- | --- | --- |
| 信任層是核心，不是後處理 | 多源資訊在進 LLM *之前 *就先評分、加權、過濾 | `trust/scoring.py ` |
| 一切可溯源（provenance-first） | 每個結論都能追回原始來源與分數 | `agent/orchestrator.py ` |
| AI 輔助決策，不代替決策 | 輸出帶資訊完整度分級與反方證據 | `schema.py::Report ` |
| AWS Bedrock 是唯一模型入口 | 全部 LLM 呼叫集中在 `bedrock.py ` | `bedrock.py ` |

### 2. 三層管線（Layer 1–3）

Layer 1 — Ingestion（多源輸入） ↓ 統一介面 `ingestion.base.Source `，輸出標準化 `Document `Layer 2 — Trust（信任提煉 ★ 核心） ↓ 對每條 Claim 計算 `TrustScore `Layer 3 — Agent（編排 + 溯源生成） ↓ Bedrock 生成市場分析，強制引用 claim_id → 輸出帶溯源

#### Layer 1 — Ingestion（7 大來源）

| 來源 | 連接器 | 信號類型 | 實作檔案 |
| --- | --- | --- | --- |
| 價格 OHLCV | `prices ` | 客觀價格數據 | `ingestion/prices.py ` |
| 新聞 / RSS | `news ` | 敘事、事件 | `ingestion/news.py ` |
| 社群 / X / Reddit | `social ` | 情緒、熱度、喊單 | `ingestion/social.py ` |
| 鏈上數據 | `onchain ` | 大額轉帳、交易所流入流出 | `ingestion/onchain.py ` |
| HOYA BIT 行情 | `hoyabit ` | 報價、深度、成交 | `ingestion/hoyabit.py ` |
| 監管 / 公告 | `regulatory ` | 政策、合規事件 | `ingestion/regulatory.py ` |
| CoinGecko | `coingecko ` | 即時報價、情緒、開發活動 | `ingestion/coingecko.py ` |

所有連接器先經 `CachedSource `包裝（ `ingestion/cache.py `）：產品路徑永不直接打真實 API，只讀 cache；真實 API 抓取由 `scripts/fetch_scheduler.py `排程寫入 cache。

#### Layer 2 — Trust（核心）

對每條從 Document 抽出的 **Claim（主張） **計算 `TrustScore `：

```text

TrustScore = w_src · SourceReputation
           + w_corr · CrossSourceCorroboration
           + w_rec · RecencyDecay
           − w_manip · ManipulationPenalty

```

| 分量 | 說明 | 預設權重 |
| --- | --- | --- |
| SourceReputation | 來源歷史可信度（白名單/黑名單 + Dawid-Skene 動態學習） | 0.50 |
| CrossSourceCorroboration | 同一主張被幾個獨立來源佐證（去除轉發回音室） | 0.25 |
| RecencyDecay | 時效指數衰減，加密市場資訊半衰期短 | 0.15 |
| ManipulationPenalty | 拉盤喊單 / bot 轉發 / 情緒極化偵測 | 0.40 |

權重可調，預設見 `trust/scoring.py::DEFAULT_WEIGHTS `。最終對 query 相關主張做信任加權聚合，產出 `TrustedBrief `（含支撐證據與反方證據）。

#### Layer 3 — Agent（編排 + 溯源生成）

輸入 `TrustedBrief `（已加權、已附溯源），由 Bedrock agent 生成市場分析， **強制引用 **brief 中的 claim id → 輸出帶溯源。

| 步驟 | 動作 | Bedrock 呼叫 |
| --- | --- | --- |
| Step 1 | Claim 抽取（從 Document 抽出結構化主張） | 是（或 regex fallback） |
| Step 2 | 信任評分聚合（純演算法，無 Bedrock） | 否 |
| Step 3 | 帶 claim_id 溯源行文（生成分析報告） | 是 |
| Step 4 | 限制複審（可選，預算 >60s 才執行） | 是（可選） |

### 3. AWS 部署拓樸

使用者 / 評審 ↓ HTTPS :443 EC2 t3.micro（ap-southeast-2） ├─ nginx :80 → 301 redirect :443 ├─ nginx :443 → React 靜態 build（SPA） └─ nginx :443 /api/* → proxy_pass 127.0.0.1:8080 trustforge.web（Python stdlib HTTP） ↓ 僅監聽 127.0.0.1:8080 TrustForge Pipeline（4 Steps） ↓ InvokeModel Amazon Bedrock（Claude Sonnet 4.6）

#### AWS 服務對應表（Evidence Snapshot：2026-07-26 13:50 Asia/Taipei）

| 服務 | repo 佐證 | 線上佐證／目前狀態 |
| --- | --- | --- |
| EC2 + nginx | `deploy/deploy_ec2.sh `、 `deploy/nginx.conf `、 `deploy/setup_tls.sh ` | 已線上驗證 ： `/healthz `、 `/api/health `回 200；nginx header 與 CSP 可見。 |
| Amazon Bedrock | `src/trustforge/bedrock.py `是唯一模型入口； `pyproject.toml `依賴 `boto3 ` | 程式支援，線上目前關閉 ： `/api/status `顯示 `bedrock_capable=false `，不可寫成 live Bedrock 已啟用。 |
| DynamoDB cache | `ingestion/cache.py `、 `ledger.py `、 `rate_limit_store.py `、 `idempotency_lease.py ` | 已線上驗證 ： `/api/status `顯示 `DynamoDBCache connected=true `。 |
| SSM / runtime token | `src/trustforge/ssm_params.py `、 `deploy/put_runtime_tokens.sh `、 `deploy/deploy_ec2.sh ` | 設計已落地 ：公開文件不列 secret；線上 `live_token_set=true `，但不讀取 token 值。 |
| CloudWatch / alarms | `cloudwatch_metrics.py `、 `deploy/put_dedup_alarm.sh ` | repo 佐證 ：有上報與告警腳本；本次未讀取 AWS Console 狀態。 |
| Lambda Function URL | `lambda_handler.py `、 `deploy/deploy_lambda.sh ` | 備援路徑 ：文件只能寫支援／gated，不能寫成公開 production 入口。 |

### 4. 核心元件對照表

| 元件 | 職責 | 檔案 | 行數 |
| --- | --- | --- | --- |
| Web Server | 純 stdlib HTTP server，SSR + JSON API | `web.py ` | ~7,200 |
| CLI | 命令列入口（ `trustforge analyze `/ `control `） | `cli.py ` | ~400 |
| Pipeline | 共享分析流程（ `run() `/ `run_comparison() `） | `pipeline.py ` | ~300 |
| Bedrock Client | 唯一 AWS Bedrock 介面（narrative + stance + claim extract） | `bedrock.py ` | ~800 |
| Trust Scoring | 信任評分演算法（★ 核心） | `trust/scoring.py ` | ~1,800 |
| Agent Orchestrator | 4-step 編排（claim → score → narrative → limits） | `agent/orchestrator.py ` | ~1,200 |
| Budget Guard | Bedrock 日花費上限 + 每請求預算預留（TOCTOU-safe） | `budget_guard.py ` | ~400 |
| Cost Ledger | 成本帳本（JSONL / SQLite / DynamoDB backends） | `ledger.py ` | ~300 |
| Rate Limit Store | DynamoDB 跨實例速率限制計數器 | `rate_limit_store.py ` | ~200 |
| Idempotency Lease | Analyze 請求去重（防止重複 Bedrock 計費） | `idempotency_lease.py ` | ~200 |
| Admin Config | 執行期管理配置（DynamoDB，live token、bedrock 開關） | `admin_config.py ` | ~300 |
| Lambda Handler | AWS Lambda Function URL 入口 | `lambda_handler.py ` | ~150 |
| Hermes Manifest | 工具定義（14 tools）、技能契約、自主邊界 | `hermes.py ` | ~400 |

### 5. 端到端資料流

```text

使用者 Query（coin, question_type, question）
  │
  ▼
pipeline.run()
  │
  ├─ data_mode="live"（預設）
  │   │
  │   ▼
  │   ingestion.collect(query)          # List[Document]
  │   │
  │   ├─ prices.py: load_ohlcv()         # 讀 data/data/{COIN}_daily_ohlcv.csv
  │   ├─ cache.CachedSource              # 各來源讀 cache（DynamoDB/JSON）
  │   │   ├─ news, social, onchain
  │   │   ├─ regulatory, hoyabit
  │   │   └─ coingecko
  │   └─ safe_fetch.py                   # HTTPS fetch with timeout
  │
  ├─ bedrock.extract_claims_with_llm()   # List[Claim]（或 regex fallback）
  │
  ├─ trust.scoring.score()               # List[ScoredClaim] ★
  │   ├─ SourceReputation
  │   ├─ CrossSourceCorroboration
  │   ├─ RecencyDecay
  │   └─ ManipulationPenalty
  │
  ├─ trust.scoring.aggregate()           # TrustedBrief
  │
  ▼
agent.orchestrator.build_report()
  ├─ _scored_to_evidence()               # List[Evidence]
  ├─ _direction()                        # 市場方向
  ├─ _derive_limits()                    # 限制/翻轉條件
  ├─ detect_cross_source_signal()        # 分歧/共識
  ├─ trust.insights.detect_insights()    # 獨特洞察
  └─ Bedrock narrative generation        # Step 3 + Step 4
  │
  ▼
Report + List[Evidence] + ExecutionLog
  │
  ▼
輸出：report.md + evidence.json + execution_log.jsonl
     或 Web UI：HTML（SSR）或 JSON（/api/analyze）

```

### 6. 安全設計總覽

| 面向 | 實作 |
| --- | --- |
| 最小 IAM | Instance Role 僅 `bedrock:InvokeModel `（限 `anthropic.* `）+ `s3:GetObject `（限 deploy bucket）+ SSMCore；無其他 AWS 權限 |
| 無金鑰運維 | EC2 無 SSH key pair，維運走 SSM Session Manager，不開 port 22 |
| 離線預設 | 公開 EC2 預設離線示範，真實 Bedrock 呼叫需 `TRUSTFORGE_LIVE_TOKEN `HTTP header |
| Token Gate | Live 模式 token-gated（環境變數 `TRUSTFORGE_LIVE_TOKEN `），防止 Bedrock 被濫用 |
| 反作弊（模型層） | `bedrock.py `強制： `fact `型 claim 只能來自客觀來源（price/onchain/regulatory），社群/新聞主張一律降為 `inference ` |
| 版控安全 | pre-push hook：backend tests、data contracts、stub scan、question bank、frontend test/lint/build、diff check 全部通過才放行；GitHub + Gitea 雙遠端；不入版控任何 AWS credential |
| SSRF 防護 | 所有外部連結先過 `safeHref() `（僅 http/https） |
| CSP | Content-Security-Policy 限制資源來源，禁止 inline script/eval |
| Rate Limit | 依端點分三組獨立 per-IP 滑動視窗 bucket，超過回 `429 ` |

### 7. 三種運作模式

| 模式 | 觸發條件 | 資料來源 | Bedrock | 成本 |
| --- | --- | --- | --- | --- |
| **Real Data, $0（預設） ** | 未帶 `live `/ `sample `參數 | Live connectors + cache reads | OFF | $0 |
| **Offline Demo ** | `?sample=1 ` | `demo/sample_data/ `離線樣本 | Stub（不回傳真實內容） | $0 |
| **Live Bedrock ** | `?live=1 `+ `X-Live-Token `header | Live connectors + cache reads | 程式支援；本輪線上 `bedrock_capable=false ` | 啟用後依 token 計費 |

### 8. 每輪分析交付物

無論 CLI 或 Web，每輪分析固定產出三份文件：

| 檔案 | 格式 | 內容 |
| --- | --- | --- |
| `report.md ` | Markdown | 結論／市場判斷、關鍵依據（附 Evidence 連結）、信心分數、已知限制、可推翻結論的條件 |
| `evidence.json ` | JSON | 可追溯的 Evidence List，每筆含 source、fetched_at、content_reference、related_claim |
| `execution_log.jsonl ` | JSONL | 節點級執行時序（source ingestion → claim extraction → trust reasoning → evidence assembly → report delivery） |

[← 文件首頁 ](README.md)[03 部署指南 → ](03-deployment.md)
TrustForge 技術文件 · 01 系統架構總覽 · v0.18.5
