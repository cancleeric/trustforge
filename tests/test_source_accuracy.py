"""Tests for source_accuracy — per-source accuracy report & Spearman comparison."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.trustforge.trust.source_accuracy import (
    SourceAccuracyReport,
    _spearman_rank_correlation,
    evaluate_source_accuracy,
)


def _write_training_file(tmpdir: Path, filename: str, records: list[dict]) -> None:
    """Write a temporary JSONL training file."""
    fp = tmpdir / filename
    with open(fp, "w") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def test_evaluate_source_accuracy_basic():
    """Two sources with known correctness."""
    records = [
        {"date": "2023-01-01", "coin": "BTC", "direction": "偏多", "trust_score": 0.8, "confidence": 0.7, "evidence_count": 3, "sources": ["src_a", "src_b"], "model_id": "test", "generated_at": "2023-01-01T00:00:00Z", "outcome_pct": 5.0, "ground_truth_direction": "bullish", "split": "train"},
        {"date": "2023-01-02", "coin": "BTC", "direction": "中性", "trust_score": 0.5, "confidence": 0.4, "evidence_count": 2, "sources": ["src_a"], "model_id": "test", "generated_at": "2023-01-02T00:00:00Z", "outcome_pct": 1.0, "ground_truth_direction": "neutral", "split": "train"},
        {"date": "2023-01-03", "coin": "BTC", "direction": "bearish", "trust_score": 0.6, "confidence": 0.5, "evidence_count": 2, "sources": ["src_b"], "model_id": "test", "generated_at": "2023-01-03T00:00:00Z", "outcome_pct": -5.0, "ground_truth_direction": "bearish", "split": "train"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_training_file(Path(tmpdir), "BTC.jsonl", records)

        reports = evaluate_source_accuracy(training_dir=tmpdir, n_days=7, threshold=0.03)
        assert len(reports) == 2
        by_src = {r.source: r for r in reports}

        # src_a: [("bullish", "bullish"), ("neutral", "neutral")] → 2/2 correct
        assert by_src["src_a"].total == 2
        assert by_src["src_a"].correct == 2
        assert by_src["src_a"].accuracy == 1.0

        # src_b: [("bullish", "bullish"), ("bearish", "bearish")] → 2/2 correct
        assert by_src["src_b"].total == 2
        assert by_src["src_b"].correct == 2
        assert by_src["src_b"].accuracy == 1.0


def test_evaluate_source_accuracy_chinese_directions():
    """Test normalization of Chinese direction labels."""
    records = [
        {"date": "2023-01-01", "coin": "BTC", "direction": "偏多", "trust_score": 0.8, "confidence": 0.7, "evidence_count": 3, "sources": ["src_a"], "model_id": "test", "generated_at": "2023-01-01T00:00:00Z", "outcome_pct": 5.0, "ground_truth_direction": "bullish", "split": "train"},
        {"date": "2023-01-02", "coin": "BTC", "direction": "中性", "trust_score": 0.5, "confidence": 0.4, "evidence_count": 2, "sources": ["src_a"], "model_id": "test", "generated_at": "2023-01-02T00:00:00Z", "outcome_pct": 1.0, "ground_truth_direction": "neutral", "split": "train"},
        {"date": "2023-01-03", "coin": "BTC", "direction": "不明", "trust_score": 0.4, "confidence": 0.3, "evidence_count": 1, "sources": ["src_a"], "model_id": "test", "generated_at": "2023-01-03T00:00:00Z", "outcome_pct": -1.0, "ground_truth_direction": "bearish", "split": "train"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_training_file(Path(tmpdir), "BTC.jsonl", records)

        reports = evaluate_source_accuracy(training_dir=tmpdir, n_days=7, threshold=0.03)
        by_src = {r.source: r for r in reports}

        # src_a: "偏多"→bullish(correct), "中性"→neutral(correct), "不明"→neutral(incorrect, GT=bearish)
        assert by_src["src_a"].total == 3
        assert by_src["src_a"].correct == 2
        assert by_src["src_a"].accuracy == 2.0 / 3.0
        assert by_src["src_a"].directional == 1  # only "偏多" is directional
        assert by_src["src_a"].directional_accuracy == 1.0  # "偏多" is correct


def test_evaluate_source_accuracy_missing_gt():
    """Records with missing GT and no OHLCV data → skipped."""
    records = [
        {"date": "2023-01-01", "coin": "NOCOIN", "direction": "偏多", "trust_score": 0.8, "confidence": 0.7, "evidence_count": 3, "sources": ["src_a"], "model_id": "test", "generated_at": "2023-01-01T00:00:00Z", "outcome_pct": 5.0, "ground_truth_direction": None, "split": "train"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_training_file(Path(tmpdir), "NOCOIN.jsonl", records)
        # NOCOIN has no real OHLCV data → Phase 1 labeler returns None → record skipped
        reports = evaluate_source_accuracy(training_dir=tmpdir, n_days=7, threshold=0.03)
        # src_a should not appear because GT is None
        assert all(r.source != "src_a" for r in reports)


def test_evaluate_source_accuracy_confusion_matrix():
    """Confusion matrix should be correct."""
    records = [
        {"date": "2023-01-01", "coin": "BTC", "direction": "偏多", "trust_score": 0.8, "confidence": 0.7, "evidence_count": 3, "sources": ["src_a"], "model_id": "test", "generated_at": "2023-01-01T00:00:00Z", "outcome_pct": 5.0, "ground_truth_direction": "bullish", "split": "train"},
        {"date": "2023-01-02", "coin": "BTC", "direction": "偏多", "trust_score": 0.8, "confidence": 0.7, "evidence_count": 3, "sources": ["src_a"], "model_id": "test", "generated_at": "2023-01-02T00:00:00Z", "outcome_pct": -5.0, "ground_truth_direction": "bearish", "split": "train"},
        {"date": "2023-01-03", "coin": "BTC", "direction": "中性", "trust_score": 0.5, "confidence": 0.4, "evidence_count": 2, "sources": ["src_a"], "model_id": "test", "generated_at": "2023-01-03T00:00:00Z", "outcome_pct": 1.0, "ground_truth_direction": "neutral", "split": "train"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_training_file(Path(tmpdir), "BTC.jsonl", records)

        reports = evaluate_source_accuracy(training_dir=tmpdir, n_days=7, threshold=0.03)
        by_src = {r.source: r for r in reports}
        cm = by_src["src_a"].confusion_matrix

        assert cm["bullish"]["bullish"] == 1
        assert cm["bullish"]["bearish"] == 1
        assert cm["neutral"]["neutral"] == 1


def test_spearman_perfect_positive():
    """Perfect positive correlation → 1.0."""
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [2.0, 4.0, 6.0, 8.0, 10.0]
    rho = _spearman_rank_correlation(x, y)
    assert rho == 1.0


def test_spearman_perfect_negative():
    """Perfect negative correlation → -1.0."""
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [5.0, 4.0, 3.0, 2.0, 1.0]
    rho = _spearman_rank_correlation(x, y)
    assert rho == -1.0


def test_spearman_zero_variance():
    """All values same → NaN (no variance)."""
    x = [1.0, 1.0, 1.0]
    y = [2.0, 3.0, 4.0]
    rho = _spearman_rank_correlation(x, y)
    import math
    assert math.isnan(rho)


def test_spearman_insufficient_length():
    """Fewer than 2 points → NaN."""
    rho = _spearman_rank_correlation([1.0], [2.0])
    import math
    assert math.isnan(rho)


def test_source_accuracy_report_dataclass():
    """Smoke test for dataclass."""
    report = SourceAccuracyReport(
        source="test",
        total=10,
        directional=5,
        correct=3,
        accuracy=0.3,
        directional_accuracy=0.6,
        confusion_matrix={"bullish": {"bullish": 1, "bearish": 0, "neutral": 0}},
    )
    assert report.source == "test"
    assert report.total == 10


def test_evaluate_source_accuracy_no_sources():
    """Records without sources → empty reports."""
    records = [
        {"date": "2023-01-01", "coin": "BTC", "direction": "偏多", "trust_score": 0.8, "confidence": 0.7, "evidence_count": 3, "sources": [], "model_id": "test", "generated_at": "2023-01-01T00:00:00Z", "outcome_pct": 5.0, "ground_truth_direction": "bullish", "split": "train"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_training_file(Path(tmpdir), "BTC.jsonl", records)

        reports = evaluate_source_accuracy(training_dir=tmpdir, n_days=7, threshold=0.03)
        assert len(reports) == 0


def test_evaluate_source_accuracy_no_gt():
    """All records with invalid GT and unknown coin → empty reports."""
    records = [
        {"date": "2023-01-01", "coin": "NOCOIN", "direction": "偏多", "trust_score": 0.8, "confidence": 0.7, "evidence_count": 3, "sources": ["src_a"], "model_id": "test", "generated_at": "2023-01-01T00:00:00Z", "outcome_pct": 5.0, "ground_truth_direction": None, "split": "train"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_training_file(Path(tmpdir), "NOCOIN.jsonl", records)

        reports = evaluate_source_accuracy(training_dir=tmpdir, n_days=7, threshold=0.03)
        # NOCOIN has no OHLCV data → Phase 1 labeler returns None → record skipped
        assert len(reports) == 0
