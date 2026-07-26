# Spec：多源 Stance 加權方向 Layer 2 (#342)

> Issue: #342
> Depends on: #338 Layer 1 ✅

---

## Requirements

### R1: stance 收集
- 從 supporting claims 收集有 `claim.direction` 為 bullish/bearish 的
- 排除 neutral 和空值

### R2: 信任加權多數決
- bullish_weight = sum(trust_score for bullish claims)
- bearish_weight = sum(trust_score for bearish claims)
- bullish > bearish × 1.3 → "偏多"
- bearish > bullish × 1.3 → "偏空"
- 否則 → 維持 Layer 1 結果

### R3: 獨立來源門檻
- 需 ≥2 獨立來源有方向才做 Layer 2
- 不足時 fallback 到 Layer 1
