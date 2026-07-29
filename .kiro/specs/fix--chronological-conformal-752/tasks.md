# 實作任務：時間序列 Chronological Split 與 Leakage 修正

> Issue: #752
> PR: #755 (merged to develop)

## Task 1: 移除 Random Shuffle，實作 Chronological Split

- [x] 移除 `random.shuffle()` / `random.sample()` 呼叫
- [x] `chronological_split()` 改用全域唯一 ISO dates 排序
- [x] 中位數切分：`boundary = len(dates) // 2`
- [x] 同日所有 rows 歸屬同一 partition
- [x] `random_seed` 參數保留但 `del random_seed`（API 相容）

## Task 2: 強化 Temporal Embargo

- [x] Calibration rows 額外篩選：`outcome_utc < held_start_utc`
- [x] 確保 calibration_end < held_out_start（嚴格不等）
- [x] 無法形成嚴格分割 → `DatasetError`

## Task 3: 修正 Backtest 跨幣邊界

- [x] `_chronological_partitions()` 改用所有幣全域日期聯集
- [x] 不以 BTC bars index 作為 boundary 代理
- [x] 確保各幣 held-out 區間完整

## Task 4: 修正 Threshold 與報告

- [x] 移除 `auc_proxy` 與基於它的 promotion check；保留誠實的 `promotion_checks` 研究門檻
- [x] `conformal_threshold()` — nonconformity 分位數
- [x] `_evaluate_split()` — 報 joint_error, abstain_rate, conditional_wrong, accuracy
- [x] `ConformalResult` dataclass 含 `source_families` 計數

## Task 5: 強化 Sample Validation

- [x] `load_samples()` 驗證所有 required fields
- [x] ISO 8601 timezone-aware 驗證
- [x] `outcome_observed_at > as_of` 時序驗證
- [x] `sample_id` 唯一性驗證
- [x] `evidence_strength` ∈ [0,1] finite 驗證
- [x] `source_family` ∈ valid set 驗證

## Task 6: 報告 metadata 完整化

- [x] Split boundaries (calibration_start/end, held_out_start/end)
- [x] Dataset digest (SHA-256)
- [x] Per-family counts
- [x] Per-coin counts

## Task 7: 新增研究文件

- [x] `docs/qa/CONFORMAL-FINDING.md` 記錄方法論與誠實聲明

## Task 8: 新增測試

- [x] `tests/test_conformal_chronological.py`
  - 同日跨幣在同 partition
  - Global boundary 不依賴 BTC-only
  - Calibration outcome < held-out as_of（無 leakage）
  - < 4 unique dates → DatasetError
  - Duplicate sample_id → DatasetError
  - Invalid temporal ordering → DatasetError
  - Invalid source_family → DatasetError
- [x] `tests/test_w4_conformal.py` — fixture 調整
- [x] `tests/test_honest_metrics_cli.py` — fixture 對齊

## Task 9: Review gates

- [x] Named reviewer requested (@nicholaswang941013)
- [x] /codex-review APPROVE
- [x] Eye scan (0/0)
- [x] Full pre-push PASS (4750 backend, 459 frontend, 24/24 QA)
