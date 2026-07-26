#!/usr/bin/env python3
"""SageMaker Training Job entry point for TrustForge calibrator.

此腳本在 SageMaker Training Job container 內執行。讀取訓練資料，
跑 isotonic regression（純 Python PAV），產出 model.json。

SageMaker 標準路徑：
  - 輸入: /opt/ml/input/data/training/data.jsonl
  - 輸出: /opt/ml/model/model.json
  - 失敗: /opt/ml/output/failure

零第三方依賴——只用 stdlib + 專案內 calibration_model.py。

Ref: Issue #704, #708
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# SageMaker 標準路徑（可透過環境變數覆寫，方便本地測試）
INPUT_DIR = Path(os.getenv("SM_CHANNEL_TRAINING", "/opt/ml/input/data/training"))
OUTPUT_DIR = Path(os.getenv("SM_MODEL_DIR", "/opt/ml/model"))
FAILURE_PATH = Path(os.getenv("SM_OUTPUT_FAILURE", "/opt/ml/output/failure"))

# 最小訓練樣本數（低於此數不足以產出有意義的校準模型）
MIN_SAMPLES = 10


def _write_failure(message: str) -> None:
    """寫入 failure 訊息並 exit(1)。"""
    FAILURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FAILURE_PATH.write_text(message, encoding="utf-8")
    print(f"FAILURE: {message}", file=sys.stderr)
    sys.exit(1)


def _load_training_data(input_dir: Path) -> list[dict]:
    """讀取 JSONL 訓練資料。"""
    data_file = input_dir / "data.jsonl"
    if not data_file.exists():
        _write_failure(f"Training data not found: {data_file}")

    rows: list[dict] = []
    try:
        text = data_file.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as e:
                _write_failure(f"Invalid JSON at line {line_no}: {e}")
            if not isinstance(row, dict):
                _write_failure(f"Line {line_no} is not a JSON object")
            rows.append(row)
    except OSError as e:
        _write_failure(f"Cannot read training data: {e}")

    return rows


def _extract_features(rows: list[dict]) -> tuple[list[float], list[bool]]:
    """從訓練資料中抽取 confidence 和 hit flag。"""
    confidences: list[float] = []
    hits: list[bool] = []

    for i, row in enumerate(rows):
        # 支援兩種欄位名：calibrated_confidence 或 confidence
        conf = row.get("calibrated_confidence")
        if conf is None:
            conf = row.get("confidence")
        if conf is None:
            _write_failure(f"Row {i}: missing 'confidence' or 'calibrated_confidence'")

        hit = row.get("hit")
        if hit is None:
            _write_failure(f"Row {i}: missing 'hit' field")

        try:
            conf_float = float(conf)
        except (TypeError, ValueError):
            _write_failure(f"Row {i}: 'confidence' is not a number: {conf!r}")

        if not (0.0 <= conf_float <= 1.0):
            _write_failure(f"Row {i}: 'confidence' out of range [0,1]: {conf_float}")

        confidences.append(conf_float)
        hits.append(bool(hit))

    return confidences, hits


def main() -> None:
    """Training Job 主流程。"""
    print(f"TrustForge Calibrator Training Script")
    print(f"  INPUT_DIR:  {INPUT_DIR}")
    print(f"  OUTPUT_DIR: {OUTPUT_DIR}")

    # 1. 讀取訓練資料
    rows = _load_training_data(INPUT_DIR)
    print(f"  Loaded {len(rows)} training rows")

    if len(rows) < MIN_SAMPLES:
        _write_failure(
            f"Insufficient training data: {len(rows)} rows < minimum {MIN_SAMPLES}"
        )

    # 2. 抽取 features
    confidences, hits = _extract_features(rows)

    # 3. 跑 isotonic regression（PAV 演算法）
    # 匯入路徑：SageMaker container 內 source_dir 會被加到 sys.path
    # 本地測試時透過 PYTHONPATH 或直接匯入
    try:
        from trustforge.calibration_model import train_isotonic, save_calibration_model
    except ImportError:
        # Fallback：如果 trustforge package 不在 path，嘗試相對路徑
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from trustforge.calibration_model import train_isotonic, save_calibration_model

    points = train_isotonic(confidences, hits)
    print(f"  Isotonic regression produced {len(points)} calibration points")

    if not points:
        _write_failure("Isotonic regression produced empty model (no valid calibration points)")

    # 4. 存到 output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model_path = OUTPUT_DIR / "model.json"
    save_calibration_model(points, model_path, sample_count=len(rows))
    print(f"  Model saved to {model_path}")

    # 5. 驗證產出
    verify = json.loads(model_path.read_text(encoding="utf-8"))
    if "points" not in verify or not isinstance(verify["points"], list):
        _write_failure("Output verification failed: model.json missing 'points'")

    print(f"  Training completed successfully ({len(verify['points'])} points)")


if __name__ == "__main__":
    main()
