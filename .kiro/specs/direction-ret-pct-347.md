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

---

## Design

### 診斷路徑

```
price_facts() → Document(meta={ret_pct: -4.32})
    → extract_claims() → Claim
        → score() → ScoredClaim
            → aggregate(coin='BTC') → TrustedBrief.supporting
                → build_report() → _direction(brief.supporting)
                    → _price_trend_direction(supporting)
                        → 讀 meta["ret_pct"] → 跟閾值比較
```

需要在每一步確認 claim 還在。

### 可能的 bug 點

1. `aggregate()` 的 coin 篩選把 price claim 濾掉了
2. `_price_trend_direction` 的 ret_pct 閾值是 0.03 但 ret_pct 值是 -4.32（單位不一致）
3. supporting 列表裡有 price claim 但 `sc.claim.doc.kind` 對不上

---

## Tasks
- [x] Task 1: 在 pipeline 每步加 print 追蹤
- [x] Task 2: 找到斷點
- [x] Task 3: 修復
- [x] Task 4: 親測 5 幣種
