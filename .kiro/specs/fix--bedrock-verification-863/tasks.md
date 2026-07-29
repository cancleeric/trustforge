# 實作任務：#863 退件修正

## Task 1: smoke_test_bedrock_extended stance label fail-closed（FR-1）

- [ ] classify_stance test: `correct=false` → `all_pass = False`
- [ ] classify_stance_contradiction test: `correct=false` → `all_pass = False`
- [ ] 確認 exit code 在 label 錯誤時 ≠ 0

## Task 2: verify_traceability claim_id all_traceable 失敗條件（FR-2）

- [ ] Section B.1: 新增 `all_traceable == False` → `all_pass = False`
- [ ] 印出不可追溯的 claim_id（最多 5 條）

## Task 3: verify_traceability BEDROCK_MODEL_ID 未設定 exit code（FR-3）

- [ ] 新增 `--allow-skip` CLI 參數解析
- [ ] 未設定 + 無 --allow-skip → exit 2
- [ ] 未設定 + 有 --allow-skip → exit 0（開發/CI 容許）
- [ ] output JSON 標記 `"status": "skipped"`

## Task 4: verify_traceability 降級測試強制失敗判斷（FR-4）

- [ ] `pipeline_completed=True` 且 `has_degradation_indication=False` → `all_pass = False`
- [ ] 印出明確錯誤訊息「pipeline 完成但無降級標記」

## Task 5: 回歸測試 fail-closed 行為

- [ ] 新增 `tests/test_verification_fail_closed.py`
- [ ] test: stance label wrong → exit ≠ 0
- [ ] test: claim_id untraceable → section fail
- [ ] test: no model_id no allow-skip → exit 2
- [ ] test: no model_id allow-skip → exit 0
- [ ] test: degradation no marker → fail
- [ ] test: offline-only degradation pass

## Task 6: 回歸驗證

- [ ] 既有 pytest suite 通過
- [ ] scripts/verify_traceability.py --offline-only 通過
- [ ] lint / type-check 通過
