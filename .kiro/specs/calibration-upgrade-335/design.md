# Design

## 資料流

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

## 模組結構

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

## CLI

```python
# cli.py 新增 calibrate 子命令
def cmd_calibrate(args):
    from .calibration_runner import run_calibration
    coins = COIN_POOL if args.all else [args.coin]
    for coin in coins:
        report = run_calibration(coin, data_dir, training_dir)
        write_json(f"out/historical-replay-{coin.lower()}.json", report)
```
