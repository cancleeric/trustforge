# 需求：Source Reliability 與 Calibration 誠實指標

> Issue: #751
> Parent: #749
> Depends on: #750 (sample-contract remediation)
> Labels: research-remediation, metrics, data-integrity
> PR: #756 (merged to develop)

## 背景

`scripts/train_source_reliability.py` 與 `src/trustforge/calibration_runner.py` 存在方法論缺陷：

1. **假 AUC proxy**：`max(accuracy, 1-accuracy)` 被標記為 "auc_proxy"，但這不是 ROC AUC。當 accuracy=0.3 時回傳 0.7，誤導使用者以為有 0.7 的辨識力。
2. **Promotion check 不誠實**：基於假 AUC 的 promotion 門檻決策不可信。
3. **Calibration ROC AUC 定義不明**：未明確限定為「confidence 對 correctness 的辨識力」，也未處理 tie。
4. **training_cutoff 非標準日期**：接受非 ISO 格式（如 epoch int）。
5. **Artifact 缺版本/provenance**：無法追溯訓練資料快照。

## 範圍

修正 source trainer 與 calibration runner 的指標定義與 artifact schema，使其誠實、可驗證。

## 功能需求

### FR-1: 移除假 AUC proxy

- 完全移除 `max(accuracy, 1-accuracy)` 邏輯與相關 promotion check。
- Source trainer 只報以下指標：accuracy、balanced_accuracy（適用時）、Brier score、support、Wilson 95% CI。
- `SourceStats` dataclass 不含 `auc_proxy` 欄位。

### FR-2: 誠實 Balanced Accuracy

- 需至少 2 個 observed outcome classes 才計算。
- 單一 class → `balanced_accuracy=None` + `balanced_accuracy_reason` 說明。
- 每個 class 算 recall，取平均。

### FR-3: Calibration ROC AUC — Tie-Aware

- 限定用途：confidence 對 correctness 的辨識力（Mann–Whitney probability）。
- Ties 得半分（`positive == negative → 0.5`）。
- 單一 class（全對或全錯）→ `value=None` + `reason`。
- 回傳結構含 `target` 欄位標明是什麼的 AUC。

### FR-4: Training Cutoff 嚴格化

- 只接受 `YYYY-MM-DD` 格式（exact 10 字元）。
- 非法格式（epoch int、短月份、不存在日期）→ ValueError。
- Cutoff 為 UTC inclusive：`as_of` 正規化到 UTC 後 date <= cutoff 才納入。

### FR-5: Artifact Schema 與 Provenance

- Artifact 有 `schema`/`version`/`training_cutoff_utc`/`cutoff_inclusive`。
- `provenance` 子物件記錄：
  - `input_samples` / `selected_samples` / `excluded_after_cutoff`
  - `labels_validated_at_or_before_cutoff`
  - `label_timestamp_missing` / `label_timestamp_invalid` / `label_temporal_order_invalid`
  - `label_observed_after_cutoff`
  - `input_sha256` / `selected_dataset_sha256`
- `sample_time_range_utc` 記 min/max as_of。
- 舊 artifact 保留但標 `superseded`。

### FR-6: Sample Identity Contract

- 每筆 sample 必須有非空 `sample_id`（string）。
- 重複 `sample_id` → reject。
- `as_of` 與 `outcome_observed_at` 必須可解析為 UTC datetime。
- `outcome_observed_at` <= cutoff 才接受（label 在 cutoff 前已可觀測）。

## 非功能需求

- **NFR-1: 零第三方依賴** — 純 stdlib + math/statistics。
- **NFR-2: 向後相容** — 舊 artifact 讀回不崩（標 superseded）。
- **NFR-3: Deterministic** — 相同輸入 → 相同 artifact（浮點 rounding 一致）。

## 驗收條件

1. `assert not hasattr(stats, "auc_proxy")` — 無假 AUC。
2. Perfect set → AUC=1.0；inverted → AUC=0.0；all ties → AUC=0.5；single class → None+reason。
3. `training_cutoff_utc` 為合法 UTC `YYYY-MM-DD`。
4. Artifact 有完整 provenance。
5. 單元測試 + CLI contract tests 全通過。
6. Named reviewer + Eye + /codex-review + full pre-push 通過。
