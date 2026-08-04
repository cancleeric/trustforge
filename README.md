# TrustForge Hermes（信源熔爐）

## 目前專案快照

以下數字以本 README 更新時的 `origin/main`（`a3f0824b`）為準；重新計算方式寫在表格中，避免把舊版快照誤當成目前狀態。

| 項目 | 狀態 |
|------|------|
| Canonical version | `0.27.51`（`src/trustforge/_version.py`、`frontend/package.json`） |
| Tracked files | `1,913`（`git ls-files`） |
| Tracked UTF-8 text lines | 約 `445,958` 行（程式、設定、Markdown、HTML、測試與腳本；以版控檔案實際換行數計算） |
| Python test files | `403` 個（`tests/**/*.py`） |
| Frontend test files | `86` 個（`frontend/**/*.{test,spec}.*`） |
| 主要文件索引 | [`docs/README.md`](docs/README.md) |
| 技術文件 | [`docs/architecture/`](docs/architecture/)；部署規範見 [`docs/RELEASE-DEPLOY-GOVERNANCE.md`](docs/RELEASE-DEPLOY-GOVERNANCE.md) |
| 比賽交付文件 | [`docs/competition/`](docs/competition/) |

> 測試檔案數與 release gate 的測試批次數不是同一個指標；gate 會依測試收集器與隔離規則重新分批執行。

---

> 加密市場分析 AI Agent — **多源資訊的信任提煉**
>
> 2026 雲湧智生：臺灣生成式 AI 應用黑客松競賽｜黑客組
>
> 命題：【智慧金融：HOYA BIT】加密市場分析 AI Agent：多源資訊的信任提煉
>
> 出品：HurricaneSoft（颶風軟體）

---

## 專案定位

TrustForge 解決的是加密市場資訊的核心問題：**資訊量爆炸，但真假、時效、來源可信度與推論鏈不透明**。

本專案不是「再做一個幣價聊天機器人」，而是把新聞、鏈上訊號、社群、監管公告、HOYA BIT 行情與歷史 OHLCV 先經過 **Trust Layer（信任層）**，再輸出可查證的市場分析。

> 我們交付的不是「一個答案」，而是「一個你能查證的答案」。

---

## 目前專案快照

| 項目 | 狀態 |
|---|---:|
| Canonical version | `0.27.51` |
| Tracked files | 1,837 |
| 程式／設定／文件行數 | 約 369,717 行 |
| 測試檔 | 394 個 |
| 測試函式 | 6,259 個 |
| 主文件索引 | [`docs/README.md`](docs/README.md) |
| 技術文件（Markdown） | [`docs/technical-docs/README.md`](docs/technical-docs/README.md)；含目前已接 4 個台灣來源與 4 條外部資料來源主線，HTML 版另存 [`docs/technical-docs/html/`](docs/technical-docs/html/) |
| 比賽交付文件 | [`docs/competition/`](docs/competition/) |

---

## 核心差異：Trust Layer

| 一般 crypto AI agent | TrustForge |
|---|---|
| 多源資料直接交給 LLM 摘要 | 多源資料先拆成主張、評分、交叉佐證，再交給 Bedrock 組織報告 |
| 來源不分可信度 | 來源信譽、交叉佐證、時效衰減三維評估 |
| 結論難以追溯 | 每個結論保留 evidence 與 provenance |
| 只輸出一句方向 | 輸出信任分數、反方證據、限制條件與反轉條件 |
| demo 常靠人手補故事 | pipeline 產出分析報告、證據清單與執行紀錄 |

---

## 系統架構

```text
多源輸入（6 維 × 14+ 連接器）        Trust Layer（核心）                  Agent 編排 / 輸出
┌─────────────────────────┐       ┌────────────────────────┐       ┌─────────────────────┐
│ 價格：HOYA BIT OHLCV     │       │ 1. Claim extraction     │       │ AWS Bedrock          │
│      CoinGecko / CMC     │       │ 2. Source reputation    │       │ - 信任加權融合        │
│ 鏈上：Blockchain.com     │  ──▶  │ 3. Corroboration        │  ──▶  │ - 有引文敘事化        │
│      Etherscan / Arkham  │       │ 4. Recency decay        │       │ - 反方證據與限制條件  │
│      Whale Alert / DeFi  │       │ 5. TrustScore per claim │       └──────────┬──────────┘
│ 新聞：11 個 RSS 來源     │       └────────────────────────┘                  │
│ 社群：Reddit RSS          │                                            ┌────────▼────────┐
│ 監管：SEC / 金管會 / TWSE │                                            │ Web / CLI Demo   │
│ 情緒：Fear & Greed       │                                            │ Report + Evidence│
└─────────────────────────┘                                            │ Execution Log    │
                                                                        └─────────────────┘
```

### 六維資料來源明細

| 維度 | 已接入來源 | 信譽基準 |
|------|-----------|---------|
| 價格（Price） | HOYA BIT OHLCV、CoinGecko（價格/情緒/dev）、CoinMarketCap、DefiLlama | 0.85~0.95 |
| 鏈上（On-chain） | Blockchain.com、Etherscan、Whale Alert、Arkham、mempool.space、Blockchair | 0.95 |
| 新聞（News） | CoinDesk、Cointelegraph、Decrypt、BitcoinMagazine、CryptoSlate、Bitcoinist、NewsBTC、DailyHodl、TheBlock、UToday、Blockworks（共 11 個 RSS） | 0.65 |
| 社群（Social） | Reddit RSS（r/CryptoCurrency、r/Bitcoin）、CryptoPanic | 0.35~0.50 |
| 監管（Regulatory） | SEC EDGAR、FSC 金管會、MOPS 公開資訊觀測站、TWSE 臺灣證交所、TPEx 櫃買中心 | 0.90 |
| 情緒（Sentiment） | Alternative.me Fear & Greed Index、CoinGecko Sentiment | 0.50 |

詳見：

- [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md)
- [`docs/architecture/AWS-ARCHITECTURE.md`](docs/architecture/AWS-ARCHITECTURE.md)
- [`docs/technical-docs/02-architecture.md`](docs/technical-docs/02-architecture.md)（HTML 版：[`docs/technical-docs/html/02-architecture.html`](docs/technical-docs/html/02-architecture.html)）

### 實際接入資料源

README 上方的圖只列資料類型；目前 repository 內已接入或保留 adapter 的具體來源如下，實際執行時仍受環境變數、憑證、成本上限與 fail-closed 策略控制。

| 類型 | 來源 | 用途 |
|---|---|---|
| 價格 / 市場資料 | HOYA BIT OHLCV、CoinGecko、CoinMarketCap、DefiLlama | 歷史價格、即時價格交叉佐證、DeFi TVL 與市場背景 |
| 鏈上 / 大額轉帳 | Etherscan、Whale Alert、Arkham Intelligence | ETH 鯨魚交易與大額轉帳追蹤，供鏈上訊號與反方證據使用 |
| 新聞 / RSS | News / RSS connectors | 市場敘事、事件脈絡與 sentiment 類 claim |
| 監管 / 台灣來源 | FSC 金管會 VASP 公告、MOPS、TWSE、TPEx | 台灣監管公告、公開資訊觀測站與市場揭露資料 |
| ESG / 碳足跡 | 碳足跡模組 | ESG 與能源/碳排相關證據補充 |

---

## Hermes Agent 能力

Hermes 是 TrustForge 的有界自主研究 Agent。它可以執行資料刷新、快照建構、品質量測、回放、診斷、升級候選審查與正式分析，但所有生產變更都受 fail-closed 與人工核准邊界限制。

### 主要工具鏈

| 類別 | 能力 |
|---|---|
| 資料刷新 | `refresh_sources`、`archive_source_snapshot`、`build_snapshots` |
| 品質與可靠度 | `measure_connector_reliability`、`measure_quality`、`replay_history` |
| 正式分析 | `read_snapshot`、`extract_claims`、`classify_stance`、`assemble_report`、`export_deliverables` |
| 自我改善 | `diagnose_improvement`、`review_upgrades`；只產生候選，不自動改生產 |

### 技能約束

1. **five-year-ohlcv-lineage** — 價格事實必須能回到資料範圍與 checksum。
2. **evidence-contract** — 結論必須連到 `source/fetched_at/content_reference/related_claim`。
3. **contrarian-evidence** — 反方與低信任證據不得靜默丟棄。
4. **report-contract** — 報告必含判斷、關鍵依據、校準信心、限制條件、反轉條件。
5. **bounded-self-improvement** — 診斷可提出沙盒實驗；生產啟用需人工核准。

詳細證據見 [`docs/HERMES-CAPABILITIES-REVIEW.md`](docs/HERMES-CAPABILITIES-REVIEW.md)。

---

## 競賽硬約束

1. 生成式 AI 模型入口以 **AWS Bedrock** 為準；競賽路徑不走內部模型閘道。
2. 使用 HOYA BIT 企業資料與官方 OHLCV 基準資料。
3. 交付需包含技術架構、企業資料應用、生成式 AI 應用與 Live Demo。
4. 所有功能主張必須對應可驗證 evidence；不得虛報 demo 能力。
5. Kiro 使用採 evidence-first、保守口徑：只主張部分 spec-driven workflow，不宣稱全程使用。

競賽文件入口：

- [`docs/competition/COMPETITION.md`](docs/competition/COMPETITION.md)
- [`docs/competition/COMPLIANCE-CHECK.md`](docs/competition/COMPLIANCE-CHECK.md)
- [`docs/competition/SUBMISSION-CHECKLIST.md`](docs/competition/SUBMISSION-CHECKLIST.md)
- [`docs/competition/SLIDE-DECK.md`](docs/competition/SLIDE-DECK.md)
- [`docs/competition/SPEECH-SCRIPT-6MIN.md`](docs/competition/SPEECH-SCRIPT-6MIN.md)
- [`docs/competition/TRUST-EXPLAINABILITY.md`](docs/competition/TRUST-EXPLAINABILITY.md)

---

## 快速開始

```bash
# 1. 建立 Python 環境
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. 設定 AWS Bedrock（live 模式需要）
export AWS_REGION=us-east-1
export BEDROCK_MODEL_ID="<Bedrock model id>"

# 3. 離線 demo：不呼叫 AWS，也能看信任層與交付件格式
python -m trustforge.cli analyze \
  --coin BTC \
  --type multi_source \
  --query "分析 BTC 過去兩週市場狀況，整合多源資料" \
  --offline \
  --out out/btc

# 4. 測試
pytest -q
```

題型：

| 題型 | 用途 |
|---|---|
| `multi_source` | 多源市場整合分析 |
| `hypothesis` | 假設驗證 |
| `comparison` | 幣種／事件比較分析 |

幣種池：`BTC`、`ETH`、`SOL`、`BNB`、`XRP`。

---

## Live Demo

```bash
./scripts/trustforge_control.sh start
./scripts/trustforge_control.sh status
./scripts/trustforge_control.sh stop
```

本機 demo 預設不自動打開 Bedrock；要走真實 Bedrock 與 production continuous cycle，需明確設定 runtime switch 與成本上限。詳見：

- [`docs/competition/AWS-LAMBDA-DEPLOYMENT.md`](docs/competition/AWS-LAMBDA-DEPLOYMENT.md)
- [`docs/runbooks/HERMES_PRODUCTION_AUDIT.md`](docs/runbooks/HERMES_PRODUCTION_AUDIT.md)
- [`deploy/README.md`](deploy/README.md)

---

## 官方交付件

每次正式分析會產生或對應下列交付物：

| 交付件 | 範例位置 | 說明 |
|---|---|---|
| 專題報告／分析報告／最終報告 | `out/<coin>/report.md` | 市場判斷、關鍵依據、信心與限制條件 |
| 證據清單 | `out/<coin>/evidence.json` | 每筆證據含來源、時間、引用與對應主張 |
| 執行紀錄 | `out/<coin>/execution_log.jsonl` | 工具呼叫、流程時戳、預算與狀態 |
| 程式碼與設定 | 本 repo | pipeline、模型入口、測試、部署與文件 |

> 反作弊邊界：市場判斷、證據整合與信任評分由本 pipeline 產生；Bedrock 負責受約束的語意抽取、立場分類與敘事化，不得把第三方現成結論當主要結果。

---

## 文件導覽

| 區域 | 入口 | 用途 |
|---|---|---|
| 主文件索引 | [`docs/README.md`](docs/README.md) | 所有規劃、技術、交付文件總入口 |
| 技術文件（Markdown） | [`docs/technical-docs/README.md`](docs/technical-docs/README.md) | 從 devlog 技術文件同步到主 repo 的交付版文件；GitHub / code review 以 Markdown 為主 |
| 技術文件（HTML） | [`docs/technical-docs/html/index.html`](docs/technical-docs/html/index.html) | 原 devlog 視覺版另存一份，適合瀏覽器展示或離線交付 |
| Evidence map | [`docs/technical-docs/00-evidence-map.md`](docs/technical-docs/00-evidence-map.md) | 技術主張與佐證矩陣；HTML 版：[`docs/technical-docs/html/00-evidence-map.html`](docs/technical-docs/html/00-evidence-map.html) |
| 比賽投稿 | [`docs/technical-docs/16-competition-submission.md`](docs/technical-docs/16-competition-submission.md) | 投稿與展示口徑；HTML 版：[`docs/technical-docs/html/16-competition-submission.html`](docs/technical-docs/html/16-competition-submission.html) |
| 架構 | [`docs/architecture/`](docs/architecture/) | 架構設計、ADR、資料契約 |
| 競賽 | [`docs/competition/`](docs/competition/) | 官方規範、簡報、講稿、交付清單 |
| AIMS / EU AI | [`docs/aims/README.md`](docs/aims/README.md) | ISO/IEC 42001 與 EU AI Act overlay 草案；不代表正式認證或 conformity claim |

---

## 倉庫結構

```text
trustforge/
├── README.md
├── pyproject.toml
├── src/trustforge/              # Python backend、Trust Layer、Bedrock、CLI、web API
├── frontend/                    # React + Vite + TypeScript UI
├── native/                      # native trust / immutable runtime foundation
├── deploy/                      # App Runner、Lambda、nginx、release router、TLS、scheduler scripts
├── docs/
│   ├── README.md                # 文件總索引
│   ├── competition/             # 競賽規範、簡報、講稿、交付清單
│   ├── architecture/            # 架構、資料契約、ADR
│   ├── technical-docs/          # Markdown 技術文件；html/ 另存 HTML 靜態版
│   ├── plans/                   # 開發計劃與差距分析
│   ├── qa/                      # 測試、QA、研究發現
│   └── aims/                    # AIMS / EU AI Act overlay 草案
├── data/                        # 官方 OHLCV、資料集 metadata、training/evidence fixtures
├── demo/sample_data/            # 離線 demo 樣本
├── scripts/                     # 驗證、訓練、打包、release 輔助腳本
└── tests/                       # pytest regression / security / release / API / frontend-adjacent gates
```

---

## 開發與 release 紀律

本 repo 採 **local pre-push gate 為準**；GitHub Actions 不是 release gate。

1. 從 issue 與 acceptance criteria 開始。
2. 建 scoped branch，不直接在 `main` 開發。
3. 實作需附 regression tests 或文件驗證。
4. push 前跑 `.githooks/pre-push`；至少需 `git diff --check` 無誤。
5. PR 需記錄驗證證據、reviewer attestation、必要時 `/codex-review`。
6. 安全／成本敏感變更需具名安全或成本審查。
7. production deploy 走明確 release workflow，部署後驗證 health 與實際使用者流程。

權威規則見 [`docs/RELEASE-DEPLOY-GOVERNANCE.md`](docs/RELEASE-DEPLOY-GOVERNANCE.md) 與 [`docs/governance/PRE_PUSH_RELEASE_GATES.md`](docs/governance/PRE_PUSH_RELEASE_GATES.md)。

---

## 版控

| 位置 | 說明 |
|---|---|
| GitHub | `https://github.com/cancleeric/trustforge` |
| Gitea | `http://appleteki-MacBook-Air-2.local:3033` |

GitHub 是主 repo；Gitea 為公司內部 Git 伺服器入口。不要在文件中寫死區網 IP，使用 mDNS hostname。

---

## 團隊

| 成員 | 學校／單位 | LinkedIn | 職務 | 分工 |
|---|---|---|---|---|
| 王英豪 | 颶風軟體有限公司 | [LinkedIn](https://www.linkedin.com/in/%E8%8B%B1%E8%B1%AA-%E7%8E%8B-8b399a73/) | 隊長 | 主要開發 |
| 曾嵋婕 | 國立中正大學 | [LinkedIn](https://www.linkedin.com/in/%E5%B5%8B%E5%A9%95-%E6%9B%BE-6a2b11365/) | 組長 | 專案指導 |
| 林子彤 | 中原大學 | [LinkedIn](https://www.linkedin.com/in/%E6%9E%97-%E5%BD%A4-17b435412/) | 企劃長 | 使用者介面前端、專案企劃 |
| 王榆翔（Nicholas） | 中原大學 | [LinkedIn](https://www.linkedin.com/in/%E5%92%AA%E5%B8%B6%E5%AD%A3-%E4%BA%94%E8%9D%A6-654522426/) | 副隊長 | 外框升級模組開發 |

詳見 [`docs/competition/TEAM.md`](docs/competition/TEAM.md)。
