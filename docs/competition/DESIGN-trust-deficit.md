# 設計文件：信任赤字（Trust Deficit）— 招牌洞察

> 2026 雲湧智生黑客松 · HOYA BIT 命題 · 招牌創意 ①（見 [`CREATIVE-CONCEPTS-ANALYSIS.md`](CREATIVE-CONCEPTS-ANALYSIS.md)）
> 定位：TrustForge 唯一「別隊做不出」的原創市場洞察。零付費資料、騎在既有 pipeline。
> 撰寫：CEO 定案，對照實際 codebase（`src/trustforge/trust/`）2026-07-18

---

## 1. 一句話定義

> **信任赤字 = 市場對某幣的「情緒信心」 − 支撐它的「獨立高信任證據量」。**
> 赤字為正且大 → 樂觀（或恐慌）是借來的，情緒遠超證據；為負 → 證據被低估。

TrustForge **不預測價格**（官方明講不要），而是回答一個更深、只有信任提煉引擎做得出的問題：**「市場的信心，配得上它的證據嗎？」**

---

## 2. 計算式（建在既有真欄位上，非憑空）

兩個分量各正規化到 0–1：

### 2.1 情緒信心 Conviction（市場「叫得多大聲、多一面倒」）
```
Conviction = DirectionalConsensus × VolumeWeight
```
- **DirectionalConsensus**（一面倒程度）= |Σ sentiment_i| / Σ|sentiment_i|，i 跑遍 social/news claims。
  - 用既有 `ScoredClaim` 的 sentiment（`scoring.py` sentiment 權重 0.50 已存在）。全部同向→1；多空對半→0。
- **VolumeWeight** = min(1, log1p(N_raw) / log1p(N_ref))，N_raw = **去重前**原始提及數。
  - 關鍵：Conviction 刻意用「去重前」量 —— 因為市場的「聲量」本來就含回音。

### 2.2 證據品質 EvidenceQuality（去掉回音後，真正的高信任獨立證據）
```
EvidenceQuality = Σ_{獨立且高信任} Evidence.trust_i  /  QualityNorm
```
- **獨立**：用既有 `_independent_source_keys` / `_coordination_template_flags`（union-find）塌縮同文/模板化來源，回音只算一票。
- **高信任**：`Evidence.trust ≥ τ`（τ 建議 0.5，可校準）。`Evidence.trust` 已是四維 TrustScore 產物。
- 跨源佐證（不同 kind：price/onchain/news 互證同一主張）給加權，沿用既有 corroboration。

### 2.3 赤字
```
TrustDeficit = clamp(Conviction − EvidenceQuality, −1, +1)
```
| 區間 | 判讀 |
|------|------|
| ≥ +0.4 | **高信任赤字**：情緒遠超證據，樂觀/恐慌是借來的 |
| +0.15 ~ +0.4 | 中度赤字：留意情緒領先 |
| −0.15 ~ +0.15 | 信心與證據大致相稱 |
| ≤ −0.15 | **信任盈餘**：證據強於市場關注，可能被低估 |

---

## 3. 三態誠實合約（沿用既有 insights 慣例，攸關 30% 主題）

既有 `insights.py` 對「覆蓋不足」一律標 `insufficient`／「無法判定」，**絕不硬湊**（Phase 0 三態誠實合約）。信任赤字必須遵守：

- **N_raw < 最低樣本**（如社群+新聞 < 8 條）→ 輸出 `insufficient`，明說「聲量樣本不足，無法可靠估計情緒信心」。
- **獨立高信任源 < 2** → 輸出「證據面過稀，赤字數字不可靠」，不給數字。
- 冷門幣（BNB/XRP 常見）觸發上述 → **這個空白本身就是限制說明**（命中官方「不確定性與限制說明」評分點），不是失敗。

> ⛔ 禁止：樣本不足時硬給一個好看的赤字數字。誠實的「無法判定」比造假的洞察更對題。

---

## 4. 報告呈現

**報告新增區塊「信任赤字」（第 1 節結論之後、第 2 節依據之前）：**

```
┌─ 信任赤字：+0.53（高） ────────────────────────┐
│  情緒信心   ████████████████░░░░  0.81           │
│  證據品質   █████░░░░░░░░░░░░░░░░  0.28           │
│                                                  │
│  SOL 近兩週有 5,000+ 條看多提及，但去重後獨立、    │
│  高信任的證據只有 12 條。樂觀是借來的。            │
│  → 主結論信心已據此下修（見信心說明）。            │
└──────────────────────────────────────────────────┘
```
- 雙 bar 對比 + 缺口高亮，一眼看懂（免費、純呈現層）。
- 一句白話結論，可回溯到 claim_id。
- **與主結論連動**：高赤字時 `Report.confidence` 應反向下修（誠實：情緒撐起的判斷信心要打折）。

---

## 5. 對應評分項（為什麼這一個功能同時吃多欄分數）

| 評分項 | 信任赤字如何得分 |
|--------|-----------------|
| **30% 主題切合** | 這就是「信任提煉」的字面實作：把情緒聲量與證據品質分離。多源整合（sentiment+trust+獨立性跨 kind）、矛盾/不確定處理（三態）、限制說明（insufficient）一次全中。 |
| **15% 創意** | 原創指標、非表面摘要、非價格預測 —— 評審沒看過別隊這樣答。 |
| **20% 商業** | 交易者最實用的一句話：「別被 5000 條假熱鬧騙了」。直接可行動。 |
| **25% 技術** | **確定性、pipeline 計算、非 LLM 生成** → 同時是反作弊鐵則的活教材（判斷由我方 pipeline 產出）。 |
| **10% 完成** | 以既有 `Insight` 形式輸出，序列化進 report/evidence，穩定。 |

---

## 6. 落地（對照 codebase，估工時）

| 工作項 | 位置 | 估時 | 備註 |
|--------|------|------|------|
| `detect_trust_deficit()` 偵測器 | `trust/insights.py` | 3h | 沿用 `Insight`/`InsightContribution` dataclass 與三態 pattern（同 `detect_smart_money_divergence`） |
| 併入 `detect_insights()` 聚合 | `trust/insights.py` | 0.5h | 加一行呼叫 |
| Conviction/EvidenceQuality 計算 | 重用 `scoring.py` sentiment、`_independent_source_keys`、`Evidence.trust` | 2h | 不新建資料源，組合既有 |
| 主結論信心連動下修 | `agent/orchestrator.py` build_report | 1h | 高赤字 → confidence 打折 |
| 報告雙 bar 呈現 | `web.py` + 報告模板 | 2h | 純前端，中性樣式（比照 W3 info_flags 不用紅旗） |
| 測試（含 insufficient 三態） | `tests/` | 1.5h | 覆蓋冷門幣/低樣本 |
| **合計** | | **~10h** | 全落在 8/1 前預建範圍 |

**資料需求**：零新增。social（Reddit）、news（RSS/CryptoPanic）、price/onchain 免費端點皆為既有連接器。

---

## 7. 已知限制（誠實登錄，也是報告的限制說明素材）
- sentiment 目前偏關鍵詞式，情緒強度非完美 → 校準 τ 與 N_ref 需用樣本調。
- 聲量可被灌水 —— 但這**正是赤字要抓的**：灌水量高、獨立證據低 = 高赤字，機制自洽。
- 「情緒信心」與「證據品質」皆為相對指標，跨幣比較需同口徑正規化。

## 8. 下一步
- CTO 併入 [`BUILD-PLAN.md`](BUILD-PLAN.md) 的 8/1 前預建時間盒（~10h）。
- τ / N_ref / 樣本門檻用官方 OHLCV + 樣本資料先行校準。
