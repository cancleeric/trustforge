# 退件修正：Bedrock 驗證腳本 Fail-Closed 修復

> Issue: #863（退件重開）
> 前置 spec: done--bedrock-inference-verification-863
> Labels: ops, fix, verification

## 背景

PR 交付了三支 Bedrock 驗證腳本（`verify_bedrock.py`、`smoke_test_bedrock_extended.py`、`verify_traceability.py`），但審查退件指出驗證器行為不夠嚴格——多處「未驗證」或「驗證失敗」的情況仍以 exit 0 結束，等於把「跳過」誤判為「成功」。

## 退件必修項目

### FR-1: stance smoke test label 錯誤時必須 fail

**問題**：`smoke_test_bedrock_extended.py` 的 classify_stance 測試在 label 錯誤時只記 `correct=false`，不影響 `all_pass`。

**修正**：
- `correct=false` 時設 `all_pass = False`
- exit code 必須反映任何 label 驗證失敗

### FR-2: claim_id 驗證以 all_traceable 為失敗條件

**問題**：`verify_traceability.py` 的 claim_id 驗證只檢查數量（`meets_minimum`），沒有用 `all_traceable` 作為判斷條件。引用不存在的 claim_id 仍可通過。

**修正**：
- `all_traceable == False` 時該 section 算失敗
- 同時保留 `meets_minimum` 檢查（數量 ≥5 且全部可追溯）

### FR-3: 未設 BEDROCK_MODEL_ID 時不得 exit 0

**問題**：`verify_traceability.py` 在線上段落，若 `BEDROCK_MODEL_ID` 未設定，以 exit 0 跳過——被誤判為成功。

**修正**：
- 正式驗收模式不得把 skipped 當 success
- 新增 `--allow-skip` 參數：未提供時 skip = exit 2（明確非成功）
- 提供 `--allow-skip` 時 skip = exit 0（開發/CI 容許跳過）
- 預設行為（無 flag）= fail-closed

### FR-4: 降級測試必須有強制失敗判斷

**問題**：降級測試雖計算 `has_degradation_indication`，但不影響 exit code。

**修正**：
- `has_degradation_indication == False` 時 `all_pass = False`
- Pipeline 完成但無降級標記 = 偽裝成功 = fail
- Pipeline 未完成 = fail（已有此判斷）

### FR-5: 線上真實執行 artifact（blocked-external）

**問題**：尚無真實 Bedrock smoke、成本、耗時、claim_id 溯源及降級 artifact。

**修正**：
- 在有 Bedrock 存取的真實環境執行三支驗證腳本
- 附上 `out/bedrock_smoke_test.json`、`out/bedrock_traceability.json`（去除 credential/token）
- 成功案例需證明至少 5 個真實且可追溯 claim_id
- 失敗案例需誠實降級

**狀態**：blocked-external（需 Bedrock 模型開通）

## 非功能需求

- 修正後的腳本本身須可離線測試（`--offline-only`、`--dry-run`）
- 新增回歸測試驗證 fail-closed 行為
- 任何「跳過」狀態必須在 output JSON 中標記為 `"status": "skipped"`，不得標 `"pass"`

## 驗收條件

- [ ] stance label 錯誤 → exit code ≠ 0
- [ ] claim_id 引用不存在的 id → exit code ≠ 0
- [ ] BEDROCK_MODEL_ID 未設定且無 --allow-skip → exit code = 2
- [ ] 降級測試 pipeline 完成但無降級標記 → exit code ≠ 0
- [ ] --offline-only 降級測試通過
- [ ] 回歸測試覆蓋所有 fail-closed 行為
- [ ] 真實 Bedrock artifact 附上（blocked-external 時可延後）
