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

---

## Design

```python
# src/trustforge/outcome_labeler.py
def label_outcomes(training_dir, ohlcv_dir, horizon=7, threshold=0.03):
    for coin_file in training_dir.glob("*.jsonl"):
        bars = load_ohlcv(coin, ohlcv_dir)
        date_to_close = {b.date: b.close for b in bars}
        
        entries = [json.loads(line) for line in coin_file]
        entries.sort(key=lambda e: e["date"])
        
        split_idx = int(len(entries) * 0.8)
        
        for i, entry in enumerate(entries):
            t0_close = date_to_close.get(entry["date"])
            t7_date = (date.fromisoformat(entry["date"]) + timedelta(days=horizon)).isoformat()
            t7_close = date_to_close.get(t7_date)
            
            if t0_close and t7_close:
                pct = (t7_close - t0_close) / t0_close
                entry["outcome_pct"] = round(pct * 100, 2)
                entry["ground_truth_direction"] = "bullish" if pct > threshold else "bearish" if pct < -threshold else "neutral"
            
            entry["split"] = "train" if i < split_idx else "val"
        
        # 寫回
        coin_file.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")
```

---

## Tasks
- [ ] outcome_labeler.py
- [ ] CLI 子命令
- [ ] 執行標記
- [ ] 驗證分佈
