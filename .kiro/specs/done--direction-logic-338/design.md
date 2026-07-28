# Design

## 修改位置

`src/trustforge/agent/orchestrator.py` 的 `_direction()` 函式。

## 新邏輯

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

## 不改動的

- TrustScore 公式不動（信譽/佐證/時效/操縱）
- 資訊完整度計算不動
- abstain（棄權）邏輯不動
