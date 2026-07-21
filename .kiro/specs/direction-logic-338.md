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

## Design（設計）

### 修改位置

`src/trustforge/agent/orchestrator.py` 的 `_direction()` 函式。

### 新邏輯

```python
def _direction(supporting: list[ScoredClaim]) -> str:
    """多層方向判定：多源共識 > 價格趨勢 > 不明"""
    
    # Layer 1: 多源 stance 共識
    stance_direction = _stance_consensus_direction(supporting)
    if stance_direction:
        return stance_direction
    
    # Layer 2: OHLCV 價格趨勢
    price_direction = _price_trend_direction(supporting)
    if price_direction:
        return price_direction
    
    return "不明"


def _price_trend_direction(supporting: list[ScoredClaim]) -> str | None:
    """從 OHLCV meta 計算 14 天報酬率方向"""
    # 找所有 price kind 的 claim
    # 提取 meta.close 值，按日期排序
    # 算最近 vs 14天前的報酬率
    # > +3% = "偏多", < -3% = "偏空", else = "中性"


def _stance_consensus_direction(supporting: list[ScoredClaim]) -> str | None:
    """多源 stance 加權方向（需 ≥2 獨立來源）"""
    # 收集 claim.direction == bullish/bearish 的
    # 用 trust_score 加權
    # 判定多數方向
```

### 不改動的

- TrustScore 公式不動（信譽/佐證/時效/操縱）
- 資訊完整度計算不動
- abstain（棄權）邏輯不動

---

## Tasks（任務）

### Task 1: 實作 `_price_trend_direction()`
- 從 supporting claims 的 price documents 提取 close 值
- 計算報酬率
- 回傳方向或 None

### Task 2: 實作 `_stance_consensus_direction()`
- 收集有 direction 的 claims
- 信任加權多數決
- ≥2 獨立來源才有效

### Task 3: 重寫 `_direction()`
- 組合 Layer 1 + Layer 2
- 移除舊的關鍵字匹配

### Task 4: 測試
- tests/test_direction_logic.py
- 含：漲 > 3% → 偏多、跌 > 3% → 偏空、盤整 → 中性
- 含：多源 stance 有效/無效 fallback
- 含：回歸測試確保不破壞其他

### Task 5: 驗證
- 用今天的多源資料跑一次分析，確認不全是中性
- 用歷史 OHLCV 抽樣確認三種方向分佈合理

---

## 風險

| 風險 | 緩解 |
|------|------|
| 改方向判定影響所有分析結果 | 保守閾值（±3%），中間仍是中性 |
| 多源 stance 資料稀缺 | fallback 到價格趨勢，不會比現在差 |
| 回歸風險 | 加測試覆蓋，跑 QA matrix 確認 |
