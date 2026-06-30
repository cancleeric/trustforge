# PLAN-corroboration.md — `_corroboration` 深化設計方案

> 對應 Issue #15（token-overlap 誤判）+ Issue #4（矛盾方向被當佐證）
> 負責人：CPO｜狀態：待 CEO 拍板
> 最後更新：2026-07-01

---

## 一、現況缺陷分析

### 1-1 根本問題：裸 token-overlap 無法區分「通用詞重疊」與「內容相似」

現行 `_corroboration`（scoring.py L139–153）：

```
overlap = |tokens(A) ∩ tokens(B)| / max(1, |tokens(A)|)
if overlap >= 0.4 → B 視為 A 的佐證
```

加密市場分析的文字必然大量使用幣名（btc / 比特幣）、市場通用詞（成交量、交易所、價格、市場），這些詞出現在每一篇分析文字中，對「是否在說同一件事」毫無鑑別力。

### 1-2 具體誤判案例（可重現）

**案例 A：Price vs Volume 假佐證（Issue #15 codex 實證 corr=1.0 異常）**

| | Claim A（價格主張）| Claim B（成交量主張）|
|---|---|---|
| 原文 | BTC 今日成交量創新高，交易所買壓增加，價格上漲 | BTC 成交量萎縮，交易所拋壓加重，價格下跌 |
| tokens | {btc, 今日, 成交量, 創新高, 交易所, 買壓, 增加, 價格, 上漲} | {btc, 成交量, 萎縮, 交易所, 拋壓, 加重, 價格, 下跌} |
| 交集 | {btc, 成交量, 交易所, 價格} — 4 個通用詞 | |
| 現行 overlap | 4 / 9 = **0.44 ≥ 0.4 → 誤判為佐證** | |
| 語意 | 兩者一漲一跌，方向完全相反 | |

這正是「corr=1.0 異常」的根源：多個不同來源只要提到 BTC 相關市場內容，就會互相佐證，讓信任分虛高。

**案例 B：矛盾訊號互相加分（Issue #4）**

| | Claim X | Claim Y |
|---|---|---|
| 原文 | BTC 比特幣看漲，突破阻力 | BTC 比特幣看空，跌破支撐 |
| 交集 tokens | {btc, 比特幣} — 2 個幣名 | |
| 現行 overlap | 2 / 4 = 0.5 → **互相佐證** | |
| 語意 | 一多一空，正面矛盾 | |
| 評審衝擊 | 「矛盾處理」30% 主題項直接失分 | |

**案例 C：回音室已擋但通用詞未擋（現有邏輯不足）**

現行邏輯只過濾「同一 source」，但不同獨立來源說的通用詞仍互相加分。同一則新聞被 3 個獨立媒體轉載改寫，幣名與通用詞完全重疊，`n=3` → `corr = 0.875`，遠超應得分數。

### 1-3 對評分的直接傷害

| 評審項目（共 30% 主題）| 受傷程度 | 原因 |
|---|---|---|
| 矛盾處理 | 高 | 矛盾主張互相加分，無法正確呈現正反方 |
| 信心校準 | 中 | 虛高 corr → 虛高 trust → 信心過度樂觀 |
| 來源獨立性 | 中 | 通用詞讓「獨立佐證」門檻過低，回音室邊界模糊 |

---

## 二、改進演算法設計

### 設計原則重申

1. 判斷邏輯由 pipeline 產出，不交給 LLM 黑箱（反作弊鐵則）
2. 離線也能用：`Claim.direction` 預設 "neutral"，主力邏輯不依賴 direction
3. 不過度工程：黑客松要求「明顯改善 + 簡報能說清楚原理」
4. 保留「獨立來源」精神（現有 source 判重邏輯不動）

### 2-1 特異性加權重疊（主力修改）

**核心想法**：加密市場有一批「域內停用詞」（Domain Stopwords），它們出現在每一篇分析文字中，對「是否在說同一件事」沒有鑑別力。把這批詞從 overlap 計算中移除，讓佐證判斷只依賴「具體/稀有」的內容詞。

**域內停用詞清單（建議初版）**：

```python
DOMAIN_STOP: set[str] = {
    # 幣名（太普遍，任何 BTC 分析都有）
    "btc", "eth", "sol", "bnb", "xrp",
    "bitcoin", "ethereum", "solana",
    "比特幣", "比特", "以太坊", "以太", "幣",
    # 超高頻市場通用詞
    "市場", "價格", "成交量", "交易所", "交易",
    "行情", "數據", "分析", "資料", "報告",
    # 方向性通用詞（過於籠統）
    "漲跌", "漲", "跌",
    # 高頻語法詞（_normalize 已過濾單字，這裡補雙字）
    "目前", "近期", "顯示", "表示", "預計", "預測", "可能",
    "目標", "支撐", "阻力",
}
```

**加權公式**：

```
specificity_overlap(A, B):
    effective_A = {t for t in tokens(A) if t not in DOMAIN_STOP}
    effective_B = {t for t in tokens(B) if t not in DOMAIN_STOP}
    
    if not effective_A:
        return 0.0   # A 全是通用詞，不具備被佐證的內容
    
    return len(effective_A ∩ effective_B) / len(effective_A)

判斷閾值：specificity_overlap >= 0.4 → 視為潛在佐證
```

閾值維持 0.4 不變，但分母已排除通用詞，等效於「具體詞中有 40% 重疊才算佐證」，比裸 overlap 嚴格得多。

**案例 A 重算：**

```
effective_tokens(A) = {今日, 創新高, 買壓, 增加, 上漲}   （過濾：btc/成交量/交易所/價格）
effective_tokens(B) = {萎縮, 拋壓, 加重, 下跌}
intersection = {}  → overlap = 0 / 5 = 0.0  → 不佐證 ✓
```

**案例 B（direction gate 未啟動時）重算：**

```
effective_tokens(X) = {看漲, 突破, 阻力}   （過濾：btc/比特幣）
effective_tokens(Y) = {看空, 跌破, 支撐}
intersection = {}  → overlap = 0 / 3 = 0.0  → 不佐證 ✓
```

（即使 `看漲/看空` 不在停用詞清單，只要沒有共同具體詞，就不會互相佐證。）

### 2-2 方向一致性閘（輔助防線）

**適用條件**：Claim.direction 為 "bullish" 或 "bearish"（LLM 抽取後才有），"neutral" 不擋（離線 demo 預設值）。

```
_direction_compatible(d1, d2) -> bool:
    if "neutral" in (d1, d2):
        return True          # 任一方 neutral → 不擋（離線安全）
    return d1 == d2          # 兩者皆有方向時，方向必須一致

在 _corroboration 迴圈中：
    if not _direction_compatible(target.direction, c.direction):
        continue             # 矛盾方向：略過，不加入 independent_sources
```

**兩道防線的角色分工：**

| 情境 | 主力（停用詞過濾）| 輔助（direction gate）|
|---|---|---|
| 離線 demo（direction=neutral）| 靠停用詞過濾 | 不觸發（pass-through）|
| 線上 LLM 抽取有 direction | 先過濾通用詞 | 再擋矛盾方向 |
| 通用詞少但方向矛盾 | 可能漏網 | 補防 |

### 2-3 完整修改後的 `_corroboration` 邏輯步驟

```
_corroboration(target, all_claims):
1. tt = tokens(target.text) - DOMAIN_STOP
2. if not tt: return 0.0
3. independent_sources = set()
4. for c in all_claims:
     if c.doc.source == target.doc.source: continue     # 同源排除（不變）
     if not _direction_compatible(target.direction, c.direction): continue  # 方向閘（新增）
     ct = tokens(c.text) - DOMAIN_STOP
     overlap = len(tt ∩ ct) / len(tt)
     if overlap >= 0.4:
         independent_sources.add(c.doc.source)
5. n = len(independent_sources)
6. return 1.0 - pow(0.5, n) if n else 0.0              # 飽和公式不變
```

步驟 1、4（停用詞過濾）和步驟 4（direction 閘）是唯一的改動；其餘邏輯不動。

---

## 三、驗收標準與現有測試衝擊

### 3-1 算法層驗收標準（可寫成 pytest 的新 test case）

| # | 情境 | 預期結果 | 舊行為 |
|---|---|---|---|
| V1 | 兩條只共享幣名/通用詞的 BTC 主張（不同 source）| corr = 0.0 | corr > 0（誤判）|
| V2 | 方向相反的 bullish vs bearish 主張（不同 source）| corr = 0.0 | corr > 0（誤判）|
| V3 | 兩條共享具體罕見詞（如「清算瀑布」「ETF 審批」）的主張 | corr > 0 | corr > 0（正確）|
| V4 | direction=neutral 兩條主張 → 方向閘不擋 | 只靠停用詞判斷 | 同上 |
| V5 | effective_tokens 為空（主張文字全是停用詞）| corr = 0.0 | 可能 > 0 |

### 3-2 現有測試衝擊評估

**test_trust_scoring.py**

| 測試 | 衝擊 | 處置建議 |
|---|---|---|
| `test_onchain_outranks_anon_social` (L10) | 無衝擊。依賴 reputation，不依賴 corr。 | 不動 |
| `test_manipulation_language_is_penalised` (L20) | 無衝擊。只看 manipulation 分項。 | 不動 |
| `test_independent_corroboration_raises_trust` (L27) | 低風險。shared text 含「大額」「轉入」「賣壓」「下跌」等具體詞，過濾通用詞後仍有高重疊，預期 corr > 0.5 仍成立。 | 運行驗證；若邊界失敗，調整 shared text 加入更多具體詞 |
| `test_aggregate_splits_supporting_and_contrarian` (L40) | 無衝擊。喊單社群的低 trust 來自 manipulation penalty，不靠 corr。 | 不動 |
| `test_negated_manipulation_not_penalised` (L53) | 無衝擊。只看 `_manipulation_penalty`，不涉及 corr。 | 不動 |
| `test_manipulation_entries_land_in_contrarian` (L69) | 低風險。trust < 0.3 由 manipulation penalty 主導（−0.40 × 1.0 × 1.5 已足夠壓低）。 | 不動 |
| `test_cross_source_corroboration_active` (L91) | **高衝擊。必須更新。** L112–130 的內聯驗證邏輯直接複製了舊 `_normalize`+overlap 邏輯，未過濾停用詞。更新演算法後，此段程式碼與實際行為不一致，會產生誤導性斷言。 | 更新 L112–130 的內聯 token-overlap 邏輯，加入 DOMAIN_STOP 過濾；同時確認 `onchain-btc-inflow` 離線樣本含足夠具體詞以維持 corr > 0.5 斷言 |

**test_report.py**

| 測試 | 衝擊 | 處置建議 |
|---|---|---|
| 所有 6 個測試 | 低風險。報告測試依賴最終 `brief.confidence` 與 `brief.supporting/contrarian` 的存在性，不釘 corr 數值。改善後 corr 降低（消除虛高），confidence 可能略降，但 `test_btc_judgment_is_bearish_from_data` 的「偏空」斷言依賴 pipeline 判斷邏輯，不依賴 corr 絕對值。 | 運行驗證；若 confidence 值改變影響支撐/反方分類，調整 support_threshold 或樣本資料 |

**風險最高的單一測試**：`test_cross_source_corroboration_active` L125–130 的兩個 `assert`（`len >= 2` 和 `{"news","social"} <= kinds`）。這兩個斷言依賴離線樣本的 `onchain-btc-inflow` 文字含有足夠的「非停用詞」具體詞，才能與 news/social 樣本的具體詞重疊。如果離線樣本的 `onchain-btc-inflow` 文字過短或全是通用詞，此斷言可能失敗——需要先 inspect 離線樣本文字再實作。

---

## 四、必做 vs 加分（邊界）

### 必做（黑客松核心差異化，直接影響 30% 主題分）

| 項目 | 說明 | 工程量 |
|---|---|---|
| M1 | 在 `scoring.py` 定義 `DOMAIN_STOP` 常數集合 | 30 分鐘 |
| M2 | `_corroboration` 加入停用詞過濾（修改 token set 計算，兩行） | 30 分鐘 |
| M3 | `_direction_compatible()` helper + 在迴圈加 direction gate | 20 分鐘 |
| M4 | 更新 `test_cross_source_corroboration_active` L112–130 內聯邏輯 | 30 分鐘 |
| M5 | 新增 V1/V2/V3 驗收 test case（具體用例，可貼簡報） | 45 分鐘 |

**總估時：約 2.5 小時**

### 加分（有時間再做，簡報加分但非核心）

| 項目 | 說明 | 工程量 | 風險 |
|---|---|---|---|
| B1 | 半降權清單（`DOMAIN_DISCOUNT`，給 0.3 分量而非完全排除） | 1 hr | 需要調參，可能影響更多測試 |
| B2 | 數值精確比對（共享具體數字如 "46637" 額外加分） | 1.5 hr | 需 regex 數值提取，複雜度增加 |
| B3 | claim_type gate（fact 不接受 opinion 佐證）| 2 hr | 離線 claim_type 全為 "inference"，黑客松中意義有限 |
| B4 | Corroboration 分項可視化（簡報：哪些詞觸發佐證）| 1 hr | 純展示，不影響演算法 |

**建議：B4 值得做**（可在決賽簡報螢幕上實時呈現「哪些具體詞讓兩則消息互相佐證」，強化可解釋性敘事）。

---

## 五、CEO 需拍板的核心取捨

### 取捨 1（主要）：停用詞「完全排除」vs「分數降權」

**選項 A（建議）：完全排除**
- 停用詞從 overlap 計算完全移除（weight = 0）
- 優點：邏輯簡單、可在 30 秒內向評審說清楚（「BTC、成交量這類詞每篇都有，我們只算具體用詞的重疊」）
- 風險：若主張文字非常短且全是通用詞，`effective_tokens` 為空 → corr = 0（算法正確，但顯示出資料品質問題）

**選項 B：分數降權（0.3x）**
- 停用詞以 0.3 倍重量參與計算
- 優點：不會因短文字而直接掉零
- 缺點：多維護一個 `DOMAIN_DISCOUNT` 清單；閾值需重新調參；難以直覺解釋

**CPO 建議選 A**。黑客松場景「可解釋 > 精確」，評審問到演算法時 A 方案 30 秒能說清，B 方案需要 2 分鐘。

---

### 取捨 2（次要）：`test_cross_source_corroboration_active` 的斷言是否放寬

此測試目前斷言 `onchain-btc-inflow` 必須被 news + social 至少 2 種 kind 佐證（corr > 0.5）。改善演算法後，這個斷言是否仍然合理，取決於離線樣本文字的具體程度。

- **保守選項**：維持斷言，先 inspect 離線樣本，確認 `onchain-btc-inflow` 文字含具體詞後才實作。這是 CTO 實作前的必要前置步驟。
- **務實選項**：若樣本文字太短，可調整為 `corr > 0.3`（1 個獨立佐證即可），並補充更有具體詞的樣本文字。

**CPO 建議保守選項**：維持斷言嚴格，先確認樣本再改演算法，避免為了通過測試而降低品質標準。

---

## 六、實作指引摘要（給 CTO）

實作順序：

1. 定義 `DOMAIN_STOP` 常數（可放 scoring.py 頂部，與 `KIND_REPUTATION` 同級）
2. 加 `_direction_compatible()` helper（4 行）
3. 修改 `_corroboration()` 的 token 計算：`tt = _normalize(target.text) - DOMAIN_STOP`，迴圈內同理，加 direction gate
4. 執行現有測試，確認哪些失敗
5. 更新 `test_cross_source_corroboration_active` L112–130 的內聯邏輯（同步加入 DOMAIN_STOP 過濾）
6. 新增 V1/V2/V3 test case

**不應做**：更動 `_normalize()`、`score()`、`DEFAULT_WEIGHTS`、`aggregate()` 或任何報告生成邏輯——本次只改 `_corroboration` 內部，介面不變。

---

## 附錄：關聯文件

- `docs/COMPETITION.md` — 評分標準與反作弊鐵則
- `docs/ARCHITECTURE.md` — 三層管線設計
- `src/trustforge/trust/scoring.py` — 實作目標檔案（L139–153 `_corroboration`）
- `tests/test_trust_scoring.py` — 受衝擊測試（尤其 L91–130）
- GitHub Issue #15 — token-overlap 誤判回報
- GitHub Issue #4 — 矛盾方向佐證（賽後 backlog，本方案提前解決）
