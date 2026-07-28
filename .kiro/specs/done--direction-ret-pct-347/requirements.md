# Spec：方向判定讀不到 ret_pct (#347)

> Issue: #347
> Priority: P0-critical

---

## Requirements

### R1: 診斷根因
- 追蹤 `price-BTC-ret` claim（有 meta.ret_pct）在 pipeline 中的路徑
- 確認 `aggregate()` 是否把它放進 `brief.supporting`
- 確認 `build_report()` 呼叫 `_direction(brief.supporting)` 時 supporting 裡有沒有這筆

### R2: 修復
- 確保 price claims with ret_pct 能到達 `_direction()` 的 supporting 參數
- 確認 ret_pct 單位（-4.32 是百分比）跟閾值（±3%）對齊
  - ret_pct=-4.32 → 要跟 ±3 比（不是 ±0.03）
  - 或轉換：ret_pct / 100 跟 ±0.03 比

### R3: 驗收
- BTC ret_pct=-4.32 → 偏空
- ETH/SOL/BNB/XRP 各跑一次，確認有方向分佈
- 不全是「不明」
