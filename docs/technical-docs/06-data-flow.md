# 06 — 資料流與連接器

[← 05 API 參考 ](05-api.md)[文件首頁 ](README.md)[07 運維手冊 → ](07-operations.md)

## 06 — 資料流與連接器

Data Flow & Connectors · 從 7 大來源到最終報告的完整旅程

**目錄 **

- [資料流全景 ](#flow-overview)

- [7 大來源連接器 ](#connectors)

- [Cache 層設計 ](#cache)

- [Document 資料結構 ](#document)

- [Claim 抽取流程 ](#claim)

- [Evidence 溯源鏈 ](#evidence)

- [Report 生成 ](#report)

- [Analysis Lineage（可稽核性） ](#lineage)

- [儲存層架構 ](#storage)

### 1. 資料流全景

┌──────────────────────────────────────────────┐ │ 資料流全景：5 階段端到端 │ └──────────────────────────────────────────────┘ 使用者 Query │ coin=BTC, type=multi_source, q="現在適合進場嗎？" ▼ ┌─────────────────────────────────────────────────────────────────┐ │ Phase 1: Source Ingestion（多源收集） │ │ │ │ prices ───→ load_ohlcv() ───→ data/data/{COIN}_daily_ohlcv.csv │ │ news ─────→ CachedSource ──→ DynamoDB / JSON cache │ │ social ───→ CachedSource ──→ DynamoDB / JSON cache │ │ onchain ──→ CachedSource ──→ DynamoDB / JSON cache │ │ regulatory→ CachedSource ──→ DynamoDB / JSON cache │ │ coingecko → CachedSource ──→ DynamoDB / JSON cache │ │ hoyabit ──→ CachedSource ──→ DynamoDB / JSON cache │ │ │ │ 輸出: List[Document] │ └──────────────────────┬──────────────────────────────────────────┘ ▼ ┌─────────────────────────────────────────────────────────────────┐ │ Phase 2: Claim Extraction（主張抽取）★ 首次 Bedrock 呼叫 │ │ │ │ bedrock.extract_claims_with_llm() ─→ List[Claim] │ │ 若 Bedrock 不可用 → regex fallback │ └──────────────────────┬──────────────────────────────────────────┘ ▼ ┌─────────────────────────────────────────────────────────────────┐ │ Phase 3: Trust Scoring（信任評分）★ 純演算法，無 Bedrock │ │ │ │ trust.scoring.score() ─→ List[ScoredClaim] │ │ ├─ SourceReputation: 來源信譽 × 0.50 │ │ ├─ CrossSourceCorroboration: 多源佐證 × 0.25 │ │ ├─ RecencyDecay: 時效衰減 × 0.15 │ │ └─ ManipulationPenalty: 操縱偵測 − 0.40 │ │ │ │ trust.scoring.aggregate() ─→ TrustedBrief │ │ ├─ 支撐證據 (supporting claims) │ │ └─ 反方證據 (contrarian claims) │ └──────────────────────┬──────────────────────────────────────────┘ ▼ ┌─────────────────────────────────────────────────────────────────┐ │ Phase 4: Narrative Generation（行文生成）★ 第二次 Bedrock 呼叫 │ │ │ │ agent.orchestrator.build_report() │ │ ├─ _scored_to_evidence() ─→ List[Evidence] │ │ ├─ _direction() ─→ 市場方向 │ │ ├─ _derive_limits() ─→ 限制條件 │ │ ├─ detect_cross_source_signal() ─→ 來源分歧/共識 │ │ ├─ trust.insights.detect_insights() ─→ 獨特洞察 │ │ └─ Bedrock narrative ─→ market_judgment（含 claim_id 溯源） │ └──────────────────────┬──────────────────────────────────────────┘ ▼ ┌─────────────────────────────────────────────────────────────────┐ │ Phase 5: Delivery（交付） │ │ │ │ 輸出: Report + List[Evidence] + ExecutionLog │ │ 格式: report.md + evidence.json + execution_log.jsonl │ │ 或 Web UI: HTML (SSR) / JSON (/api/analyze) │ └─────────────────────────────────────────────────────────────────┘

### 2. 目前來源連接器（develop snapshot）

| 來源群 | 模組 | 輸入 | 輸出 | Cache / 安全邊界 |
| --- | --- | --- | --- | --- |
| **價格 OHLCV** | `ingestion/prices.py` | HOYA BIT 官方 5 年日線 | 價格事實（Document 含 SHA-256 lineage） | 本地檔案；不經 cache |
| **新聞 / Crypto media** | `ingestion/news.py` | Cointelegraph / CoinDesk / The Block 等 RSS | 標題、摘要、發布時間、來源 URL | DynamoDB / JSON cache |
| **社群** | `ingestion/social.py` | Reddit RSS, X/Twitter | 情緒、熱度、喊單、討論量 | DynamoDB / JSON cache；主張多降為 inference |
| **鏈上基礎** | `ingestion/onchain.py` | Blockchain.com / Fear & Greed 等 | 鏈上統計、恐懼貪婪、交易所流入/流出 | SSRF-safe fetch + cache |
| **Whale trades** | `ingestion/whale_trades.py` | Whale Alert + Arkham transfers | 大額轉帳、已標記錢包 / chain transfer 訊號 | key-based；不把 key 寫入 URL/meta/log |
| **Etherscan** | `ingestion/etherscan.py` | Etherscan V2 txlist | ETH 鯨魚交易；目前方向誠實中性，待 address→exchange mapping | key-based query；例外訊息 sanitized |
| **CoinGecko** | `ingestion/coingecko.py` | CoinGecko API | 即時報價、社群情緒、開發活動 | keyless / public；cache |
| **CoinMarketCap** | `ingestion/cmc.py` | CMC quotes/latest | 第三條 price_live 交叉佐證來源 | key 走 `X-CMC_PRO_API_KEY` header，不進 URL |
| **DefiLlama** | `ingestion/defillama.py` | prices/current、`/v2/chains` | price_live + DeFi TVL 客觀訊號 | keyless；coin/path 白名單 |
| **台灣監管 / 公開揭露** | `ingestion/taiwan_regulatory.py` | FSC、MOPS、TWSE、TPEx | VASP / 虛擬資產公告、上市櫃揭露 | host 白名單、PIT visible_at、截斷 sentinel、fail-closed |
| **國際監管** | `ingestion/regulatory.py` | SEC EDGAR / 政府公告 | 政策、合規事件 | DynamoDB / JSON cache |
| **HOYA BIT** | `ingestion/hoyabit.py` | HOYA BIT exchange API / 官方資料 | 報價、深度、成交 | DynamoDB / JSON cache |

#### 2.1 已接來源數量與誠實邊界

| 類別 | 已接 | 尚未標成已接 |
| --- | --- | --- |
| 台灣監管 / 公開揭露 | FSC、MOPS、TWSE、TPEx（4 個） | BlockTempo 等台灣在地媒體 RSS |
| 外部資料來源主線 | Whale trades（Whale Alert + Arkham）、Etherscan、CoinMarketCap、DefiLlama（4 條主線） | Etherscan 方向分類仍需 address→exchange mapping；key 未配置時只降級不造假 |
| 既有核心來源 | HOYA BIT OHLCV、CoinGecko、SEC EDGAR、Blockchain.com、news feeds | live enabled 需以 runtime status / cache / credential 驗證 |

### 3. Cache 層設計

**核心原則：產品路徑永不直接打真實 API。 **所有即時資料來源都經 `CachedSource `包裝（ `ingestion/cache.py `），只讀 cache。真實 API 抓取由 `scripts/fetch_scheduler.py `排程寫入。

#### 3.1 Cache 後端架構

| 後端 | 類別 | 適用場景 | 多實例安全 |
| --- | --- | --- | --- |
| `DynamoDBCache ` | `DynamoDBCache ` | Production（多 EC2 / Lambda 共享） | 是 |
| `JsonCacheBackend ` | `JsonCacheBackend ` | 本地開發、測試、單機部署 | 否 |
| `SqliteCacheBackend ` | `SqliteCacheBackend ` | 單機開發（跨啟動持久） | 否 |

#### 3.2 Source Archive（歸檔層）

`source_archive.py `提供不可變的來源事件歸檔（SQLite），供歷史 replay 使用：

- 每筆記錄有 **三個時間值 **： `published_at `（文檔發布時間／或明確 unknown）、 `fetched_at `（cache 抓取時間）、 `snapshot_at `（歸檔時間）

- Formal replay 只能選取 `snapshot_at `≤ `run_started_at `的歸檔——同日稍晚拍的 snapshot 會被拒絕

- 缺失的歷史歸檔不會被「用當前 cache 重建」——保持誠實的資訊時間邊界

### 4. Document 資料結構

```text

**Document**（schema_version: 1.0.0）
  ├─ source      : str         # 來源名稱（e.g. "BTC price OHLCV"）
  ├─ kind        : str         # 來源類別（price/news/social/onchain/regulatory/hoyabit/coingecko）
  ├─ content     : str         # 原始內容
  ├─ corpus      : str         # 清洗後內容（用於 claim 抽取）
  ├─ published_at: datetime    # 文檔發布時間
  ├─ fetched_at  : datetime    # cache 抓取時間
  ├─ meta        : dict        # 來源特異欄位（如 author、tag、url）
  ├─ timestamp   : datetime    # Document 建構時間
  └─ schema_version: str      # 契約版本

```

### 5. Claim 抽取流程

```text

**Claim**
  ├─ claim_id    : str         # 唯一識別碼（全管線不變）
  ├─ text        : str         # 主張內容
  ├─ kind        : str         # fact | inference | opinion
  │                              fact: 只能來自 price/onchain/regulatory
  │                              inference: 社群/新聞推論
  │                              opinion: 主觀判斷
  ├─ direction   : str         # bullish | bearish | neutral
  ├─ confidence  : float       # 來源信心（由 stance classifier 產出）
  └─ source_doc  : Document    # 來源文檔引用

```

**Fact 型強制規則 **： `bedrock.py `強制 `fact `型 claim 只能來自客觀來源（price/onchain/regulatory）。社群/新聞主張一律降為 `inference `，不得宣稱是事實。這是競賽反作弊的核心機制。

### 6. Evidence 溯源鏈

```text

**Evidence**（schema_version: 1.0.0）
  ├─ source         : str      # 來源名稱
  ├─ fetched_at     : datetime # 資料抓取時間
  ├─ content_reference: str    # 引用內容片段
  ├─ claim_id       : str      # 對應的 Claim ID（全管線溯源）
  ├─ trust_score    : float    # 信任分數
  ├─ trust_components: {       # 信任分數分量分解
  │    reputation     : float
  │    corroboration  : float
  │    recency        : float
  │    manipulation   : float
  │  }
  ├─ stance         : str      # bullish | bearish | neutral
  ├─ author         : str|null # 來源使用者名稱（內部用，不對外暴露）
  └─ decision_score : float    # 比較模式決策分

```

**隱私邊界： **`author `僅存於內部 cache/快照，供未來 detect 用。 `/api/analyze `、 `/api/overview `、 `/api/history `等公開端點在序列化邊界過濾掉此欄位。

### 7. Report 生成

```text

**Report**（schema_version: 1.0.0）
  ├─ coin             : str    # 幣種
  ├─ coin_cn          : str    # 幣種中文名
  ├─ question         : str    # 原始問題
  ├─ question_type    : str    # multi_source | hypothesis | comparison
  ├─ direction        : str    # 偏多 | 偏空 | 中性 | 不明
  ├─ decision_state   : str    # abstain | low_confidence | normal
  ├─ calibrated_confidence: float  # 校準信心
  ├─ market_judgment  : str    # 市場判斷（Bedrock 生成，含 claim_id）
  ├─ key_basis        : list   # 關鍵支撐證據
  ├─ limits           : list   # 限制條件／可推翻結論的因素
  ├─ trust_radar      : dict   # 多維度信任雷達（按 kind 分）
  ├─ trust_components_aggregate: dict  # 信任分量聚合
  ├─ insight_labels   : list   # 獨特洞察標籤
  ├─ cross_source_signal: dict # 跨源分歧/共識
  ├─ snapshots        : list   # 歷史快照（比較模式用）
  └─ schema_version   : str    # 契約版本

```

### 8. Analysis Lineage（可稽核性）

每份結果都能由 `analysis_lineage_events `反向追溯到固定 snapshot 與完整五階段執行：

```text
snapshot_created → job_enqueued → stage_started/stage_completed → result_published
```

- 每個事件保存 `snapshot_id `、 `job_id `、stage、entity/parent 關係、時間與非敏感執行 metadata

- SQLite trigger 禁止 UPDATE/DELETE——重試與失敗追加新 event，不覆蓋先前歷史

- `AnalysisFlow.lineage(job_id=...) `提供稽核查詢

### 9. 儲存層架構

詳見 `docs/architecture/TRUSTFORGE-STORAGE-ROADMAP.md `。三個階段：

| 階段 | 技術 | 用途 | 狀態 |
| --- | --- | --- | --- |
| **現在 ** | SQLite | 活動題目、分析 queue、不可變 snapshot、結果、lineage、feature values、upgrade approvals | repo 已實作；線上狀態以 `/api/status `為準 |
| **下一階段 ** | Parquet + DuckDB | 當 SQLite >10GB 或 replay query p95 >30s 時，匯出 immutable Bronze/quarantine/lineage/feature values | 已規劃 |
| **Production Scale ** | S3 + Iceberg | 多 worker/節點共享、schema evolution、time travel、retention | 已規劃 |

[← 05 API 參考 ](05-api.md)[07 運維手冊 → ](07-operations.md)
TrustForge 技術文件 · 06 資料流與連接器 · v0.18.5
