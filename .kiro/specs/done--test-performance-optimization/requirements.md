# Requirements: 測試效能優化

## 背景

後端 pytest 全跑 5008 個測試需 ~135 秒（2 分 15 秒），254 個測試檔。
pre-push hook 跑完整輪需要太久，影響開發節奏。

## 目標

- 找出異常慢的測試（>1s/test）並修正
- 找出重複或冗餘的測試合併/移除
- 將全量測試時間從 ~135s 降到 <60s
- 保持覆蓋率 >=75%（目前 83.82%）

## 功能需求

### FR-1: 測試耗時分析
- 用 `--durations=50` 找出最慢的 50 個測試
- 分類慢的原因：I/O wait、重複 setup、不必要的 sleep、過大 fixture

### FR-2: 修正異常慢的測試
- sleep/time.sleep 替換為 mock
- 重複的 DynamoDB/moto setup 改用 session-scoped fixture
- 不必要的網路呼叫加 mock
- parametrize 過多的組合裁減

### FR-3: 測試結構優化
- 合併功能重疊的測試檔
- 移除已失效或重複驗證同一邏輯的測試
- 加上 pytest marks（slow / integration）方便分層跑

### FR-4: 分層執行策略
- `pytest -m "not slow"` 作為快速本地檢查（目標 <30s）
- `pytest` 全量作為 pre-push（目標 <60s）
- 保留覆蓋率閘門 75%

## 非功能需求

- 不降低覆蓋率低於 75%
- 不移除有價值的邊界測試
- 不改變 pre-push hook 的檢查項目
