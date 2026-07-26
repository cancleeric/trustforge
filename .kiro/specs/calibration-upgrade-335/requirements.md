# Spec：校準升級執行 (#335)

> Issue: #335
> Priority: P0-critical

---

## Requirements（需求）

### R1: Historical Replay 比對
- 輸入：`out/training-data/*.jsonl` 中有方向預測（direction ≠ "不明"）的記錄
- 對每筆，用 `data/{COIN}_daily_ohlcv.csv` 取 date 之後的收盤價
- 比對 horizon：T+1、T+7、T+14
- 方向判定：
  - 預測「中性」→ 實際 |change| < 2% = hit
  - 預測「偏多」→ 實際 change > 0 = hit
  - 預測「偏空」→ 實際 change < 0 = hit
- 輸出：eligible_predictions, hit_rate per horizon

### R2: Calibration Error 計算
- 將 confidence 分 5 bin
- 每 bin：mean_confidence vs empirical_hit_rate
- calibration_error = max |bin_confidence - bin_hit_rate|
- reliability diagram 數據（供前端畫圖）

### R3: 整合到升級流程
- 結果寫入 `out/historical-replay-{coin}.json`（格式與 `calibration.replay_report` 一致）
- `diagnose_hermes.py` 讀取 → 產出 `confidence-calibrator-{horizon}` proposal
- `review_hermes_upgrades.py` 做 LLM 審查

### R4: CLI
- `python -m trustforge.cli calibrate --coin BTC`
- `python -m trustforge.cli calibrate --all`（5 幣全跑）

### R5: 系統模組化
- 核心邏輯在 `src/trustforge/calibration_runner.py`（新檔）
- 不是 script，是可 import 的模組
- daemon 可定時呼叫

---

## 風險

| 風險 | 緩解 |
|------|------|
| 預測全是「中性」→ hit_rate 很高 → error 小 → 不觸發升級 | 這是正確行為（calibration 本來就準就不需要升級）|
| OHLCV 沒有預測日期之後的資料 | 跳過，不計入 eligible |
| confidence 全在同一 bin | 至少要 2 bin 有資料才有意義 |
