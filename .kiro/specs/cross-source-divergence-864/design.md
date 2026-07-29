# 設計：跨源分歧偵測與新聞信任校準

> Issue: #864

## 架構決策

### AD-0: 核心問題——Jaccard 詞重疊不足以偵測語意分歧

**現狀分析**：

`corroborate()` 的第一道閘門是 Jaccard token overlap ≥ 0.4（去除 DOMAIN_STOP 後）。這對「同議題同措辭」有效，但跨源分歧的典型場景是：

- 來源 A（客觀）：「BTC 算力創新高 活躍地址數上升」（bullish）
- 來源 B（新聞）：「分析師警告 市場超買 可能回調」（bearish）

這兩筆的 token 交集幾乎為零（談的面向不同），Jaccard 門檻不會通過 → corroboration 不加分 → 新聞 claim trust 停在 ~0.475（低於 0.5 門檻）→ **分歧偵測根本不會觸發**。

同時 `_detect_stance_pairs` 的進入條件也要求候選對都屬 `_SENTIMENT_KINDS`——它只在**情緒類內部**找矛盾配對，不做客觀 vs 情緒的跨類比對。

**結論**：「客觀 bullish vs 情緒 bearish」的分歧偵測**不依賴文字相似度**，而是依賴：
1. 各類 claim 各自達到 trust ≥ 0.5 門檻（靠各自的 kind_reputation + 自身類內佐證）
2. 聚合投票後客觀面與情緒面主導方向相反

所以 fixture 設計的關鍵不是讓兩筆跨類 claim 文字相似，而是確保**情緒類內部有足夠佐證**推升 trust 過門檻。

### AD-1: 不動分數公式，只動 fixture 可見性

本單**不修改**信任評分引擎（Layer 2）的公式或權重。校準策略：

1. 建立固定 fixture（已知時間戳、已知佐證/操縱旗標）。
2. 斷言 fixture 在完整 scoring pipeline 下的 trust 分布。
3. 若分布不符預期，調整的是 **fixture 輸入條件**（佐證數、時效、操縱旗標），而非 KIND_REPUTATION 固定值。
4. 透過 fixture 固定「在什麼條件下新聞 claim 能突破 0.5 門檻」的知識。

理由：
- 符合驗收「不應只提高新聞類別的固定信譽分；需以實際 scoring 組成和 fixture 驗證校準結果」。
- 公式已有多輪對抗審校準，動權重影響面太大。
- fixture 本身即為校準結果的文件化。

### AD-2: Fixture 設計策略——端到端 scoring pipeline

fixture 不是繞過 scoring 直接手工指定 trust，而是：

```
Document[] (固定內容/時間戳/source/kind)
  → extract_claims()
  → score(claims, now=固定時間)
  → ScoredClaim[] (含 trust、components)
  → detect_cross_source_signal(scored)
  → 斷言 result
```

這確保 fixture 驗證的是**完整 pipeline** 下的行為，不是隔離測試。

**但同時保留 hybrid fixture**：若端到端 pipeline 因 `extract_claims`（LLM 依賴）在 offline 模式無法穩定產出 claim，則使用「手工構造 ScoredClaim + 走 `detect_cross_source_signal`」的 hybrid 方式——這仍然是在驗證分歧偵測邏輯本身，只是跳過 claim 抽取步驟。

### AD-3: 新聞 claim 信任分組成分析

根據現有公式，新聞 claim 的 trust 組成：

```
trust = 0.50 × source_reputation(kind="news")    # = 0.50 × 0.65 = 0.325
      + 0.25 × corroboration                       # 0.0–1.0，取決於跨源佐證
      + 0.15 × recency_decay                       # 1.0（新鮮）→ 0.0（過期）
      − 0.40 × manipulation_penalty                # 0.0（無嫌疑）→ ~0.5（命中）
```

**關鍵觀察**：
- 純新聞（無佐證、完美時效、無操縱）：0.325 + 0.0 + 0.15 − 0.0 = **0.475** < 0.5 門檻
- 有 1 筆佐證（corr ≈ 0.3）：0.325 + 0.075 + 0.15 = **0.55** > 0.5
- 有操縱命中（penalty ≈ 0.2）：0.325 + 0.0 + 0.15 − 0.08 = **0.395** < 0.5

結論：新聞 claim 要達 0.5 門檻（進入 `detect_cross_source_signal` eligible），**至少需要一筆跨源佐證**或 meta.reputation 加持。此為設計意圖（非 bug）——防止單一孤立新聞主導分歧判斷。

### AD-4: 佐證（corroboration）門檻分析——Jaccard 0.4 的限制與緩解

**限制**：corroboration 的第一道門是 Jaccard token overlap ≥ 0.4。這代表：
- 兩筆 claim 需要有 40%+ 的非 DOMAIN_STOP 詞彙重疊才會觸發佐證加分。
- 情緒面要互為佐證推升 trust，**文字必須有足夠相似**（同議題、同用詞）。

**緩解策略（fixture 設計）**：

```python
# ✅ 能觸發佐證（重疊詞：超買、回調、下跌、修正）
doc_news   = "分析師 警告 BTC 超買 可能 回調 下跌 修正"
doc_social = "BTC 超買 預計 回調 下跌 修正 恐慌"

# ❌ 不會觸發佐證（完全不同措辭）
doc_news   = "分析師 認為 估值過高 風險加劇"
doc_social = "BTC 超買 準備 回調 下跌"
```

fixture 必須刻意使用重疊關鍵詞，模擬「同議題同方向的不同來源」報導——這在真實世界也合理（同議題報導用詞有自然重疊）。

**未來改善方向**（不在本 issue 範圍）：
- 使用 embedding 相似度替代 Jaccard 做佐證判定（需引入向量模型，影響面大）
- 擴大 `require_stance=True` 的覆蓋範圍（需更多 Bedrock 呼叫預算）

### AD-5: fixture 分歧觸發場景

構造穩定的分歧觸發 fixture：

```python
# 客觀面：binance 價格 bullish（trust 高：kind_rep=0.95，不需佐證即過 0.5）
doc_price = Document(id="p1", kind="price", source="binance",
                     text="BTC 突破 7 萬美金 新高 量能放大", ts=now)
# 客觀面：glassnode 鏈上 bullish
doc_onchain = Document(id="o1", kind="onchain", source="glassnode",
                       text="BTC 活躍地址數 創新高 鏈上轉帳量 上升", ts=now)
# 情緒面：coindesk 新聞 bearish（需與 social 互為佐證才能過 0.5）
doc_news = Document(id="n1", kind="news", source="coindesk",
                    text="分析師 警告 BTC 超買 可能 回調 下跌 修正", ts=now)
# 情緒面：twitter 社群 bearish（與 news 文字重疊 → corr 加分）
doc_social = Document(id="s1", kind="social", source="crypto_twitter",
                      text="BTC 超買 預計 回調 下跌 修正", ts=now)
```

**佐證觸發分析**：
- news token（去 DOMAIN_STOP）：{分析師, 警告, 超買, 回調, 修正}（5 個）
- social token（去 DOMAIN_STOP）：{超買, 回調, 修正, 恐慌}（取決於 DOMAIN_STOP 列表）
- 交集 / news_tokens：{超買, 回調, 修正} / 5 = 0.6 ≥ 0.4 ✓ → 佐證觸發
- 方向一致（both bearish）→ `_direction_compatible` ✓
- 不同來源（coindesk ≠ crypto_twitter）✓

→ news claim corr 加分 → trust 過 0.5 → 進入 eligible → 分歧偵測可觸發

客觀面 2 source（binance, glassnode）+ 情緒面 1+ source（coindesk）= 合計 ≥ 2 ✓

### AD-6: 未觸發診斷——測試層級的斷言策略

`detect_cross_source_signal` 回 None 時不附帶診斷資訊（不改簽章），改由**測試本身**解釋原因：

```python
def test_no_trigger_due_to_low_trust():
    """全部 trust < 0.5 → eligible 為空 → None。"""
    scored = [_sc("c1", "news", "coindesk", "bearish", 0.4), ...]
    # 先斷言 eligible 確實為空
    eligible = [sc for sc in scored if sc.trust >= 0.5]
    assert len(eligible) == 0, "前置條件：全部 trust < 0.5"
    # 再斷言結果為 None
    assert detect_cross_source_signal(scored) is None
```

理由：診斷資訊是測試時的開發者工具，不需暴露在 runtime API 中（增加回傳複雜度/相容性負擔）。

### AD-7: 來源正規化——複用既有 `_canonical_source`

不新增正規化邏輯。`_normalize_source_key` 已委託 `_canonical_source`（issue #72 收口），涵蓋：
- `strip().casefold()` — 大小寫 + 前後空白
- 別名映射（`coindesk.com` → `coindesk`、`twitter` → `x`）

fixture 中刻意構造大小寫/空白/別名變體，斷言 `_independent_source_keys` 正確收斂：

```python
def test_source_normalization_in_divergence():
    """同來源大小寫/格式變體不膨脹獨立來源計數。"""
    scored = [
        _sc("c1", "price",  "Binance",    "bullish", 0.9),
        _sc("c2", "news",   "CoinDesk",   "bearish", 0.6),
        _sc("c3", "news",   " coindesk ", "bearish", 0.55),  # 同源變體
    ]
    result = detect_cross_source_signal(scored)
    # 情緒面只有 1 個獨立來源（coindesk），合計 2（binance + coindesk）→ 可觸發
    assert result is not None
    assert result["sentiment_source_count"] == 1  # 單一來源徽章
```

## 新增/修改模組

### `tests/test_cross_source_divergence_calibration.py`（新增）

完整端到端校準測試檔：

```python
"""#864：跨源分歧偵測觸發校準 + 新聞信任分布驗證。

使用固定 fixture（已知文字/時間戳/source/kind），通過完整 scoring pipeline
驗證分歧/共識/未觸發行為。不依賴即時新聞、不打 Bedrock。
"""
import time
from trustforge.ingestion.base import Document
from trustforge.trust.scoring import extract_claims, score, aggregate, ScoredClaim
from trustforge.agent.orchestrator import detect_cross_source_signal, _independent_source_keys

NOW = 1_750_000_000.0  # 固定時間戳

class TestNewsTrustDistribution:
    """FR-2: 新聞 claim 信任分在完整 pipeline 下的分布。"""

    def test_solo_news_claim_below_threshold(self):
        """單獨新聞 claim（無佐證）trust < 0.5。"""

    def test_news_with_corroboration_above_threshold(self):
        """有跨源佐證的新聞 claim trust ≥ 0.5。"""

    def test_news_with_manipulation_flag_penalty(self):
        """操縱關鍵詞命中時 trust 顯著下降。"""

    def test_news_recency_decay(self):
        """過期新聞（超過半衰期）trust 衰減。"""

class TestDivergenceFixture:
    """FR-1: 固定 fixture 分歧案例，穩定觸發。"""

    def test_price_bullish_vs_news_bearish_divergence(self):
        """客觀(price) bullish + 情緒(news) bearish → divergence。"""

    def test_onchain_bearish_vs_social_bullish_divergence(self):
        """客觀(onchain) bearish + 情緒(social) bullish → divergence。"""

class TestConsensusFixture:
    """FR-1: 固定 fixture 共識案例。"""

    def test_price_and_news_both_bullish_consensus(self):
        """客觀 + 情緒同向 → consensus。"""

class TestNotTriggered:
    """FR-6: 未觸發診斷。"""

    def test_no_trigger_missing_sentiment(self):
        """缺情緒類 → None（原因：_SENTIMENT_KINDS 無 eligible）。"""

    def test_no_trigger_low_trust(self):
        """全低 trust → None（原因：eligible 為空）。"""

    def test_no_trigger_neutral_dominant(self):
        """兩類都有但主導為 neutral → None。"""

    def test_no_trigger_same_source_inflated(self):
        """同來源大小寫變體不膨脹 → 獨立來源不足 → None。"""

class TestSourceNormalization:
    """FR-3: 來源正規化不變量。"""

    def test_case_whitespace_variants_collapse(self):
        """大小寫/空白變體收斂為同一源。"""

    def test_alias_variants_collapse(self):
        """已知別名（coindesk.com → coindesk）收斂。"""

    def test_truly_distinct_sources_preserved(self):
        """不同來源不被過度合併。"""

class TestExplainability:
    """FR-4: 結果可追溯性。"""

    def test_supporting_claim_ids_present(self):
        """divergence 結果包含 supporting_claim_ids。"""

    def test_claim_ids_traceable_to_source(self):
        """每個 claim_id 可追回 source/kind/direction。"""
```

### 既有測試無修改

- `tests/test_cross_source_signal.py`（T1–T8）— 不動、全綠。
- `tests/test_source_dedup_invariant.py` — 不動、全綠。

### `src/trustforge/agent/orchestrator.py`

本輪**不修改** `detect_cross_source_signal` 的邏輯（已有完整實作）。只可能：
- 補充 docstring 中關於門檻條件的文件化（若目前不夠明確）。

### `src/trustforge/trust/scoring.py`

本輪**不修改**。只透過 fixture 觀察其行為。

## 測試策略

| 測試類型 | 檔案 | 目的 |
|----------|------|------|
| 端到端校準 | `test_cross_source_divergence_calibration.py` | 完整 pipeline fixture 驗證 |
| 既有回歸 | `test_cross_source_signal.py` | T1–T8 全綠 |
| 來源不變量 | `test_source_dedup_invariant.py` | source dedup 全綠 |

所有新測試：
- 使用固定時間戳（`NOW = 1_750_000_000.0`）
- 使用 `BedrockClient(offline=True)` 或不需 client
- 不依賴網路/即時資料
- 可在 CI 穩定重現

## 安全考量

- 不引入新的 Bedrock 呼叫（不影響時間預算）
- 不修改信任公式權重（不影響既有所有報告的分數）
- 新增程式碼為純測試檔，不影響 production code path
- fixture 文字不含真實個人資料或敏感資訊

## 風險與緩解

| 風險 | 緩解 |
|------|------|
| 新聞 claim 在 fixture 中 trust 不穩定 | 選用高度確定性的文字（方向詞明確、無歧義） |
| fixture 與真實資料行為差異大 | fixture 只用公式可預期的輸入組合 |
| `_canonical_source` 別名不完整 | 本輪只驗證已有別名；完整別名見 follow-up #72 |
