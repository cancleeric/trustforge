# Design

修改 `scoring.py` 第 1441 行：
```python
def score(..., dynamic_reputation: bool = True, ...)  # 從 False 改 True
```

確認 `em_source_reliability` 的 `max_iter` 和 `_reputation_floor` 合理。
