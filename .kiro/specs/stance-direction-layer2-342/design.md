# Design

在 `orchestrator.py` 新增：
```python
def _stance_consensus_direction(supporting: list[ScoredClaim]) -> str | None:
    # 收集 bullish/bearish claims
    # 加權
    # 判定
```

修改 `_direction()`：
```python
def _direction(supporting):
    stance_dir = _stance_consensus_direction(supporting)
    if stance_dir:
        return stance_dir
    return _price_trend_direction(supporting) or "不明"
```
