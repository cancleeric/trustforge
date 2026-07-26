# Design

```python
# src/trustforge/calibration_model.py（新檔）

def train_isotonic(predictions, actuals) -> list[dict]:
    """純 Python isotonic regression"""

def load_calibration_model(path) -> list[dict] | None:
    """讀取校準模型"""

def apply_calibration(raw_confidence, model) -> float:
    """用模型映射 confidence"""
```
