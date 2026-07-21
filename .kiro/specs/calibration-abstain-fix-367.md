# Spec：校準後 confidence 過低導致永遠棄權 (#367)

> Issue: #367
> Priority: P0-critical

---

## 問題根因

校準模型 `data/model-artifacts/calibration-model.json` 用舊資料（方向全中性）訓練，學到「所有預測都不準」→ isotonic mapping 把 0.5 壓到 0.29 → 永遠觸發 `is_abstain`（門檻 0.50）。

## Requirements

### R1: 刪除錯誤的校準模型
- 移除 `data/model-artifacts/calibration-model.json`
- 讓 `_calibrate_confidence()` fallback 到硬編碼（暫時）
- 等累積足夠正確三態資料後再重訓

### R2: 棄權時仍標記方向趨勢
- `is_abstain=True` 時，direction 仍呼叫 `_direction()` 取得方向
- 報告文字改為：「資料不足以做確信判斷，但價格趨勢指向 {direction}（僅供參考）」
- 前端可顯示方向但標低信心

### R3: 驗證
- 移除模型後 calibrated confidence 回到合理值
- 有方向預測輸出

---

## Design

### R1: 移除模型
```bash
git rm data/model-artifacts/calibration-model.json
```

### R2: 修改 build_report 棄權邏輯
```python
if is_abstain:
    direction = _direction(brief.supporting, all_scored=scored)  # 仍然計算方向
    head = (f"{coin}：資料不足以做確信判斷，"
            f"但價格趨勢指向{direction}（僅供參考，非投資建議）。")
```

---

## Tasks
- [ ] 移除錯誤校準模型
- [ ] 修改棄權邏輯（仍給方向）
- [ ] 測試
- [ ] 親測 5 幣

