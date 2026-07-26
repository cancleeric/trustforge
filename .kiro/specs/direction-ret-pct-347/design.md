# Design

## 診斷路徑

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

## 可能的 bug 點

1. `aggregate()` 的 coin 篩選把 price claim 濾掉了
2. `_price_trend_direction` 的 ret_pct 閾值是 0.03 但 ret_pct 值是 -4.32（單位不一致）
3. supporting 列表裡有 price claim 但 `sc.claim.doc.kind` 對不上
