# Spec：重建 PIT 標籤 (#378)

> Issue: #378
> Priority: P0-critical

---

## Requirements

### R1: 用 OHLCV T+7 outcome 標記 ground truth
- 讀 data/training/{coin}.jsonl 每筆的 date
- 查 data/data/{COIN}_daily_ohlcv.csv 取 date+7 天的 close
- outcome = (close_t7 - close_t0) / close_t0
- > +3% = "bullish", < -3% = "bearish", else = "neutral"
- 寫入 `ground_truth_direction` 和 `outcome_pct` 欄位

### R2: 時間切分標記
- 加 `split` 欄位：前 80% = "train", 後 20% = "val"
- 按 date 排序後切分

### R3: CLI
- `python -m trustforge.cli label-outcomes --horizon 7 --threshold 3`
