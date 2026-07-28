# Tasks

## Task 1: 實作 `_price_trend_direction()`
- 從 supporting claims 的 price documents 提取 close 值
- 計算報酬率
- 回傳方向或 None

## Task 2: 實作 `_stance_consensus_direction()`
- 收集有 direction 的 claims
- 信任加權多數決
- ≥2 獨立來源才有效

## Task 3: 重寫 `_direction()`
- 組合 Layer 1 + Layer 2
- 移除舊的關鍵字匹配

## Task 4: 測試
- tests/test_direction_logic.py
- 含：漲 > 3% → 偏多、跌 > 3% → 偏空、盤整 → 中性
- 含：多源 stance 有效/無效 fallback
- 含：回歸測試確保不破壞其他

## Task 5: 驗證
- 用今天的多源資料跑一次分析，確認不全是中性
- 用歷史 OHLCV 抽樣確認三種方向分佈合理
