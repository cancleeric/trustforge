# 08 — 信任演算法詳解

[← 07 運維手冊 ](07-operations.md)[文件首頁 ](README.md)[09 前端架構 → ](09-frontend.md)

## 08 — 信任演算法詳解

Trust Algorithm · TrustScore 公式、權重矩陣、Stance 分類、Dawid-Skene、Conformal

**目錄 **

- [演算法總覽 ](#overview)

- [TrustScore 計算公式 ](#trust-score)

- [預設權重矩陣 ](#weights)

- [Source Reputation（來源信譽） ](#reputation)

- [Cross-Source Corroboration（多源佐證） ](#corroboration)

- [Recency Decay（時效衰減） ](#recency)

- [Manipulation Penalty（操縱懲罰） ](#manipulation)

- [Stance 分類（立場分類器） ](#stance)

- [Dawid-Skene EM（動態來源信譽） ](#dawid-skene)

- [Conformal Calibration（校準） ](#conformal)

- [獨特洞察偵測 ](#insights)

- [協同操縱偵測 ](#coordination)

### 1. 演算法總覽

TrustForge 的信任層（Layer 2）是整個系統的核心差異點。它不是把原始資料直接餵給 LLM 後再做分數後處理，而是在 LLM **之前 **對每條主張做獨立信任評分。每個分量都有明確的數學定義和可調整的權重。

**不可變量： **TrustScore 四分量設計保證以下不變量——任一已確認操縱筆（ `manipulation=1.0 `）必然使總分低於無操縱情境；來源信譽由 Dawid-Skene EM 動態學習，不靠靜態白名單。

### 2. TrustScore 計算公式

TrustScore = w_src × SourceReputation + w_corr × CrossSourceCorroboration + w_rec × RecencyDecay − w_manip × ManipulationPenalty

每個分量標準化至 [0, 1] 範圍。總分 range 為 [−w_manip, w_src + w_corr + w_rec]。

| 分量 | 數學定義 | 範圍 | 說明 |
| --- | --- | --- | --- |
| SourceReputation | Dawid-Skene posterior（動態）+ static tier weight | [0, 1] | 來源歷史正確率 × 來源等級權重 |
| CrossSourceCorroboration | Jaccard 相似度（同主張 ÷ 獨立來源總數），扣除回音室 | [0, 1] | 越多獨立來源佐證同一主張 → 越高分 |
| RecencyDecay | `e^(−λ × hours_ago) `，加密幣市場半衰期 λ = ln(2)/4（~4h） | [0, 1] | 資訊越新 → 權重越重 |
| ManipulationPenalty | 4 項子分數合計（見 §7） | [0, 1] | 喊單/bot/極化 → 懲罰扣分 |

### 3. 預設權重矩陣

| 權重 | 值 | 占總正分比例 | 說明 |
| --- | --- | --- | --- |
| `w_src ` | 0.50 | 55.6% | 來源信譽最重要——可靠來源的資訊價值高於不可靠來源 |
| `w_corr ` | 0.25 | 27.8% | 多源佐證次重要——獨立確認提供信心加成 |
| `w_rec ` | 0.15 | 16.7% | 時效性輔助——加密市場資訊快速貶值 |
| `w_manip ` | 0.40 | 44.4%* | 操縱懲罰權重故意設高——寧可錯殺，避免把操縱訊號當合法資訊 |

*w_manip 是扣分項，百分比為相對總正分 (0.90) 的比例。

權重定義在 `trust/scoring.py::DEFAULT_WEIGHTS `，可透過環境變數調整。

### 4. Source Reputation（來源信譽）

來源信譽由兩層組合：

#### 4.1 Static Tier Weight（靜態來源等級）

| 等級 | 來源類型 | 權重 |
| --- | --- | --- |
| Tier 1 | On-chain、OHLCV、Regulatory（SEC） | 1.00 |
| Tier 2 | Mainstream News（Reuters, Bloomberg） | 0.80 |
| Tier 3 | Crypto Media（CoinDesk, CoinTelegraph） | 0.60 |
| Tier 4 | CoinGecko | 0.50 |
| Tier 5 | Anonymous Social（Reddit, X） | 0.30 |

#### 4.2 Dawid-Skene Dynamic Reputation（動態信譽）

Dawid-Skene EM 演算法（ `trust/dawid_skene.py `）根據來源歷史正確率動態更新信譽分數：

- 收集每輪分析中各來源對各 claim 的 stance 標記（bullish/bearish/neutral）

- EM iteration 收斂到每個來源的 error rate matrix（confusion matrix）

- Error rate 轉換為動態信譽乘數（1 − error_estimate）

- 靜態 tier weight × 動態信譽乘數 = 最終 SourceReputation

**離線 fallback： **當 Bedrock 不可用、stance 分類無法線上進行時，Dawid-Skene 從歷史 stance cache（ `trust/stance_cache.py `）讀取標記。若連 cache 都沒有 → 使用靜態 tier weight，不誤報。

### 5. Cross-Source Corroboration（多源佐證）

計算同一主張被幾個 **獨立 **來源佐證：

CorroborationScore = |S_supporting| / (|S_supporting| + α × |S_contrarian|)

- **回音室排除 **：轉發、引述同一來源的不計為獨立佐證。來源間若有明確轉發鏈（如 X 轉發 Reddit、新聞轉述 X），從來源等級判斷獨立性。

- **Stance pair **：兩個來源同時出現 bullish + bearish 主張時，視為「分歧（divergence）」，不是佐證——反而會觸發 `cross_source_signal `標記。

- **α = 0.5 **：反方證據加權——讓分歧更有影響力（不一致的資訊比一致的資訊更有資訊量）。

### 6. Recency Decay（時效衰減）

RecencyDecay(doc) = e^(−λ × hours_since_publish)

| 參數 | 值 | 說明 |
| --- | --- | --- |
| λ | ln(2) / 4 ≈ 0.173 | 加密市場資訊半衰期 ~4 小時 |
| 4h 後 | 0.50 | 可信度剩一半 |
| 12h 後 | 0.125 | 幾乎不具時效價值 |
| 24h 後 | 0.016 | 接近無效 |

若文檔無 `published_at `→ 使用 `fetched_at `。若兩者皆缺 → 給予預設 0.3（中性保守值）。

### 7. Manipulation Penalty（操縱懲罰）

ManipulationPenalty 由 4 項子分數加總（上限 1.0）：

| 子偵測器 | 偵測目標 | 觸發條件 | 子分數 |
| --- | --- | --- | --- |
| **Sentiment Polarization ** | 情緒極化 | 同一來源短期內全部 bullish/bearish，方向單一且強度極端 | 0.25 |
| **Bot Detection ** | Bot 轉發 | 相同文字在短時間內被多個帳號重複發送 | 0.25 |
| **Pump-and-Dump Pattern ** | 拉盤/砸盤 | 極端正面 + 無實質內容（無價格/鏈上支撐）+ 短時間群發 | 0.30 |
| **Contradiction with Objective Data ** | 與客觀資料矛盾 | 社群說 bullish 但 OHLCV/鏈上資料是 bearish | 0.20 |

Bedrock judge（Haiku）輔助評分：對高風險 discourse 做 semantic evaluation，但不完全依賴 LLM——所有偵測器都有 rule-based 基礎。

### 8. Stance 分類（立場分類器）

Stance 分類器（透過 `bedrock.classify_stance() `）使用 AWS Bedrock Converse API + tool-use 將每條 claim 分類為三種立場：

| Stance | 語意 | 範例 |
| --- | --- | --- |
| `bullish ` | 看多 / 正面 | 「BTC 突破 7 萬，機構資金流入加速」 |
| `bearish ` | 看空 / 負面 | 「交易所 BTC 流入量創 3 月新高，潛在賣壓」 |
| `neutral ` | 中性 / 事實描述 | 「BTC 24h 成交量 25B USD」 |

**Stance Cache（ `trust/stance_cache.py `）： **為了避免重複 Bedrock 呼叫（每條 claim 都打一次太貴），stance 分類結果會寫入持久化 cache。相同 content hash 的 claim 可重用前次 stance 結果。Bedrock Haiku 用於 stance（便宜）、Sonnet 用於 narrative（貴重），成本拆分到成本帳本不同的 model key。

### 9. Dawid-Skene EM（動態來源信譽）

Dawid-Skene 是一種無監督 EM 演算法，用於從多個 annotator（來源）的標記中估計每個 annotator 的 error rate。

#### 演算法步驟

- **E-step **：給定當前 error rate 估計，計算每個 claim 的真實立場（ground truth）後驗分布

- **M-step **：給定 ground truth 後驗，更新每個來源的 confusion matrix（P(標記=Y | 真實=X)）

- **收斂 **：迭代至 error rate 變化 < ε（預設 1e-4）

- **動態信譽乘數 **：1 − avg_error_rate（來源在三個 class 上的平均錯誤率）

實作於 `trust/dawid_skene.py `。與 Dawid-Skene 配合的 truth-discovery 評估報告見 `docs/architecture/TRUTH-DISCOVERY-EVALUATION-2026-07-13.md `。

### 10. Conformal Calibration（校準）

TrustForge 的 `calibrated_confidence `使用簡化版分位數校準（quantile calibration）—— **不是 **嚴謹 conformal prediction 的覆蓋率承諾。

**誠實邊界： **W4 Split Conformal Prediction 研究（ `docs/qa/CONFORMAL-FINDING.md `）數學實作完成、JOINT coverage 達標，但代理訊號 pseudo-AUC ≈ 0.49（等同隨機）——誠實負結果，不接進 production。當前 `calibrated_confidence `反映的是 **資訊完整度 **（有多少資訊、多少來源支撐），不是預測準確度。

| 信心等級 | 範圍 | 觸發條件 |
| --- | --- | --- |
| 高 | > 0.75 | 3+ 獨立來源、claims > 10、無操縱訊號 |
| 中 | 0.50 – 0.75 | 2+ 來源、claims > 5 |
| 低 | 0.35 – 0.50 | 1 來源或 claims < 5 |
| 棄權（abstain） | < 0.35 | 去重後獨立來源數 < 2 或 calibrated_confidence < 0.35 |

### 11. 獨特洞察偵測

`trust/insights.py `提供四種洞察類型：

| 洞察類型 | 偵測方法 | 範例 |
| --- | --- | --- |
| **Smart-Money Divergence ** | 社群情緒 bullish 但鏈上大戶 outflow | 「社群同步看多但鯨魚在出貨」 |
| **Manipulation Surge ** | 短期內 manipulation_score 急遽升高 | 「過去 2h 疑似喊單訊息暴增 5 倍」 |
| **Self-Contradiction ** | 同一來源短時間內立場 180 度轉變 | 「CoinDesk 先報 bullish 4h 後改 bearish」 |
| **Source Absence ** | 重要來源（SEC、鏈上）無資料 | 「監管來源近 72h 無更新——資訊缺口警告」 |

### 12. 協同操縱偵測（待實作）

Account 維度的資料累積已前置（ `Evidence.author `），供未來 W3「協同操縱偵測」使用。目前不做任何跨平台關聯、衍生識別運算——不影響任何 trust 分數，不在 UI 顯示。上線前需重新評估 90-day TTL 是否足夠，是否需要主動刪除同步機制。

[← 07 運維手冊 ](07-operations.md)[09 前端架構 → ](09-frontend.md)
TrustForge 技術文件 · 07 信任演算法詳解 · v0.18.5
