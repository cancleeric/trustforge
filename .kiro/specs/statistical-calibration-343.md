# Spec：統計校準取代硬編碼查表 (#343)

> Issue: #343
> Depends on: #338 ✅, #335 ✅

---

## Requirements

### R1: 校準模型學習
- 從 training-data JSONL + OHLCV 建立 confidence → hit_rate 映射
- 用 isotonic regression（單調遞增保證）
- 純 Python 實作，不引入 sklearn

### R2: 模型持久化
- 存入 `out/model-artifacts/calibration-model.json`
- 格式：`{points: [{confidence: x, hit_rate: y}, ...], trained_at, sample_count}`

### R3: 生產整合
- `_calibrate_confidence()` 改為：有模型 → 用模型映射，無模型 → fallback 硬編碼
- CLI: `python -m trustforge.cli train-calibration`

### R4: 可攜性
- `export-model` / `import-model` 支援 calibration model
- 新環境載入即可用

---

## Design

```python
# src/trustforge/calibration_model.py（新檔）

def train_isotonic(predictions, actuals) -> list[dict]:
    """純 Python isotonic regression"""

def load_calibration_model(path) -> list[dict] | None:
    """讀取校準模型"""

def apply_calibration(raw_confidence, model) -> float:
    """用模型映射 confidence"""
```

---

## Tasks
- [x] Task 1: isotonic regression 純 Python 實作
- [x] Task 2: train CLI 子命令
- [x] Task 3: `_calibrate_confidence()` 整合
- [x] Task 4: export/import
- [x] Task 5: 測試
