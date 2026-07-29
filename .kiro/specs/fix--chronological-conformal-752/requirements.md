# 需求：時間序列 Chronological Split 與 Leakage 修正

> Issue: #752
> Parent: #749
> Depends on: #750 (sample-contract remediation)
> Labels: research-remediation, conformal, data-integrity
> PR: #755 (merged to develop)

## 背景

`scripts/conformal_on_samples.py` 與 `scripts/backtest_conformal.py` 存在方法論缺陷：

1. **Random shuffle 破壞時序**：原實作對時間序列樣本做隨機打亂後再 split，violating exchangeability 的基本假設前提，且導致 future leakage。
2. **跨幣 BTC-only boundary**：backtest 用 BTC 的日期索引作為所有幣種的 split boundary，ETH/SOL 等可能在 calibration set 出現比 held-out 更晚的日期。
3. **假 auc_proxy/check**：conformal report 中包含無意義的 AUC proxy 檢查。
4. **Signal window leakage**：signal 計算可能使用 t+1 及以後的資料。

## 範圍

修正 `conformal_on_samples.py` 與 `backtest_conformal.py` 的分割邏輯、驗證邏輯與報告指標。

## 功能需求

### FR-1: 移除 Random Shuffle，改用 Global Chronological Split

- 不得對時間序列樣本做 random shuffle。
- Split 依全域唯一 ISO dates 排序後取中位數切分。
- 所有幣種使用相同的全域日期邊界（不以單一幣種的日曆代理）。

### FR-2: 同日隔離保證

- 同一天（`as_of` date 正規化為 UTC date）的所有 rows 必須在同一 partition。
- Calibration 日期嚴格早於 held-out 最小日期。
- Calibration 中 outcome 觀測時間必須嚴格早於 held-out 最早 as_of（防 label leakage）。

### FR-3: 跨幣公正邊界

- `backtest_conformal.py` 的 `_chronological_partitions()` 取所有幣的全域唯一日期聯集。
- 不以 BTC bars index 代理 boundary。
- 各幣在 held-out 區間若有資料，必須完整包含。

### FR-4: Signal Window 嚴格限制

- Signal（evidence_strength）計算只能使用 t 及以前的資料。
- Future 資料只作 outcome label，不進入 features。

### FR-5: 移除假指標，保留誠實研究門檻

- 移除 conformal report 中基於假 `auc_proxy` 的 promotion check。
- 保留 `promotion_checks` 作為研究品質門檻，但只能使用可直接驗證的
  sample、source-family 與 held-out 統計，不得重新引入 AUC proxy。
- 改報：joint error、conditional wrong rate、abstain rate、support、family counts。

### FR-6: Fail-Closed 邊界

- `MIN_UNIQUE_DATES = 4`（最少 4 天才能形成有效 split）。
- Empty/small/malformed dataset → `DatasetError`。
- 無法形成嚴格順序的 non-empty partitions → `DatasetError`。

### FR-7: Report 完整性

- Report 記錄 split boundaries（calibration start/end, held-out start/end）。
- 記錄 cutoff、dataset digest。
- 記錄 per-family 和 per-coin counts。

## 非功能需求

- **NFR-1: Research-only** — 未達標不得 wire 進 production。
- **NFR-2: 確定性** — `random_seed` 參數保留但不使用（API 相容）。
- **NFR-3: 零第三方依賴** — 純 stdlib + math。

## 驗收條件

1. `grep -n "shuffle\|random.sample" scripts/conformal_on_samples.py` 回傳空。
2. 同日跨幣測試 → 同一日所有 rows 在同一 partition。
3. Calibration dates 嚴格 < held-out dates。
4. Calibration outcome timestamps < held-out earliest as_of。
5. Backtest global boundaries 不只依賴 BTC 日曆。
6. 單元 + subprocess CLI tests 全通過。
7. Named reviewer + Eye + /codex-review + full pre-push 通過。
