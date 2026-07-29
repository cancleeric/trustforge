# 設計：Bedrock 驗證腳本 Fail-Closed 修復

> Issue: #863（退件修正）

## 修改範圍

### 1. `scripts/smoke_test_bedrock_extended.py`

#### 1.1 stance label 錯誤 → fail

```python
# Test 2: classify_stance()
test_stance.update({...})
# 新增：label 正確性影響 all_pass
if not test_stance.get("correct", False):
    all_pass = False

# Test 3: classify_stance() contradiction
test_contra.update({...})
if not test_contra.get("correct", False):
    all_pass = False
```

### 2. `scripts/verify_traceability.py`

#### 2.1 claim_id all_traceable 作為失敗條件

```python
# Section B.1
trace_result = verify_claim_id_traceability(report, evidence)
if not trace_result["meets_minimum"]:
    all_pass = False
# 新增
if not trace_result["all_traceable"]:
    print(f"  ✗ claim_id 不可追溯：{trace_result['untraceable_ids'][:5]}")
    all_pass = False
```

#### 2.2 BEDROCK_MODEL_ID 未設定：exit 2（非 exit 0）

```python
# 新增 --allow-skip 參數
allow_skip = "--allow-skip" in sys.argv

# Section B pre-check
if not model_id:
    if allow_skip:
        results["overall"] = "skip (no model_id, --allow-skip)"
        _write_artifact(results)
        return 0
    else:
        results["overall"] = "skip (no model_id)"
        _write_artifact(results)
        print("❌ BEDROCK_MODEL_ID 未設定且未提供 --allow-skip，exit 2", file=sys.stderr)
        return 2  # 明確非成功
```

#### 2.3 降級測試強制失敗判斷

```python
# Section A: 降級驗證
degraded_result = verify_degraded_mode(coin)
if degraded_result["status"] != "success" or not degraded_result.get("pipeline_completed"):
    all_pass = False
# 新增：降級標記缺失 = 偽裝成功 = fail
if degraded_result.get("pipeline_completed") and not degraded_result.get("has_degradation_indication"):
    print(f"  ✗ 降級測試：pipeline 完成但無降級標記（偽裝成功）")
    all_pass = False
```

### 3. `scripts/verify_bedrock.py`

此腳本行為已正確（環境變數缺失 → exit 1）。無需修改。

## 測試策略

新增 `tests/test_verification_scripts_fail_closed.py`：

1. `test_stance_label_wrong_fails` — mock classify_stance 回傳錯誤 label → exit ≠ 0
2. `test_claim_id_untraceable_fails` — 注入不存在 claim_id → exit ≠ 0
3. `test_no_model_id_no_allow_skip_exit2` — 清除 BEDROCK_MODEL_ID + 無 flag → exit 2
4. `test_no_model_id_allow_skip_exit0` — 清除 BEDROCK_MODEL_ID + --allow-skip → exit 0
5. `test_degradation_no_marker_fails` — pipeline 完成但無降級標記 → exit ≠ 0
6. `test_offline_only_pass` — --offline-only 降級測試正常通過

## 回歸風險

- 改為 fail-closed 可能讓 CI 在無 Bedrock 存取時紅燈 → 用 `--allow-skip` 隔離
- verify_traceability exit code 從 0 改 2 → 所有呼叫端需知曉此 breaking change
