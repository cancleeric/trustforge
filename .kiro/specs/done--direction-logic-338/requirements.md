# Spec：方向判定邏輯重寫 (#338)

> Issue: #338
> Priority: P0-critical（核心演算法問題）
> References: devlog references.html、docs/architecture/ARCHITECTURE.md

---

## Requirements（需求）

### R1: 價格趨勢方向（取代關鍵字匹配）
- 從 supporting claims 中找到 price kind 的 Document
- 用 meta 中的 OHLCV 資料計算報酬率
- 14 天報酬率 > +3% → 偏多
- 14 天報酬率 < -3% → 偏空
- 中間 → 中性
- 短期（7天）波動率高（> 3%）+ 方向不明 → 中性（高波動但無趨勢）

### R2: 多源 stance 加權方向（有 stance 資料時）
- 讀取 claim.direction（bullish/bearish/neutral）
- 用 claim 的 trust_score 做加權
- bullish 加權和 > bearish 加權和 × 1.3 → 偏多
- bearish 加權和 > bullish 加權和 × 1.3 → 偏空
- 否則 → 中性
- 需要至少 2 個獨立來源有方向才做多數決

### R3: 最終方向決定
- 如果 R2（多源共識）有結果且來源 ≥ 3 → 用 R2
- 否則 fallback 到 R1（價格趨勢）
- 如果 R1 也算不出（沒有足夠價格資料）→ 「不明」

---

## 風險

| 風險 | 緩解 |
|------|------|
| 改方向判定影響所有分析結果 | 保守閾值（±3%），中間仍是中性 |
| 多源 stance 資料稀缺 | fallback 到價格趨勢，不會比現在差 |
| 回歸風險 | 加測試覆蓋，跑 QA matrix 確認 |
