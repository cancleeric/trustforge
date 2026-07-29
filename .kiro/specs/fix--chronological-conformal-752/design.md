# 設計：時間序列 Chronological Split 與 Leakage 修正

> Issue: #752
> PR: #755 (merged to develop)

## 架構決策

### AD-1: Global Unique Dates Chronological Split

```python
def chronological_split(samples: list[dict[str, Any]]) -> Split:
    """Split samples by global ISO date ordering. No randomness."""
    dates = sorted({str(row["_date"]) for row in samples})
    if len(dates) < MIN_UNIQUE_DATES:
        raise DatasetError(f"need at least {MIN_UNIQUE_DATES} unique dates")

    boundary = len(dates) // 2
    calibration_dates = set(dates[:boundary])
    held_dates = set(dates[boundary:])

    held = [row for row in samples if row["_date"] in held_dates]
    held_start_utc = min(row["_as_of_utc"] for row in held)

    # Calibration: date in calibration set AND outcome observed before held-out starts
    calibration = [
        row for row in samples
        if row["_date"] in calibration_dates
        and row["_outcome_utc"] < held_start_utc
    ]

    if not calibration or not held or dates[boundary - 1] >= dates[boundary]:
        raise DatasetError("cannot form strictly ordered non-empty partitions")

    return Split(calibration, held, ...)
```

關鍵保證：
- 同一 `_date` 的所有 rows 在同一 partition（全幣種）
- `calibration_end < held_out_start`（嚴格不等）
- Calibration 中 outcome timestamps 嚴格早於 held-out 最早 as_of（無 label leakage）

### AD-2: Backtest Global Partitions（跨幣）

```python
def _chronological_partitions(
    all_samples: dict[str, list[Sample]]
) -> tuple[list[Sample], list[Sample], str, str]:
    """Global date boundary across ALL coins."""
    all_dates = sorted({s.date for samples in all_samples.values() for s in samples})
    # 不以 BTC 的日期集為代理
    boundary_idx = len(all_dates) // 2
    calib_end = all_dates[boundary_idx - 1]
    held_start = all_dates[boundary_idx]
    ...
```

### AD-3: 移除 Random Seed 使用

```python
def run_conformal(samples, random_seed=42, alpha=ALPHA) -> ConformalResult:
    """random_seed retained for API compat but deliberately unused."""
    del random_seed
    return _evaluate_split(chronological_split(samples), alpha)
```

### AD-4: Conformal Threshold 計算

```python
def conformal_threshold(strengths, correct_flags, alpha=ALPHA) -> float:
    """Nonconformity score quantile from calibration wrongs."""
    wrong = sorted(strengths[i] for i, f in enumerate(correct_flags) if f == 0)
    if not wrong:
        return math.inf
    index = math.ceil((1 - alpha) * (len(wrong) + 1)) - 1
    if index >= len(wrong):
        return math.inf
    return max(0.0, min(1.0, wrong[index]))
```

- 嚴格 `>` 語義（不是 `>=`）用於 held-out 判定。
- Fallback `math.inf` 表示永遠不 abstain（全對時）。

### AD-5: 誠實報告指標

```python
@dataclass(frozen=True)
class ConformalResult:
    tau: float
    calibration_samples: int
    held_out_samples: int
    held_out_abstain: int       # evidence_strength <= tau
    held_out_pass: int          # strength > tau AND correct
    held_out_wrong: int         # strength > tau AND wrong
    joint_error: float          # wrong / total
    abstain_rate: float         # abstain / total
    conditional_wrong: float | None  # wrong / (pass + wrong), None if denominator=0
    accuracy: float | None      # pass / (pass + wrong), None if denominator=0
    source_families: int        # distinct families in dataset
```

### AD-6: Sample Validation（load_samples）

每筆 sample 驗證：
- Required fields 存在
- `as_of` / `outcome_observed_at` 為 timezone-aware ISO 8601
- `outcome_observed_at > as_of`（outcome 在分析後才可觀測）
- `claim_direction` / `outcome_direction` ∈ valid set
- `source_family` ∈ valid set
- `sample_id` 非空 string，不重複
- `evidence_strength` ∈ [0, 1]，finite

任一違反 → `DatasetError`（fail-closed）。

## 測試策略

### 單元測試 `tests/test_conformal_chronological.py`

| 案例 | 驗證 |
|------|------|
| 8 BTC days + 4 ETH days（交錯日期）| 同日跨幣在同 partition、全域邊界 |
| Backtest global boundaries | 不用 BTC-only 日曆 |
| Calibration outcome < held-out as_of | 無 leakage |
| < 4 unique dates | DatasetError |
| Duplicate sample_id | DatasetError |
| outcome_observed_at <= as_of | DatasetError |
| invalid source_family | DatasetError |

### CLI 整合（test_honest_metrics_cli.py）

- subprocess 執行 conformal → 驗證 report JSON 結構

## 影響範圍

- `scripts/conformal_on_samples.py` — 重構 split 邏輯、移除 auc_proxy
- `scripts/backtest_conformal.py` — 全域邊界、embargo 修正
- `docs/qa/CONFORMAL-FINDING.md` — 新增
- `tests/test_conformal_chronological.py` — 新增
- `tests/test_w4_conformal.py` — 調整 fixture
- `tests/test_honest_metrics_cli.py` — 對齊
