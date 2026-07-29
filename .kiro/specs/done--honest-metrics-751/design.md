# 設計：Source Reliability 與 Calibration 誠實指標

> Issue: #751
> PR: #756 (merged to develop)

## 架構決策

### AD-1: 指標正確性優先——移除假 AUC

舊邏輯：
```python
# REMOVED — this is NOT ROC AUC
auc_proxy = max(accuracy, 1 - accuracy)
```

新指標報告結構：
```python
@dataclass(frozen=True)
class SourceStats:
    name: str
    support: int
    correct: int
    accuracy: float
    balanced_accuracy: float | None
    balanced_accuracy_reason: str | None
    brier: float
    wilson_ci_95: tuple[float, float]
    reliability: float           # Shrinkage-adjusted accuracy
    shrinkage_weight: float
```

不含任何 AUC proxy。Promotion check 移除——可信度由 CI 寬度和 support 判斷。

### AD-2: Tie-Aware Confidence-Correctness AUC

在 `calibration_runner.py` 中實作 Mann–Whitney U-statistic：

```python
def confidence_correctness_auc(
    scores: list[float],
    labels: list[bool],
) -> dict[str, Any]:
    """
    Return:
      {"value": float|None, "reason": str|None,
       "target": "confidence_discrimination_of_correctness"}
    """
    positives = [s for s, l in zip(scores, labels) if l]
    negatives = [s for s, l in zip(scores, labels) if not l]
    if not positives or not negatives:
        return {"value": None, "reason": "requires both correct and incorrect",
                "target": "confidence_discrimination_of_correctness"}
    favourable = sum(
        1.0 if p > n else 0.5 if p == n else 0.0
        for p in positives for n in negatives
    )
    return {"value": round(favourable / (len(positives) * len(negatives)), 6),
            "reason": None,
            "target": "confidence_discrimination_of_correctness"}
```

語義明確：這是 P(correct sample 的 confidence > incorrect sample 的 confidence)，含 tie 半分。

### AD-3: Balanced Accuracy 邊界處理

```python
def _balanced_accuracy(samples) -> tuple[float | None, str | None]:
    outcome_classes = sorted({str(s["outcome_direction"]) for s in samples})
    if len(outcome_classes) < 2:
        return None, "requires at least two observed outcome classes"
    recalls = [
        sum(s["claim_direction"] == oc for s in group) / len(group)
        for oc in outcome_classes
        for group in [[s for s in samples if s["outcome_direction"] == oc]]
    ]
    return sum(recalls) / len(recalls), None
```

### AD-4: Artifact Provenance Schema

```json
{
  "schema": "trustforge.source-reputation",
  "version": "2.0.0",
  "training_cutoff_utc": "2026-07-27",
  "cutoff_inclusive": true,
  "sample_time_range_utc": {"min": "...", "max": "..."},
  "provenance": {
    "input_samples": 120,
    "selected_samples": 60,
    "excluded_after_cutoff": 30,
    "labels_validated_at_or_before_cutoff": 60,
    "label_timestamp_missing": 0,
    "label_timestamp_invalid": 0,
    "label_temporal_order_invalid": 0,
    "label_observed_after_cutoff": 30,
    "input_sha256": "...",
    "selected_dataset_sha256": "..."
  },
  "sources": { ... }
}
```

### AD-5: Cutoff 解析嚴格化

```python
def parse_cutoff(value: str) -> date:
    """Exact YYYY-MM-DD, no ambiguity."""
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"cutoff must be YYYY-MM-DD: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"cutoff must be valid YYYY-MM-DD: {value!r}")
```

### AD-6: Label Temporal Validation

- `outcome_observed_at` 必須 > `as_of`（label 在分析後才能觀測）。
- `outcome_observed_at` 的 date <= `training_cutoff`（訓練時 label 已可見）。
- 違反 → 排除並記 provenance。

## 測試策略

### 單元測試 `tests/test_source_reliability_trainer.py`

| 案例 | 驗證 |
|------|------|
| 30 samples, 80% correct | accuracy=0.8, balanced_accuracy=0.8, brier=0.04, Wilson CI 含 0.8 |
| 全為 bullish outcome | balanced_accuracy=None + reason |
| cutoff "2026-07-27" + offset timezone | 正確排除 UTC 28 日 |
| cutoff epoch int / short month | ValueError |
| label_observed 在 cutoff 後 | 排除並計數 |
| perfect / inverted / ties / single class AUC | 1.0 / 0.0 / 0.5 / None |

### 單元測試 `tests/test_calibration_runner.py`

| 案例 | 驗證 |
|------|------|
| confidence_correctness_auc: perfect discrimination | value=1.0 |
| all ties | value=0.5 |
| single class | value=None + reason |
| invalid indexed details | reject |
| ambiguous legacy rows | reject |

### CLI 整合測試 `tests/test_honest_metrics_cli.py`

- subprocess 執行 trainer → 驗證 output artifact schema

## 影響範圍

- `scripts/train_source_reliability.py` — 大幅重構指標計算
- `src/trustforge/calibration_runner.py` — 新增 AUC 函式、reject 邏輯
- `data/model-artifacts/source_reputation_v1.json` — 更新為 v2 schema
- `docs/contracts/historical-sample-contract.md` — 新增 label validation 章節
