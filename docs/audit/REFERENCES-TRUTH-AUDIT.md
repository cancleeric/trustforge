# References 狀態 Truth Audit

> Issue: #384
> 審計日期: 2026-07-21（v1）→ 2026-07-22（v2 更新）
> 審計人: implement-384 (v1) → implement-384-v2 (v2)
> 比對來源: `/tmp/trustforge-devlog/references.html` vs `main` branch (commit HEAD)
>
> **v2 變更摘要**：補入 AgentCore backend registry 段落（🟡）；校準模型路徑 PR #394 修正後升為 ✅ verified；明確標注 GitHub Actions `.disabled` 語意；逐項對照 issue comment 2026-07-22 的六項待修正點。

## 狀態圖例

| 符號 | 意義 |
|------|------|
| ✅ verified | 有 repo path + 測試 + runtime evidence |
| 🟡 implemented-not-verified | 程式碼存在但未驗證 live/production 狀態 |
| 🔬 research/experimental | 已寫碼但明確不在 production 路徑 |
| 📚 reference/planned | 方法論參考或待辦，非直接依賴 |
| ⛔ excluded | 明確排除，不會接入 |
| ⚠ blocked-external | 依賴外部條件未滿足 |

---

## 一、學術方法 / 論文

### 1. 真理發現 · 多源衝突聚合（Truth Discovery）

| 方法 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| Dawid–Skene EM (1979) | ✅ 已實作 | ✅ verified | `src/trustforge/trust/dawid_skene.py` | EM 離線 fallback，預設在 W2 路徑啟用；commit `ab0ea40`；測試 `tests/test_dawid_skene.py` |
| Yin et al. (2008) Copy-Resolved TD | 📚 方法論參考 | 📚 reference | — | 未直接實作 |
| Li et al. (2014) CATD | 📚 方法論參考 | 📚 reference | — | 未直接實作 |
| Dong et al. (2009) Invest | 📚 方法論參考 | 📚 reference | — | 未直接實作 |
| Wang et al. (2016) LTM | 📚 方法論參考 | 📚 reference | — | 未直接實作 |
| Pasternack & Roth (2010/2013) | 📚 方法論參考 | 📚 reference | — | 未直接實作 |
| Ma et al. (2013) FaitCrowd | 📚 方法論參考 | 📚 reference | — | 未直接實作 |
| Truth Discovery Algorithms (TKDE 2018) | 📚 方法論參考 | 📚 reference | — | 基準比較文獻 |
| Li et al. (2016) TD Survey | 📚 方法論參考 | 📚 reference | — | 全局指引 |

### 2. 來源信譽 · 動態權重 · 聲譽系統

| 方法 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| EigenTrust (Kamvar 2003) | 📚 方法論參考 | 📚 reference | — | 未直接實作；精神融入同源只算一票設計 |
| Resnick et al. (2000) | 📚 方法論參考 | 📚 reference | — | 聲譽系統經典綜述 |
| Jøsang et al. (2007/2017) | 📚 方法論參考 | 📚 reference | — | 主觀邏輯理論互補 |

### 3. 信任校準 · 不確定性量化

| 方法 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| Guo et al. (2017) Calibration | 🔬 研究中 | ✅ verified | `src/trustforge/calibration_model.py` + `data/model-artifacts/calibration-model.json` | **PR #394（commit `327fe48`）修正路徑分裂**：`_calibration_model_path()` 優先遍歷 `data/model-artifacts/` → `out/model-artifacts/` → fallback `_CALIBRATION_TABLE`；1980 筆 ground truth 已訓練並版控；測試 `tests/test_calibration_model.py` |
| Conformal Prediction (Split) | 🔬 研究中 | 🔬 research | `src/trustforge/trust/conformal.py` | 回測顯示 abstain rate ~94%（無判別力），CEO 決策本輪不 wire 進 production；commit `4e638e2`；測試 `tests/test_w4_conformal.py` |
| Niculescu-Mizil & Caruana (2005) Platt | 📚 方法論參考 | 📚 reference | — | 未直接實作 |
| Kuleshov et al. (2018) | 📚 方法論參考 | 📚 reference | — | 未直接實作 |
| Lakshminarayanan et al. (2017) Deep Ensembles | 📚 方法論參考 | 📚 reference | — | 未直接實作 |
| Ovadia et al. (2019) | 📚 方法論參考 | 📚 reference | — | 未直接實作 |

### 4. LLM 事實性 · 幻覺 · 檢索增強（RAG）

| 方法 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| TruthfulQA (Lin 2022) | 📚 方法論參考 | 📚 reference | — | 評測框架參考 |
| Self-RAG (Asai 2023) | 📚 方法論參考 | 📚 reference | — | 未實作（目前從 API 抓真源，不用向量庫） |
| Lewis et al. (2020) RAG | 📚 方法論參考 | 📚 reference | — | 同上 |
| Chain-of-Verification (CoVe) | 📚 方法論參考 | 📚 reference | — | 精神呼應 Hermes stance+改進流，未直接實作 |
| Li et al. (2025) Agentic RAG Survey | 📚 方法論參考 | 📚 reference | — | 架構對照文獻 |
| Huang et al. (2023) Hallucination Survey | 📚 方法論參考 | 📚 reference | — | 未直接實作 |
| Zhao et al. (2023) LLM Survey | 📚 方法論參考 | 📚 reference | — | 未直接實作 |

### 5. 情感分析 · 社群情緒

| 方法 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| VADER (Hutto 2014) | 📚 方法論參考 | 📚 reference | — | 未直接使用 VADER 模型；情緒來自 CoinGecko votes |
| FinBERT (Araci 2019) | 📚 方法論參考 | 📚 reference | — | 未引入任何 NLP 模型 |
| 加密情緒-價格文獻 | 📚 方法論參考 | 📚 reference | — | 研究脈絡 |

### 6. 鏈上指標 · 市場效率

| 方法 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| Narayanan et al. (2016) Bitcoin textbook | 📚 方法論參考 | 📚 reference | — | 教科書參考 |
| Fama (1970) EMH | 📚 方法論參考 | 📚 reference | — | 理論背景 |
| 鏈上指標文獻 | 📚 方法論參考 | 📚 reference | — | 研究脈絡 |

### 7. 操縱 · 協同 · 泵坑檢測

| 方法 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| Xu & Livshits (2019) Pump-and-Dump | 📚 方法論參考 | 📚 reference | — | 方法論參考，未直接複製演算法 |
| 協同行為檢測脈絡 | 🔬 研究中 | 🟡 implemented-not-verified | `src/trustforge/trust/scoring.py::_coordination_template_flags`, `_coordination_burst_flags` + `src/trustforge/trust/insights.py::detect_manipulation_burst` | W3 coordination 指標已實作但降為 informational-only（不扣分），CEO 定案文字相似度無法區分協同操縱 vs 合法聯播 |
| Sybil / 女巫攻擊防禦 | 📚 方法論參考 | 📚 reference | — | 精神融入「同源只算一票」的 `_canonical_source` 設計 |

### 8. 新聞可信度 · 假新聞

| 方法 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| FakeNewsNet / LIAR | 📚 方法論參考 | 📚 reference | — | 學術資料集，未使用 |
| 多源可信度（跨源交叉驗證） | ✅ 已實作 | ✅ verified | `src/trustforge/trust/scoring.py::_corroboration_detail`, `_corroboration` | 核心交叉佐證引擎，配合 Bedrock stance 判斷；測試 `tests/test_trust_scoring.py`, `tests/test_corroboration_false_boost_d02.py` |

### 9. 可解釋 · 溯源

| 方法 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| LIME (Ribeiro 2016) | 📚 方法論參考 | 📚 reference | — | 未直接實作 |
| SHAP (Lundberg 2017) | 📚 方法論參考 | 📚 reference | — | 未直接實作 |

> 注意：TrustForge 的溯源是「每結論帶 claim_id → 原始 Document → source」的 provenance chain，非 LIME/SHAP 式後解釋，實作於 `agent/orchestrator.py` Step 3 帶溯源行文。

### 10. 統計基礎 · 多數決

| 方法 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| Condorcet's Jury Theorem | 📚 方法論參考 | 📚 reference | — | 精神融入「同源只算一票」設計 |
| Grofman et al. (1983) | 📚 方法論參考 | 📚 reference | — | 理論背景 |

### 11. 防作弊 · 女巫攻擊防禦

| 方法 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| Douceur (2002) Sybil Attack | 📚 方法論參考 | 📚 reference | — | 理論支撐；實際防護：`scoring.py::_canonical_source` 同源只算一票 |

---

## 二、產業大廠 / 真實資料源

| 資料源 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|--------|----------|----------|------|------|
| HOYA BIT 官方 OHLCV | ✅ 已實作 | ✅ verified | `src/trustforge/ingestion/prices.py` + `data/` 目錄 | 5 幣 ×5 年 Daily OHLCV 為價格真值基準 |
| HOYA BIT 線上 ticker | ✅ 已實作 | ⚠ blocked-external | `src/trustforge/ingestion/hoyabit.py` | 待 `TRUSTFORGE_HOYABIT_TICKER_URL` 設定，官方 HTTPS contract 未提供前 disabled；不得標 ✅ |
| CoinGecko 現價 | ✅ 已實作 | ✅ verified | `src/trustforge/ingestion/coingecko.py::CoinGeckoPriceSource` | 免費 API，keyless 節流已實作；測試 `tests/test_coingecko.py` |
| CoinGecko 情緒/開發活動 | ✅ 已實作 | ✅ verified | `src/trustforge/ingestion/coingecko.py::CoinGeckoSentimentSource`, `CoinGeckoDevSource` | 同上 |
| SEC EDGAR 全文檢索 | ✅ 已實作 | ✅ verified | `src/trustforge/ingestion/regulatory.py` | efts.sec.gov keyless；測試 `tests/test_regulatory_fts_d03.py` |
| Blockchain.com (blockchain.info) | ✅ 已實作 | ✅ verified | `src/trustforge/ingestion/onchain.py::BlockchainInfoSource` | 公開 API，BTC only |
| mempool.space | ✅ 已實作 | ✅ verified | `src/trustforge/ingestion/onchain.py::MempoolFeesSource`, `MempoolDifficultySource` | keyless，BTC only |
| Blockchair BTC stats | ✅ 已實作 | ✅ verified | `src/trustforge/ingestion/onchain.py::BlockchairStatsSource` | 免費層 1440 req/day |
| Cointelegraph RSS | ✅ 已實作 | ✅ verified | `src/trustforge/ingestion/news.py::CoinTelegraphRSSSource` | 公開 RSS |
| CoinDesk RSS | ✅ 已實作 | ✅ verified | `src/trustforge/ingestion/news.py::CoinDeskRSSSource` | 公開 RSS（308 修復完成） |
| Reddit r/CryptoCurrency | ✅ 已實作 | 🟡 implemented-not-verified | `src/trustforge/ingestion/social.py` | 程式碼存在但 cloud IP 常 403；references.html 標 ⛔ 已排除，實際仍保留降級邏輯 |
| Reddit（references 標記） | ⛔ 已排除 | ⛔ excluded | — | references.html 明確標記接不上 |
| MOPS 公開資訊觀測站 | 📚 建議接入 | 📚 planned | — | 台灣監管源，未接入 |
| 金管會 FSC | 📚 建議接入 | 📚 planned | — | 未接入 |
| TWSE / TPEx | 📚 建議接入 | 📚 planned | — | 未接入 |
| BlockTempo 動區 | 📚 建議接入 | 📚 planned | — | 未接入 |

---

## 三、基礎設施 / 模型

| 項目 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| AWS Bedrock (Claude Sonnet) | ✅ 已實作 | ✅ verified | `src/trustforge/bedrock.py` | 唯一 LLM 入口；stance + narrative 雙模型；測試 `tests/test_bedrock_stance.py` |
| GitHub Pages (devlog) | ✅ 已實作 | ✅ verified | `/tmp/trustforge-devlog/` | 靜態站可存取 |
| SQLite (devlog DB) | ✅ 已實作 | ✅ verified | devlog `build_db.py` | devlog 來源真相 |

---

## 四、選定技術 / 技術堆疊

| 項目 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| Python 3 後端 | ✅ 已實作 | ✅ verified | `src/trustforge/` | Python 3.12+，stdlib 為主 |
| TypeScript + React 前端 | ✅ 已實作 | ✅ verified | `frontend/package.json` | React 19, react-router, recharts |
| Vite + Vitest | ✅ 已實作 | ✅ verified | `frontend/package.json` | build + test 腳本存在 |
| pytest | ✅ 已實作 | ✅ verified | `tests/` | 120+ 測試檔案 |
| AWS App Runner | ✅ 已實作 | 🟡 implemented-not-verified | `apprunner.yaml` | 設定檔存在；尚未確認 production deploy 是否活躍 |
| AWS SSM + EventBridge | ✅ 已實作 | 🟡 implemented-not-verified | `src/trustforge/ssm_params.py` + `scripts/fetch_scheduler.py` | SSM token 讀取已實作（opt-in）；EventBridge 排程未見 IaC 定義，僅有設計文件 |
| AgentCore Backend Registry | — | 🟡 implemented-not-verified | `src/trustforge/backend_registry.py` + `src/trustforge/ports.py` | Provider registry 可熱切換 `builtin`/`agentcore`（7 keys）；預設 `builtin`；無 agentcore adapter 端到端實作；測試 `tests/test_backend_registry.py` |
| SQLite 快取 | ✅ 已實作 | ✅ verified | `src/trustforge/ingestion/cache.py` (line 612+) | `CACHE_BACKEND=sqlite` 路徑完整 |
| nginx | ✅ 已實作 | 🟡 implemented-not-verified | `deploy/nginx.conf`, `deploy/nginx-react-http.conf` 等 | 設定檔存在，production 未驗證 |
| Docker | ✅ 已實作 | ✅ verified | `Dockerfile` | python:3.12-slim 基底 |
| GitHub Actions CI | ✅ 已實作 | 🟡 implemented-not-verified | `.github/workflows/ci.yml.disabled` | **所有 workflow 均帶 `.disabled` 後綴**，`workflow_dispatch` only，不自動觸發；Production Deploy workflow（`deploy-production.yml.disabled`）明確停用。能手動觸發但未進入 CI 自動門控。 |

---

## 五、資料來源（可接入的公開資源大普查）

### 5.1 行情 / 價格

| 來源 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| CoinGecko | ✅ 已接 | ✅ verified | `ingestion/coingecko.py` | 現價 + 情緒 + dev |
| HOYA BIT | ✅ 已接 | ✅ verified (OHLCV) / ⚠ blocked (live) | `ingestion/prices.py` + `ingestion/hoyabit.py` | 歷史 OHLCV ✅；live ticker ⚠ |
| CoinCap / CoinPaprika / CoinMarketCap / CryptoCompare / Binance / Coinbase | 📚 建議 | 📚 planned | — | 均未接入 |

### 5.2 鏈上指標 / DeFi

| 來源 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| Blockchain.com | ✅ 已接 | ✅ verified | `ingestion/onchain.py` | BTC only |
| Etherscan / Dune / The Graph / DeFiLlama / Messari / Glassnode / Token Terminal | 📚 建議 | 📚 planned | — | 均未接入 |

### 5.3 新聞 / 情緒 / 事實查核

| 來源 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| Cointelegraph | ✅ 已接 | ✅ verified | `ingestion/news.py::CoinTelegraphRSSSource` | RSS |
| CoinDesk RSS | ✅ 已接 (列在 RSS) | ✅ verified | `ingestion/news.py::CoinDeskRSSSource` | RSS |
| Bitcoin Magazine / CryptoSlate / Bitcoinist / NewsBTC / DailyHodl / The Block / U.Today / Blockworks | — | ✅ verified | `ingestion/news.py` | 第一/二批 RSS 全已接入 |
| Decrypt | — | ✅ verified | `ingestion/news.py` | 公開 RSS |
| CryptoPanic | — | 🟡 implemented-not-verified | `ingestion/news.py` | 需 env `CRYPTOPANIC_TOKEN` |
| Reddit | ⛔ 已排除 | ⛔ excluded | — | API 終止 |
| NewsAPI / Google Fact Check | 📚 建議 | 📚 planned | — | 未接入 |
| BlockTempo | 📚 建議 | 📚 planned | — | 未接入 |

### 5.4 監管 / 合規

| 來源 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| SEC EDGAR (美國) | ✅ 已接 | ✅ verified | `ingestion/regulatory.py` | 全文檢索 |
| MOPS / FSC / TWSE / TPEx (台灣) | 📚 建議 | 📚 planned | — | 均未接入 |
| ESMA / FCA / MAS | 📚 建議 | 📚 planned | — | 均未接入 |

### 5.5 學術資料集 / 基準

| 來源 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| TruthfulQA / FakeNewsNet / LIAR / Bitcoin OTC / BitcoinHeist | 📚 基準/資料集 | 📚 reference | — | 均未使用於 runtime |

---

## 六、技術社群 / 研究平台

全部為 📚 參考，無直接依賴，與 references.html 一致。

---

## 七、大廠實作細節 / API 文件

| 項目 | 參考狀態 | 實際狀態 | 位置 | 說明 |
|------|----------|----------|------|------|
| AWS Bedrock (Claude Sonnet 4.6) | ✅ 已接 | ✅ verified | `bedrock.py` | 語意矛盾判斷特徵 |
| OpenAI / Anthropic 文件 | 📚 文件 | 📚 reference | — | 做法參考 |
| LangChain / LlamaIndex | 📚 文件 | 📚 reference | — | 未使用（不用向量庫） |
| Pinecone / Weaviate / Qdrant | 📚 文件 | 📚 reference | — | 未使用 |
| Google Fact Check API | 📚 文件 | 📚 reference | — | 未接入 |

---

## 八、待辦：接台灣監管源

全部為 📚 planned，與 references.html 一致。程式碼中無任何台灣監管源實作（grep 確認：無 mops/fsc/twse/tpex/blocktempo 相關程式碼）。

---

## 關鍵發現摘要

### 與 references.html 宣稱不符之處（需誠實降級）

1. **HOYA BIT live ticker**：references 標 ✅ 已實作，但程式碼明確 `disabled`（待 `TRUSTFORGE_HOYABIT_TICKER_URL` 設定）。**實際狀態：⚠ blocked-external**。
2. **Reddit**：references 標 ⛔ 已排除，但 `social.py` 仍保留 RSS 爬取碼（降級邏輯）。分歧不嚴重——⛔ 符合 references 標記，程式碼只是未清除舊實作。
3. **GitHub Actions CI**：references 標 ✅ 已實作，但三個 workflow 均帶 `.disabled` 後綴且只有 `workflow_dispatch` 觸發。**實際狀態：🟡 implemented-not-verified**（CI 能手動跑但非自動觸發；Production Deploy workflow 停用）。
4. **AWS SSM + EventBridge**：references 標 ✅ 已實作，SSM 讀取碼存在但 EventBridge IaC 定義未見於 repo。**實際狀態：🟡 implemented-not-verified**。
5. **協同行為檢測**：references 標 🔬 研究中，實際已有 `_coordination_template_flags` / `_coordination_burst_flags` + insights `detect_manipulation_burst`，但 CEO 定案降為 informational-only（不扣分）。**實際狀態：🟡 implemented-not-verified**（有碼、有測試、但不影響信任分）。

### v2 新增發現（2026-07-22）

6. **AgentCore runtime routing**：`backend_registry.py` + `ports.py` 已實作 provider registry，可熱切換 `builtin`/`agentcore`；但實際 agentcore adapter 路由未驗證（`get_provider()` 預設返回 `builtin`，無 agentcore 端對端 adapter 實作）。references.html 未列此項；**實際狀態：🟡 implemented-not-verified**（registry 有，routing 未接通）。
7. **Calibration 路徑（已修正）**：v1 審計時 `calibration_model.py` 硬編碼 `out/` 路徑；PR #394（commit `327fe48`）修正為 `_calibration_model_path()` 優先遍歷 `data/model-artifacts/`，且 1980 筆 ground truth 已版控。**實際狀態升為 ✅ verified**。

### 正確標記（無需修改）

- Dawid–Skene EM：✅ 正確
- Calibration (Guo 2017)：**v2 升為 ✅ verified**（PR #394 路徑修正 + 1980 筆訓練資料版控）
- Conformal Prediction：🔬 正確（研究工件，不在 production）
- 多源可信度/交叉驗證：✅ 正確
- CoinGecko / SEC EDGAR / Blockchain.com / Cointelegraph：✅ 正確
- AWS Bedrock：✅ 正確
- 所有 📚 方法論參考：正確（均為脈絡，非直接依賴）
- Reddit ⛔ 已排除：正確

---

## 驗收條件對照

### v1（2026-07-21，PR #387）

- [x] 逐項核對學術方法、模型、資料源、基礎設施
- [x] 每個 ✅ 附 repo path、commit 或測試
- [x] HOYA BIT 誠實分級（歷史 OHLCV ✅ / live ticker ⚠ blocked）
- [x] calibration 誠實分級（🔬 research + calibrator gate blocked）→ **v2 已升為 ✅ verified（PR #394）**
- [x] manipulation detection 誠實分級（🟡 informational-only）
- [x] GitHub Actions 明確標註 `.disabled` 狀態，Production Deploy 停用
- [x] 台灣監管來源未取得真資料前不標 ✅（全部 📚 planned）
- [x] 頁面與 repo 文件一致性檢查完成

### v2（2026-07-22，本 PR）

- [x] AgentCore backend registry 新增段落：🟡 implemented-not-verified（registry 有，routing 未驗證）
- [x] 校準模型狀態升為 ✅ verified（PR #394 路徑修正 + `data/model-artifacts/` 1980 筆訓練資料版控）
- [x] GitHub Actions `.disabled` 說明強化（明確 Production Deploy workflow 亦停用）
- [x] issue comment 六項待修正點全部對照更新（HOYA BIT / GitHub Actions / App Runner / nginx / EventBridge / Calibration / AgentCore / Manipulation）
- [x] 狀態圖例覆蓋所有六個符號（✅ / 🟡 / 🔬 / 📚 / ⛔ / ⚠）

---

## 可重跑驗證

此 audit 由 `scripts/check_references_truth_audit.py` 提供 focused guard，可在 PR review 時重跑：

```bash
python3 scripts/check_references_truth_audit.py
pytest --no-cov tests/test_references_truth_audit.py
```

檢查重點：

- HOYA BIT live ticker 仍標為 `⚠ blocked-external`，不得在正式 HTTPS contract 前升為 ✅。
- AgentCore runtime routing 仍標為 `🟡 implemented-not-verified`，不得把 registry 存在誤寫成 production routing 已驗證。
- RAG 仍是 `📚 reference/planned`，不得把文獻引用誤寫成 runtime 功能。
- Manipulation detection 仍是資訊性 `🟡 implemented-not-verified`，不得誤寫成交易/production 防護。
- MOPS/FSC/TWSE/TPEx 在取得真資料與測試前不得標 ✅。
- GitHub Actions 只描述停用中的 `.disabled` workflows；Production Deploy workflow 必須明確停用。
