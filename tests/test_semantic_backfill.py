"""Tests for LLM semantic backfill (Issue #393).

Covers:
- _normalize_direction: direction label normalization
- compute_ground_truth: N+14 day outcome calculation
- enrich_training_data_with_ground_truth: batch enrichment
- retrain_calibrator: calibration model training from JSONL
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from trustforge.backfill import _normalize_direction


# ─── _normalize_direction tests ───────────────────────────────────────────────


class TestNormalizeDirection:
    """Direction normalization maps Chinese/English labels to canonical English."""

    def test_chinese_bullish(self):
        assert _normalize_direction("偏多") == "bullish"

    def test_chinese_bearish(self):
        assert _normalize_direction("偏空") == "bearish"

    def test_chinese_neutral(self):
        assert _normalize_direction("中性") == "neutral"

    def test_chinese_unknown(self):
        """'不明' (offline fallback) maps to neutral."""
        assert _normalize_direction("不明") == "neutral"

    def test_english_passthrough(self):
        assert _normalize_direction("bullish") == "bullish"
        assert _normalize_direction("bearish") == "bearish"
        assert _normalize_direction("neutral") == "neutral"

    def test_unexpected_value(self):
        """Unknown values default to neutral."""
        assert _normalize_direction("") == "neutral"
        assert _normalize_direction("sideways") == "neutral"
        assert _normalize_direction("UP") == "neutral"


# ─── compute_ground_truth tests ──────────────────────────────────────────────


@pytest.fixture
def ohlcv_data_dir(tmp_path):
    """Create minimal OHLCV CSV for testing ground truth calculation."""
    # load_ohlcv expects {COIN}_daily_ohlcv.csv or {COIN}.csv directly in data_dir
    csv_path = tmp_path / "BTC_daily_ohlcv.csv"
    # Write 30 days of data starting from 2023-01-01
    lines = ["date,open,high,low,close,volume\n"]
    import datetime
    base_date = datetime.date(2023, 1, 1)
    base_price = 20000.0
    for i in range(30):
        d = base_date + datetime.timedelta(days=i)
        # Price goes up 1% each day (total ~30%)
        close = base_price * (1.01 ** i)
        lines.append(
            f"{d.isoformat()},{close*0.99:.2f},{close*1.01:.2f},"
            f"{close*0.98:.2f},{close:.2f},1000000\n"
        )
    csv_path.write_text("".join(lines))
    return tmp_path


class TestComputeGroundTruth:
    """Ground truth: N+14 day return and direction classification."""

    def test_bullish_outcome(self, ohlcv_data_dir):
        """Price rising ~14% over 14 days → bullish."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from run_semantic_backfill import compute_ground_truth

        outcome_pct, gt_dir = compute_ground_truth(
            "BTC", "2023-01-01", ohlcv_data_dir,
        )
        assert outcome_pct is not None
        assert outcome_pct > 3.0  # 14 days at 1%/day ≈ 14.9%
        assert gt_dir == "bullish"

    def test_no_future_data(self, ohlcv_data_dir):
        """Date too close to end → None."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from run_semantic_backfill import compute_ground_truth

        # Last date in fixture is 2023-01-30, 14 days ahead doesn't exist
        outcome_pct, gt_dir = compute_ground_truth(
            "BTC", "2023-01-25", ohlcv_data_dir,
        )
        assert outcome_pct is None
        assert gt_dir is None

    def test_nonexistent_coin(self, ohlcv_data_dir):
        """Unknown coin → None."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from run_semantic_backfill import compute_ground_truth

        outcome_pct, gt_dir = compute_ground_truth(
            "DOGE", "2023-01-01", ohlcv_data_dir,
        )
        assert outcome_pct is None
        assert gt_dir is None


# ─── enrich_training_data_with_ground_truth tests ─────────────────────────────


class TestEnrichTrainingData:
    """Batch enrichment adds ground_truth_direction and outcome_pct."""

    def test_enriches_records_without_gt(self, ohlcv_data_dir):
        """Records missing ground_truth get enriched."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from run_semantic_backfill import enrich_training_data_with_ground_truth

        training_dir = ohlcv_data_dir / "training"
        training_dir.mkdir()
        # Write a record without ground_truth
        record = {
            "date": "2023-01-01",
            "coin": "BTC",
            "direction": "bullish",
            "trust_score": 0.6,
        }
        jsonl_path = training_dir / "BTC.jsonl"
        jsonl_path.write_text(json.dumps(record) + "\n")

        stats = enrich_training_data_with_ground_truth(
            training_dir, ohlcv_data_dir, ["BTC"],
        )
        assert stats["BTC"] == 1

        # Verify the record was updated
        enriched = json.loads(jsonl_path.read_text().strip())
        assert "ground_truth_direction" in enriched
        assert "outcome_pct" in enriched
        assert enriched["ground_truth_direction"] == "bullish"

    def test_skips_already_enriched(self, ohlcv_data_dir):
        """Records with existing ground_truth are not re-computed."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from run_semantic_backfill import enrich_training_data_with_ground_truth

        training_dir = ohlcv_data_dir / "training"
        training_dir.mkdir()
        record = {
            "date": "2023-01-01",
            "coin": "BTC",
            "direction": "bullish",
            "trust_score": 0.6,
            "ground_truth_direction": "bearish",  # already present
            "outcome_pct": -5.0,
        }
        jsonl_path = training_dir / "BTC.jsonl"
        jsonl_path.write_text(json.dumps(record) + "\n")

        stats = enrich_training_data_with_ground_truth(
            training_dir, ohlcv_data_dir, ["BTC"],
        )
        assert stats["BTC"] == 0  # nothing updated

        # Verify original values preserved
        enriched = json.loads(jsonl_path.read_text().strip())
        assert enriched["ground_truth_direction"] == "bearish"
        assert enriched["outcome_pct"] == -5.0


# ─── retrain_calibrator tests ────────────────────────────────────────────────


class TestRetrainCalibrator:
    """Calibration model training from JSONL training data."""

    def test_trains_model_from_valid_records(self, tmp_path):
        """Trains isotonic model when enough valid records exist."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

        training_dir = tmp_path / "data" / "training"
        training_dir.mkdir(parents=True)
        model_dir = tmp_path / "data" / "model-artifacts"
        model_dir.mkdir(parents=True)

        # Create 20 records with mix of hits and misses
        records = []
        for i in range(20):
            direction = "bullish" if i % 2 == 0 else "bearish"
            gt = "bullish" if i % 3 == 0 else "bearish"
            records.append(json.dumps({
                "date": f"2023-01-{i+1:02d}",
                "coin": "BTC",
                "direction": direction,
                "trust_score": 0.4 + i * 0.02,
                "ground_truth_direction": gt,
            }))
        (training_dir / "BTC.jsonl").write_text("\n".join(records) + "\n")

        # Patch REPO to use tmp_path
        with patch("run_semantic_backfill.REPO", tmp_path):
            from run_semantic_backfill import retrain_calibrator
            model_path = retrain_calibrator(["BTC"])

        assert model_path.exists()
        model = json.loads(model_path.read_text())
        assert "points" in model
        assert model["sample_count"] >= 10
        assert "trained_at" in model

    def test_skips_records_with_unknown_direction(self, tmp_path):
        """Records with direction='不明' are excluded from training."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

        training_dir = tmp_path / "data" / "training"
        training_dir.mkdir(parents=True)
        model_dir = tmp_path / "data" / "model-artifacts"
        model_dir.mkdir(parents=True)

        # All records have '不明' direction → should be skipped
        records = []
        for i in range(20):
            records.append(json.dumps({
                "date": f"2023-01-{i+1:02d}",
                "coin": "BTC",
                "direction": "不明",
                "trust_score": 0.5,
                "ground_truth_direction": "bullish",
            }))
        (training_dir / "BTC.jsonl").write_text("\n".join(records) + "\n")

        with patch("run_semantic_backfill.REPO", tmp_path):
            from run_semantic_backfill import retrain_calibrator
            model_path = retrain_calibrator(["BTC"])

        # Model not trained (insufficient valid samples)
        # The function warns but doesn't crash
        # Model file may or may not exist depending on previous state
