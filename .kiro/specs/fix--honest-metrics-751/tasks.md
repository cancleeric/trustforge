# 實作任務：Source Reliability 與 Calibration 誠實指標

> Issue: #751
> PR: #756 (merged to develop)
> Depends on: #750

## Task 1: 移除假 AUC proxy 與 promotion check

- [x] 搜尋並移除 `max(accuracy, 1-accuracy)` 及 `auc_proxy` 欄位
- [x] 移除基於 auc_proxy 的 promotion gate 邏輯
- [x] 更新 `SourceStats` dataclass 為誠實指標集

## Task 2: 實作正確的指標計算

- [x] `compute_source_stats()` 回報 accuracy、balanced_accuracy、brier、support、Wilson CI
- [x] `_balanced_accuracy()` — 單一 class → None + reason
- [x] `wilson_interval(k, n)` — 95% Wilson score interval
- [x] reliability = shrinkage-adjusted accuracy（shrinkage_k=100, target=0.50）

## Task 3: 實作 Tie-Aware Confidence-Correctness AUC

- [x] `confidence_correctness_auc(scores, labels)` in `calibration_runner.py`
- [x] Mann–Whitney U-statistic：tie 得半分
- [x] 單一 class → `{"value": None, "reason": ..., "target": ...}`
- [x] 回傳結構含 `target` 明確標注用途

## Task 4: 強化 Cutoff 與 Label Temporal 驗證

- [x] `parse_cutoff(value)` — 嚴格 YYYY-MM-DD，reject epoch/short/invalid
- [x] `parse_as_of(value)` — ISO 8601 → UTC datetime
- [x] `parse_outcome_observed_at(value)` — 驗證時序合理性
- [x] Label temporal ordering：outcome_observed_at > as_of
- [x] Label visibility：outcome_observed_at.date() <= cutoff
- [x] 違反 → provenance counters 記錄

## Task 5: 實作 Artifact Provenance Schema

- [x] `build_artifact()` 產出 schema=`trustforge.source-reputation`, version=`2.0.0`
- [x] `provenance` 子物件完整記錄 input/selected/excluded/label counters
- [x] `input_sha256` + `selected_dataset_sha256`
- [x] `sample_time_range_utc` (min/max)
- [x] 更新 `data/model-artifacts/source_reputation_v1.json` 為新 schema

## Task 6: Sample Identity 強化

- [x] Validate `sample_id` 非空 string，重複 → reject
- [x] Reject ambiguous legacy rows（缺 sample_id 或型別不符）
- [x] Reject invalid indexed details

## Task 7: 更新 Contract 文件

- [x] `docs/contracts/historical-sample-contract.md` 新增 label validation 章節
- [x] 記錄 training_cutoff 語義與 temporal ordering 要求

## Task 8: 新增測試

- [x] `tests/test_source_reliability_trainer.py`
  - Honest metrics 無 auc_proxy
  - Balanced accuracy null for single class
  - Cutoff UTC inclusive with timezone offset
  - Cutoff rejects non-date values
  - Label observation temporal validation
  - Full artifact provenance
- [x] `tests/test_calibration_runner.py`
  - confidence_correctness_auc: perfect / inverted / ties / single class
  - Reject invalid indexed details
  - Reject ambiguous legacy rows
- [x] `tests/test_honest_metrics_cli.py`
  - Subprocess contract test

## Task 9: Review gates

- [x] Named reviewer requested (@nicholaswang941013)
- [x] /codex-review APPROVE（7 rounds）
- [x] Eye scan (0/0)
- [x] Full pre-push PASS (4785 backend, 459 frontend, 24/24 QA)
