"""Tests for SageMaker training script (#704, #708).

用 tmp_path 模擬 SageMaker /opt/ml/ 目錄結構，本地驗證 training script。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sagemaker_train_calibrator.py"
SRC_PATH = Path(__file__).resolve().parent.parent / "src"


def _run_training(
    input_dir: Path,
    output_dir: Path,
    failure_path: Path,
    *,
    expect_success: bool = True,
) -> subprocess.CompletedProcess:
    """執行 training script，用環境變數覆寫 SageMaker 路徑。"""
    env = {
        "SM_CHANNEL_TRAINING": str(input_dir),
        "SM_MODEL_DIR": str(output_dir),
        "SM_OUTPUT_FAILURE": str(failure_path),
        "PYTHONPATH": str(SRC_PATH),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if expect_success:
        assert result.returncode == 0, f"Script failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    else:
        assert result.returncode != 0, f"Script should have failed:\nstdout: {result.stdout}"

    return result


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    """寫 JSONL 到指定路徑。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# 成功路徑
# ═══════════════════════════════════════════════════════════════════════════════


class TestTrainingSuccess:
    def test_basic_training(self, tmp_path):
        """正常訓練：產出 model.json 且可被 load_calibration_model 讀取。"""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "model"
        failure_path = tmp_path / "output" / "failure"

        # 產生訓練資料（>= 10 rows）
        rows = [
            {"confidence": 0.1 * i, "hit": i >= 5}
            for i in range(1, 11)
        ]
        _write_jsonl(input_dir / "data.jsonl", rows)

        result = _run_training(input_dir, output_dir, failure_path)

        # 驗證 model.json 產出
        model_path = output_dir / "model.json"
        assert model_path.exists()

        data = json.loads(model_path.read_text())
        assert "points" in data
        assert isinstance(data["points"], list)
        assert len(data["points"]) > 0
        assert "trained_at" in data
        assert data["sample_count"] == 10

        # 驗證 load_calibration_model 可讀取
        from trustforge.calibration_model import load_calibration_model
        points = load_calibration_model(model_path)
        assert points is not None
        assert len(points) > 0

    def test_training_with_calibrated_confidence_field(self, tmp_path):
        """支援 calibrated_confidence 欄位名（ModelHub 訓練資料格式）。"""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "model"
        failure_path = tmp_path / "output" / "failure"

        rows = [
            {"calibrated_confidence": 0.05 * (i + 1), "hit": i % 3 == 0}
            for i in range(15)
        ]
        _write_jsonl(input_dir / "data.jsonl", rows)

        _run_training(input_dir, output_dir, failure_path)

        model_path = output_dir / "model.json"
        assert model_path.exists()
        data = json.loads(model_path.read_text())
        assert data["sample_count"] == 15

    def test_large_dataset(self, tmp_path):
        """大量資料（500 rows）能正常訓練。"""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "model"
        failure_path = tmp_path / "output" / "failure"

        import random
        random.seed(42)
        rows = [
            {"confidence": random.random(), "hit": random.random() > 0.5}
            for _ in range(500)
        ]
        _write_jsonl(input_dir / "data.jsonl", rows)

        _run_training(input_dir, output_dir, failure_path)

        model_path = output_dir / "model.json"
        data = json.loads(model_path.read_text())
        assert data["sample_count"] == 500
        # isotonic regression 產出的 points 應該單調遞增
        points = data["points"]
        for i in range(len(points) - 1):
            assert points[i]["calibrated"] <= points[i + 1]["calibrated"]


# ═══════════════════════════════════════════════════════════════════════════════
# 失敗路徑
# ═══════════════════════════════════════════════════════════════════════════════


class TestTrainingFailure:
    def test_missing_data_file(self, tmp_path):
        """缺少 data.jsonl → failure。"""
        input_dir = tmp_path / "input"
        input_dir.mkdir(parents=True)  # 空目錄，無 data.jsonl
        output_dir = tmp_path / "model"
        failure_path = tmp_path / "output" / "failure"

        _run_training(input_dir, output_dir, failure_path, expect_success=False)

        assert failure_path.exists()
        assert "not found" in failure_path.read_text().lower()

    def test_insufficient_data(self, tmp_path):
        """資料不足（< 10 rows）→ failure。"""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "model"
        failure_path = tmp_path / "output" / "failure"

        rows = [{"confidence": 0.5, "hit": True}] * 5
        _write_jsonl(input_dir / "data.jsonl", rows)

        _run_training(input_dir, output_dir, failure_path, expect_success=False)

        assert failure_path.exists()
        assert "insufficient" in failure_path.read_text().lower()

    def test_invalid_json(self, tmp_path):
        """壞 JSON → failure。"""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "model"
        failure_path = tmp_path / "output" / "failure"

        data_file = input_dir / "data.jsonl"
        data_file.parent.mkdir(parents=True)
        data_file.write_text('{"confidence": 0.5, "hit": true}\nnot json\n', encoding="utf-8")

        _run_training(input_dir, output_dir, failure_path, expect_success=False)

        assert failure_path.exists()
        assert "invalid json" in failure_path.read_text().lower()

    def test_missing_confidence_field(self, tmp_path):
        """缺少 confidence 欄位 → failure。"""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "model"
        failure_path = tmp_path / "output" / "failure"

        rows = [{"hit": True, "other_field": 0.5}] * 15
        _write_jsonl(input_dir / "data.jsonl", rows)

        _run_training(input_dir, output_dir, failure_path, expect_success=False)

        assert failure_path.exists()
        assert "confidence" in failure_path.read_text().lower()

    def test_missing_hit_field(self, tmp_path):
        """缺少 hit 欄位 → failure。"""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "model"
        failure_path = tmp_path / "output" / "failure"

        rows = [{"confidence": 0.5}] * 15
        _write_jsonl(input_dir / "data.jsonl", rows)

        _run_training(input_dir, output_dir, failure_path, expect_success=False)

        assert failure_path.exists()
        assert "hit" in failure_path.read_text().lower()

    def test_confidence_out_of_range(self, tmp_path):
        """confidence 超出 [0,1] → failure。"""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "model"
        failure_path = tmp_path / "output" / "failure"

        rows = [{"confidence": 1.5, "hit": True}] * 15
        _write_jsonl(input_dir / "data.jsonl", rows)

        _run_training(input_dir, output_dir, failure_path, expect_success=False)

        assert failure_path.exists()
        assert "range" in failure_path.read_text().lower()

    def test_empty_file(self, tmp_path):
        """空檔案 → failure（insufficient data）。"""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "model"
        failure_path = tmp_path / "output" / "failure"

        data_file = input_dir / "data.jsonl"
        data_file.parent.mkdir(parents=True)
        data_file.write_text("", encoding="utf-8")

        _run_training(input_dir, output_dir, failure_path, expect_success=False)

        assert failure_path.exists()
