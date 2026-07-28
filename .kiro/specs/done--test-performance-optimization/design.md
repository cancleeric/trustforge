# Design: 測試效能優化

## 分析方法

```
1. pytest --durations=50  → 找出 top 50 最慢測試
2. 分類原因：
   - sleep()：grep 所有 time.sleep / asyncio.sleep
   - moto setup：找 @mock_aws / mock_dynamodb scope
   - 大量 parametrize：找 >20 組的 parametrize
   - 重複 fixture：找相同 setup 在多檔出現
3. 逐類修正
```

## 優化策略

### Strategy A: Mock time.sleep
```python
# Before: 實際等待
time.sleep(0.5)

# After: mock 掉
@patch('time.sleep')
def test_foo(mock_sleep):
    ...
```

### Strategy B: Session-scoped moto fixtures
```python
# Before: 每個 test function 重新建 DynamoDB table
@mock_aws
def test_a():
    create_table()
    ...

# After: session-level fixture
@pytest.fixture(scope='session')
def ddb_table():
    with mock_aws():
        create_table()
        yield table
```

### Strategy C: pytest marks 分層
```python
# conftest.py
def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks test as slow (>2s)")
    config.addinivalue_line("markers", "integration: integration tests")

# 在慢測試上加
@pytest.mark.slow
def test_heavy_integration():
    ...
```

### Strategy D: 裁減 parametrize 組合
```python
# Before: 5×5×5 = 125 combinations
@pytest.mark.parametrize("a", range(5))
@pytest.mark.parametrize("b", range(5))
@pytest.mark.parametrize("c", range(5))

# After: 只測邊界 + 代表值
@pytest.mark.parametrize("a,b,c", [
    (0, 0, 0), (4, 4, 4), (2, 0, 4), (0, 4, 2),
])
```

## 風險控制

- 每次修改後跑 `--cov-fail-under=75` 確認覆蓋率
- 合併/移除測試前確認不是唯一覆蓋某路徑的測試
- 保留 git blame 歷史方便追溯
