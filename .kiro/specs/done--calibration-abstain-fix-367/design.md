# Design

## R1: 移除模型
```bash
git rm data/model-artifacts/calibration-model.json
```

## R2: 修改 build_report 棄權邏輯
```python
if is_abstain:
    direction = _direction(brief.supporting, all_scored=scored)  # 仍然計算方向
    head = (f"{coin}：資料不足以做確信判斷，"
            f"但價格趨勢指向{direction}（僅供參考，非投資建議）。")
```
