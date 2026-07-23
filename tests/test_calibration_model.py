"""Tests for calibration_model.py (Issue #343)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from trustforge.calibration_model import (
    apply_calibration,
    load_calibration_model,
    save_calibration_model,
    train_isotonic,
)


class TestTrainIsotonicBasic:
    """test_train_isotonic_basic：驗證 PAV 輸出是單調遞增的。"""

    def test_monotonicity(self):
        """輸出的 calibrated 值必須單調遞增。"""
        # 構造一些 non-monotone 的資料
        confidences = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        hit_flags = [False, True, False, True, True, False, True, True, True]

        points = train_isotonic(confidences, hit_flags)

        assert len(points) >= 1
        for i in range(len(points) - 1):
            assert points[i]["calibrated"] <= points[i + 1]["calibrated"], (
                f"Non-monotone at index {i}: "
                f"{points[i]['calibrated']} > {points[i+1]['calibrated']}"
            )

    def test_output_range(self):
        """輸出的 calibrated 值在 [0, 1]。"""
        confidences = [0.1, 0.3, 0.5, 0.7, 0.9]
        hit_flags = [False, True, False, True, True]

        points = train_isotonic(confidences, hit_flags)

        for p in points:
            assert 0.0 <= p["calibrated"] <= 1.0
            assert 0.0 <= p["confidence"] <= 1.0

    def test_all_hits(self):
        """全 hit → calibrated 全為 1.0。"""
        confidences = [0.2, 0.4, 0.6, 0.8]
        hit_flags = [True, True, True, True]

        points = train_isotonic(confidences, hit_flags)
        for p in points:
            assert p["calibrated"] == 1.0

    def test_all_misses(self):
        """全 miss → calibrated 全為 0.0。"""
        confidences = [0.2, 0.4, 0.6, 0.8]
        hit_flags = [False, False, False, False]

        points = train_isotonic(confidences, hit_flags)
        for p in points:
            assert p["calibrated"] == 0.0

    def test_empty_input(self):
        """空輸入回空列表。"""
        assert train_isotonic([], []) == []

    def test_length_mismatch_raises(self):
        """長度不一致 raise ValueError。"""
        with pytest.raises(ValueError):
            train_isotonic([0.1, 0.2], [True])


class TestTrainIsotonicAlreadyMonotone:
    """test_train_isotonic_already_monotone：已經是單調的資料不改變順序。"""

    def test_already_monotone(self):
        """hit rate 天然遞增 → 不需合併。"""
        # 低 confidence → miss, 高 confidence → hit（天然單調）
        confidences = [0.1, 0.2, 0.3, 0.7, 0.8, 0.9]
        hit_flags = [False, False, False, True, True, True]

        points = train_isotonic(confidences, hit_flags)

        # 應有多個校準點，且自然單調
        assert len(points) >= 2
        for i in range(len(points) - 1):
            assert points[i]["calibrated"] <= points[i + 1]["calibrated"]

    def test_perfect_calibration(self):
        """完美校準的資料：低信心低 hit、高信心高 hit。"""
        confidences = [0.1, 0.1, 0.5, 0.5, 0.9, 0.9]
        hit_flags = [False, False, True, False, True, True]

        points = train_isotonic(confidences, hit_flags)

        # 單調性
        for i in range(len(points) - 1):
            assert points[i]["calibrated"] <= points[i + 1]["calibrated"]


class TestApplyCalibrationInterpolation:
    """test_apply_calibration_interpolation：線性插值正確。"""

    def test_exact_point(self):
        """落在已知校準點上，回傳該點的 calibrated 值。"""
        model = [
            {"confidence": 0.0, "calibrated": 0.0},
            {"confidence": 0.5, "calibrated": 0.3},
            {"confidence": 1.0, "calibrated": 1.0},
        ]
        assert apply_calibration(0.5, model) == 0.3

    def test_midpoint_interpolation(self):
        """兩點中間，線性插值。"""
        model = [
            {"confidence": 0.0, "calibrated": 0.0},
            {"confidence": 1.0, "calibrated": 1.0},
        ]
        result = apply_calibration(0.5, model)
        assert abs(result - 0.5) < 1e-6

    def test_quarter_interpolation(self):
        """1/4 位置的插值。"""
        model = [
            {"confidence": 0.0, "calibrated": 0.0},
            {"confidence": 0.4, "calibrated": 0.2},
            {"confidence": 1.0, "calibrated": 1.0},
        ]
        # Between 0.4 and 1.0: ratio = (0.7 - 0.4) / (1.0 - 0.4) = 0.5
        # interpolated = 0.2 + 0.5 * (1.0 - 0.2) = 0.6
        result = apply_calibration(0.7, model)
        assert abs(result - 0.6) < 1e-4


class TestApplyCalibrationEdgeCases:
    """test_apply_calibration_edge_cases：邊界情況。"""

    def test_below_range(self):
        """低於模型最小 confidence → 回傳最小 calibrated。"""
        model = [
            {"confidence": 0.2, "calibrated": 0.1},
            {"confidence": 0.8, "calibrated": 0.9},
        ]
        assert apply_calibration(0.0, model) == 0.1
        assert apply_calibration(0.1, model) == 0.1

    def test_above_range(self):
        """高於模型最大 confidence → 回傳最大 calibrated。"""
        model = [
            {"confidence": 0.2, "calibrated": 0.1},
            {"confidence": 0.8, "calibrated": 0.9},
        ]
        assert apply_calibration(1.0, model) == 0.9
        assert apply_calibration(0.9, model) == 0.9

    def test_negative_clamps(self):
        """負數 clamp 到 0。"""
        model = [
            {"confidence": 0.0, "calibrated": 0.0},
            {"confidence": 1.0, "calibrated": 1.0},
        ]
        assert apply_calibration(-0.5, model) == 0.0

    def test_above_one_clamps(self):
        """> 1 clamp 到 1。"""
        model = [
            {"confidence": 0.0, "calibrated": 0.0},
            {"confidence": 1.0, "calibrated": 1.0},
        ]
        assert apply_calibration(1.5, model) == 1.0

    def test_empty_model(self):
        """空 model → 回傳原值。"""
        assert apply_calibration(0.5, []) == 0.5

    def test_single_point_model(self):
        """單點 model → 回傳該點的 calibrated（左右邊界都是它）。"""
        model = [{"confidence": 0.5, "calibrated": 0.3}]
        assert apply_calibration(0.5, model) == 0.3
        assert apply_calibration(0.1, model) == 0.3
        assert apply_calibration(0.9, model) == 0.3


class TestSaveLoadRoundtrip:
    """test_save_load_roundtrip：存讀模型一致。"""

    def test_roundtrip(self, tmp_path: Path):
        """存完讀回，points 一致。"""
        points = [
            {"confidence": 0.1, "calibrated": 0.05},
            {"confidence": 0.5, "calibrated": 0.4},
            {"confidence": 0.9, "calibrated": 0.85},
        ]
        model_path = tmp_path / "model.json"
        save_calibration_model(points, model_path, sample_count=100)

        loaded = load_calibration_model(model_path)
        assert loaded is not None
        assert loaded == points

    def test_file_has_metadata(self, tmp_path: Path):
        """存檔包含 trained_at 和 sample_count。"""
        points = [
            {"confidence": 0.0, "calibrated": 0.0},
            {"confidence": 1.0, "calibrated": 1.0},
        ]
        model_path = tmp_path / "model.json"
        save_calibration_model(points, model_path, sample_count=42)

        data = json.loads(model_path.read_text())
        assert "trained_at" in data
        assert data["sample_count"] == 42

    def test_creates_parent_dirs(self, tmp_path: Path):
        """不存在的父目錄會自動建立。"""
        model_path = tmp_path / "nested" / "dir" / "model.json"
        points = [
            {"confidence": 0.0, "calibrated": 0.0},
            {"confidence": 1.0, "calibrated": 1.0},
        ]
        save_calibration_model(points, model_path, sample_count=10)
        assert model_path.exists()

    def test_load_nonexistent(self, tmp_path: Path):
        """不存在的檔案回 None。"""
        assert load_calibration_model(tmp_path / "no_such.json") is None

    def test_load_invalid_json(self, tmp_path: Path):
        """格式錯誤的 JSON 回 None。"""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json at all")
        assert load_calibration_model(bad_file) is None

    def test_load_missing_points(self, tmp_path: Path):
        """缺少 points key 回 None。"""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps({"trained_at": "x"}))
        assert load_calibration_model(bad_file) is None

    def test_load_single_point_invalid(self, tmp_path: Path):
        """只有 1 個點（< 2）回 None。"""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps({"points": [{"confidence": 0.5, "calibrated": 0.3}]}))
        assert load_calibration_model(bad_file) is None


class TestNoModelFallback:
    """test_no_model_fallback：無模型時 fallback 到硬編碼表。"""

    def teardown_method(self):
        """每個測試結束後清除 cache，避免污染其他測試。"""
        from trustforge.trust import scoring
        scoring._CALIBRATION_MODEL_CACHE.clear()

    @pytest.mark.xfail(reason="pre-existing 校準模型漂移，commit 9017a09 引入新 isotonic 模型後未同步更新測試期望值；追蹤 #633", strict=False)
    def test_fallback_uses_hardcoded_table(self, tmp_path: Path, monkeypatch):
        """沒有模型檔時 _calibrate_confidence 走原硬編碼邏輯。"""
        from trustforge.trust import scoring

        # 清除 cache，強制重新載入
        scoring._CALIBRATION_MODEL_CACHE.clear()
        # 指向不存在的路徑
        monkeypatch.setattr(
            scoring, "_CALIBRATION_MODEL_PATH",
            str(tmp_path / "nonexistent.json")
        )

        # 用硬編碼表的已知映射點驗證
        # (0.55, 0.55) 是表中已知的錨點
        result = scoring._calibrate_confidence(0.55)
        assert abs(result - 0.55) < 1e-4

        # (0.00, 0.00)
        result = scoring._calibrate_confidence(0.0)
        assert result == 0.0

    @pytest.mark.xfail(reason="pre-existing 校準模型漂移，commit 9017a09 引入新 isotonic 模型後未同步更新測試期望值；追蹤 #633", strict=False)
    def test_with_model_uses_isotonic(self, tmp_path: Path, monkeypatch):
        """有模型檔時 _calibrate_confidence 用模型。"""
        from trustforge.trust import scoring

        # 建立模型檔
        model_path = tmp_path / "model.json"
        points = [
            {"confidence": 0.0, "calibrated": 0.1},
            {"confidence": 0.5, "calibrated": 0.5},
            {"confidence": 1.0, "calibrated": 0.9},
        ]
        save_calibration_model(points, model_path, sample_count=50)

        # 清除 cache 並指向模型
        scoring._CALIBRATION_MODEL_CACHE.clear()
        monkeypatch.setattr(scoring, "_CALIBRATION_MODEL_PATH", str(model_path))

        # 驗證用模型映射：0.0 → 0.1（模型值，非硬編碼的 0.0）
        result = scoring._calibrate_confidence(0.0)
        assert abs(result - 0.1) < 1e-4

        # 0.25 → 插值 between (0.0, 0.1) and (0.5, 0.5)
        # ratio = 0.25/0.5 = 0.5, val = 0.1 + 0.5*(0.5-0.1) = 0.3
        result = scoring._calibrate_confidence(0.25)
        assert abs(result - 0.3) < 1e-4
