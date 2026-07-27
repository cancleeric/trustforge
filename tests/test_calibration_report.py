"""Tests for calibration_report — AUC / Brier / reliability diagram."""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

from src.trustforge.trust.calibration_report import (
    _brier_score,
    _trapezoidal_auc,
    _reliability_bins,
    generate_calibration_report,
)


def _write_training_file(tmpdir: Path, filename: str, records: list[dict]) -> None:
    fp = tmpdir / filename
    with open(fp, "w") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def test_auc_perfect_separation():
    """Perfect separation → AUC = 1.0."""
    scores = [0.1, 0.2, 0.3, 0.7, 0.8, 0.9]
    labels = [0, 0, 0, 1, 1, 1]
    auc = _trapezoidal_auc(scores, labels)
    assert auc == 1.0


def test_auc_anti_perfect():
    """Anti-perfect separation → AUC = 0.0."""
    scores = [0.7, 0.8, 0.9, 0.1, 0.2, 0.3]
    labels = [0, 0, 0, 1, 1, 1]
    auc = _trapezoidal_auc(scores, labels)
    assert auc == 0.0


def test_auc_random():
    """Random guessing → AUC ≈ 0.5."""
    scores = [0.1, 0.3, 0.5, 0.7, 0.9, 0.2, 0.4, 0.6, 0.8, 0.95]
    labels = [0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
    auc = _trapezoidal_auc(scores, labels)
    assert 0.0 <= auc <= 1.0
    # With alternating labels, AUC should be around 0.5
    assert 0.2 <= auc <= 0.8


def test_auc_no_positive():
    """All labels 0 → NaN."""
    auc = _trapezoidal_auc([0.1, 0.2, 0.3], [0, 0, 0])
    assert math.isnan(auc)


def test_auc_no_negative():
    """All labels 1 → NaN."""
    auc = _trapezoidal_auc([0.1, 0.2, 0.3], [1, 1, 1])
    assert math.isnan(auc)


def test_auc_single_point():
    """Single point → NaN."""
    auc = _trapezoidal_auc([0.5], [1])
    assert math.isnan(auc)


def test_brier_perfect():
    """Perfect predictions → Brier = 0."""
    score = _brier_score([1.0, 0.0, 1.0], [1, 0, 1])
    assert score == 0.0


def test_brier_worst():
    """Worst predictions → Brier = 0.25 (for binary)."""
    score = _brier_score([0.5, 0.5, 0.5], [0, 1, 0])
    assert score == 0.25


def test_brier_typical():
    """Typical case."""
    score = _brier_score([0.9, 0.1, 0.8, 0.3], [1, 0, 1, 0])
    # (0.9-1)^2=0.01, (0.1-0)^2=0.01, (0.8-1)^2=0.04, (0.3-0)^2=0.09 → mean=0.0375
    assert abs(score - 0.0375) < 0.0001


def test_brier_empty():
    """Empty input → NaN."""
    score = _brier_score([], [])
    assert math.isnan(score)


def test_reliability_bins_basic():
    """10 equal-width bins."""
    scores = [0.05, 0.15, 0.25, 0.85, 0.95]
    labels = [1, 0, 1, 1, 1]
    bins = _reliability_bins(scores, labels, n_bins=10)
    assert len(bins) >= 2  # multiple bins should have data
    for b in bins:
        assert "count" in b
        assert "mean_score" in b
        assert "fraction_correct" in b
        assert 0.0 <= b["fraction_correct"] <= 1.0


def test_reliability_bins_empty():
    """Empty input → empty list."""
    bins = _reliability_bins([], [])
    assert bins == []


def test_generate_report_basic():
    """Generate a report from test training data."""
    records = [
        {"date": "2023-01-01", "coin": "BTC", "direction": "偏多", "trust_score": 0.9, "confidence": 0.8, "evidence_count": 3, "sources": ["src_a"], "model_id": "test", "generated_at": "2023-01-01T00:00:00Z", "outcome_pct": 5.0, "ground_truth_direction": "bullish", "split": "train"},
        {"date": "2023-01-02", "coin": "BTC", "direction": "bearish", "trust_score": 0.7, "confidence": 0.6, "evidence_count": 2, "sources": ["src_b"], "model_id": "test", "generated_at": "2023-01-02T00:00:00Z", "outcome_pct": -5.0, "ground_truth_direction": "bearish", "split": "train"},
        {"date": "2023-01-03", "coin": "BTC", "direction": "中性", "trust_score": 0.5, "confidence": 0.4, "evidence_count": 1, "sources": ["src_c"], "model_id": "test", "generated_at": "2023-01-03T00:00:00Z", "outcome_pct": 1.0, "ground_truth_direction": "neutral", "split": "train"},
        {"date": "2023-01-04", "coin": "BTC", "direction": "偏多", "trust_score": 0.3, "confidence": 0.2, "evidence_count": 2, "sources": ["src_d"], "model_id": "test", "generated_at": "2023-01-04T00:00:00Z", "outcome_pct": -2.0, "ground_truth_direction": "bearish", "split": "train"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        _write_training_file(tmpdir_path, "BTC.jsonl", records)
        out_path = tmpdir_path / "calibration_report.json"

        report = generate_calibration_report(
            training_dir=tmpdir_path,
            output_path=out_path,
        )

        assert report["total_records"] == 4
        assert report["overall"]["correct"] == 3  # 偏多→bullish ✓, bearish→bearish ✓, 中性→neutral ✓, 偏多→bearish ✗
        assert report["overall"]["accuracy"] == 0.75
        assert "disclaimer" in report
        assert "TrustScore does not predict market direction" in report["disclaimer"]

        # Bullish subset: GT=bullish, only record 1
        assert report["bullish_subset"]["total"] == 1
        assert report["bullish_subset"]["correct"] == 1

        # Bearish subset: GT=bearish, records 2 and 4
        assert report["bearish_subset"]["total"] == 2
        assert report["bearish_subset"]["correct"] == 1  # only record 2 correct

        # Verify output file was written
        assert out_path.is_file()
        parsed = json.loads(out_path.read_text())
        assert parsed["total_records"] == 4


def test_generate_report_empty():
    """Empty training data → minimal report."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        out_path = tmpdir_path / "report.json"

        report = generate_calibration_report(
            training_dir=tmpdir_path,
            output_path=out_path,
        )

        assert report["total_records"] == 0
        assert report["overall"] == {}
        assert out_path.is_file()


def test_generate_report_all_neutral():
    """When all GT is neutral, bullish/bearish subsets are empty."""
    records = [
        {"date": "2023-01-01", "coin": "BTC", "direction": "中性", "trust_score": 0.5, "confidence": 0.4, "evidence_count": 1, "sources": ["src_a"], "model_id": "test", "generated_at": "2023-01-01T00:00:00Z", "outcome_pct": 1.0, "ground_truth_direction": "neutral", "split": "train"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        _write_training_file(tmpdir_path, "BTC.jsonl", records)
        out_path = tmpdir_path / "report.json"

        report = generate_calibration_report(
            training_dir=tmpdir_path,
            output_path=out_path,
        )

        assert report["total_records"] == 1
        assert report["overall"]["accuracy"] == 1.0
        assert report["bullish_subset"]["total"] == 0
        assert report["bearish_subset"]["total"] == 0


def test_auc_mismatched_lengths():
    """Different length lists → NaN."""
    auc = _trapezoidal_auc([0.5], [1, 0])
    assert math.isnan(auc)


def test_brier_mismatched_lengths():
    """Different length lists → NaN."""
    score = _brier_score([0.5], [1, 0])
    assert math.isnan(score)
