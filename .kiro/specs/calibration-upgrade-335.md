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

## Design（設計）

### 資料流

```
out/training-data/{coin}.jsonl
    │ 篩選 direction ≠ "不明"
    ▼
[predictions: date, direction, confidence]
    │ + data/{COIN}_daily_ohlcv.csv
    ▼
[comparison: prediction vs actual T+1/T+7/T+14]
    │
    ▼
{eligible_predictions, hit_rate, reliability_bins}
    │
    ▼ 寫入 out/historical-replay-{coin}.json
    │
    ▼ diagnose_hermes.py 讀取
    │
    ▼ 如 error ≥ 0.15 → calibration proposal
```

### 模組結構

```python
# src/trustforge/calibration_runner.py

def run_calibration(coin: str, data_dir: Path, training_dir: Path) -> dict:
    """主函式：讀 training data + OHLCV → 計算 hit_rate + calibration error"""
    predictions = load_predictions(coin, training_dir)
    ohlcv = load_ohlcv(coin, data_dir)
    results = compare_predictions(predictions, ohlcv)
    return format_replay_report(coin, results)

def load_predictions(coin, training_dir) -> list[dict]:
    """從 JSONL 讀取有方向預測的記錄"""

def compare_predictions(predictions, ohlcv) -> dict:
    """比對每筆預測 vs 實際 T+1/T+7/T+14"""

def format_replay_report(coin, results) -> dict:
    """格式化成 calibration.replay_report 相容格式"""
```

### CLI

```python
# cli.py 新增 calibrate 子命令
def cmd_calibrate(args):
    from .calibration_runner import run_calibration
    coins = COIN_POOL if args.all else [args.coin]
    for coin in coins:
        report = run_calibration(coin, data_dir, training_dir)
        write_json(f"out/historical-replay-{coin.lower()}.json", report)
```

---

## Tasks（任務）

### Task 1: `src/trustforge/calibration_runner.py`
- load_predictions()
- compare_predictions()（T+1/T+7/T+14）
- calculate_calibration_error()（5 bins）
- format_replay_report()

### Task 2: CLI `calibrate` 子命令
- `--coin` / `--all` / `--data-dir` / `--training-dir`

### Task 3: 測試
- tests/test_calibration_runner.py
- 含：正確 hit 判定、bin 計算、edge case

### Task 4: 整合驗證
- 跑一次 calibrate → diagnose → 確認 proposal 產出

---

## 風險

| 風險 | 緩解 |
|------|------|
| 預測全是「中性」→ hit_rate 很高 → error 小 → 不觸發升級 | 這是正確行為（calibration 本來就準就不需要升級）|
| OHLCV 沒有預測日期之後的資料 | 跳過，不計入 eligible |
| confidence 全在同一 bin | 至少要 2 bin 有資料才有意義 |
