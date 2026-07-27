"""Tests for calibration_runner.py (issue #335)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trustforge.calibration_runner import (
    _check_hit,
    calculate_calibration_error,
    compare_predictions,
    confidence_correctness_auc,
    load_predictions,
    run_calibration,
)
from trustforge.ingestion.prices import Bar


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("scores", "labels", "expected"),
    [
        ([0.1, 0.2, 0.8, 0.9], [False, False, True, True], 1.0),
        ([0.8, 0.9, 0.1, 0.2], [False, False, True, True], 0.0),
        ([0.5, 0.5, 0.5, 0.5], [False, False, True, True], 0.5),
    ],
)
def test_confidence_correctness_auc_is_tie_aware(scores, labels, expected):
    result = confidence_correctness_auc(scores, labels)
    assert result["value"] == expected
    assert result["reason"] is None
    assert result["target"] == "confidence_discrimination_of_correctness"


def test_confidence_correctness_auc_single_class_is_null():
    result = confidence_correctness_auc([0.2, 0.8], [True, True])
    assert result["value"] is None
    assert result["reason"] == "requires both correct and incorrect predictions"


def _make_bars(dates_closes: list[tuple[str, float]]) -> list[Bar]:
    """Helper: create Bar objects from (date, close) pairs."""
    return [
        Bar(date=d, open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1000.0)
        for d, c in dates_closes
    ]


def _write_training_jsonl(tmpdir: Path, coin: str, records: list[dict]) -> None:
    filepath = tmpdir / f"{coin}.jsonl"
    with filepath.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ─── test_load_predictions_filters_direction ─────────────────────────────────


def test_load_predictions_filters_direction(tmp_path: Path):
    """應只載入 direction 在 {'中性','偏多','偏空'} 的記錄，過濾 '不明' 和空值。"""
    records = [
        {"date": "2024-01-01", "direction": "偏多", "confidence": 0.7, "trust_score": 0.6},
        {"date": "2024-01-02", "direction": "不明", "confidence": 0.5, "trust_score": 0.5},
        {"date": "2024-01-03", "direction": "中性", "confidence": 0.4, "trust_score": 0.5},
        {"date": "2024-01-04", "direction": "", "confidence": 0.3, "trust_score": 0.4},
        {"date": "2024-01-05", "direction": None, "confidence": 0.2, "trust_score": 0.3},
        {"date": "2024-01-06", "direction": "偏空", "confidence": 0.8, "trust_score": 0.7},
    ]
    _write_training_jsonl(tmp_path, "BTC", records)

    preds = load_predictions("BTC", tmp_path)

    assert len(preds) == 3
    assert preds[0]["direction"] == "偏多"
    assert preds[1]["direction"] == "中性"
    assert preds[2]["direction"] == "偏空"


def test_load_predictions_missing_file(tmp_path: Path):
    """不存在的檔案應回傳空列表。"""
    preds = load_predictions("NOSUCH", tmp_path)
    assert preds == []


# ─── test_compare_neutral_hit ────────────────────────────────────────────────


def test_compare_neutral_hit():
    """中性 + 實際 < 2% 變化 = hit。"""
    # Day 0: 100, Day 1: 101 → change = 1% < 2% → hit
    bars = _make_bars([
        ("2024-01-01", 100.0),
        ("2024-01-02", 101.0),  # T+1: +1%
    ])
    predictions = [
        {"date": "2024-01-01", "direction": "中性", "confidence": 0.5, "trust_score": 0.5},
    ]

    result = compare_predictions(predictions, bars, horizons=(1,))
    assert result["horizons"]["T+1"]["eligible"] == 1
    assert result["horizons"]["T+1"]["hits"] == 1
    assert result["horizons"]["T+1"]["hit_rate"] == 1.0


def test_compare_neutral_miss():
    """中性 + 實際 >= 2% 變化 = miss。"""
    # Day 0: 100, Day 1: 103 → change = 3% >= 2% → miss
    bars = _make_bars([
        ("2024-01-01", 100.0),
        ("2024-01-02", 103.0),  # T+1: +3%
    ])
    predictions = [
        {"date": "2024-01-01", "direction": "中性", "confidence": 0.5, "trust_score": 0.5},
    ]

    result = compare_predictions(predictions, bars, horizons=(1,))
    assert result["horizons"]["T+1"]["hits"] == 0


# ─── test_compare_bullish_hit ────────────────────────────────────────────────


def test_compare_bullish_hit():
    """偏多 + 實際 > 0 = hit。"""
    bars = _make_bars([
        ("2024-01-01", 100.0),
        ("2024-01-02", 105.0),  # T+1: +5%
    ])
    predictions = [
        {"date": "2024-01-01", "direction": "偏多", "confidence": 0.7, "trust_score": 0.6},
    ]

    result = compare_predictions(predictions, bars, horizons=(1,))
    assert result["horizons"]["T+1"]["hits"] == 1
    assert result["horizons"]["T+1"]["hit_rate"] == 1.0


def test_compare_bullish_miss():
    """偏多 + 實際 < 0 = miss。"""
    bars = _make_bars([
        ("2024-01-01", 100.0),
        ("2024-01-02", 95.0),  # T+1: -5%
    ])
    predictions = [
        {"date": "2024-01-01", "direction": "偏多", "confidence": 0.7, "trust_score": 0.6},
    ]

    result = compare_predictions(predictions, bars, horizons=(1,))
    assert result["horizons"]["T+1"]["hits"] == 0


def test_compare_bearish_hit():
    """偏空 + 實際 < 0 = hit。"""
    bars = _make_bars([
        ("2024-01-01", 100.0),
        ("2024-01-02", 95.0),  # T+1: -5%
    ])
    predictions = [
        {"date": "2024-01-01", "direction": "偏空", "confidence": 0.8, "trust_score": 0.7},
    ]

    result = compare_predictions(predictions, bars, horizons=(1,))
    assert result["horizons"]["T+1"]["hits"] == 1


# ─── test_calibration_error_calculation ──────────────────────────────────────


def test_calibration_error_calculation():
    """校準誤差：bins 有足夠樣本時計算 max |mean_conf - hit_rate|。"""
    # 建立 10 筆 predictions，confidence 全在 0.6-0.8 bin
    # 全部都 hit → empirical_hit_rate=1.0, mean_conf≈0.7
    # error = |0.7 - 1.0| = 0.3
    predictions = [
        {"date": f"2024-01-{i:02d}", "direction": "偏多", "confidence": 0.65 + i * 0.01, "trust_score": 0.5}
        for i in range(1, 11)
    ]

    # 製造全部 hit 的 details（horizon=1）
    comparison_results = {
        "horizons": {"T+1": {"eligible": 10, "hits": 10, "hit_rate": 1.0}},
        "details": [
            {"date": f"2024-01-{i:02d}", "direction": "偏多", "confidence": 0.65 + i * 0.01,
             "horizon": 1, "change_pct": 0.05, "hit": True}
            for i in range(1, 11)
        ],
    }

    result = calculate_calibration_error(predictions, comparison_results)

    assert result["reliable_bins"] == 1  # 只有 0.6-0.8 bin 有 ≥5 samples
    assert result["calibration_error"] is not None
    # mean_conf ≈ 0.7, hit_rate = 1.0 → error ≈ 0.3
    assert abs(result["calibration_error"] - 0.3) < 0.05


def test_calibration_error_insufficient_data():
    """bins 不足 5 筆時 calibration_error 應為 None。"""
    predictions = [
        {"date": "2024-01-01", "direction": "偏多", "confidence": 0.7, "trust_score": 0.5},
    ]
    comparison_results = {
        "horizons": {"T+1": {"eligible": 1, "hits": 1, "hit_rate": 1.0}},
        "details": [
            {"date": "2024-01-01", "direction": "偏多", "confidence": 0.7,
             "horizon": 1, "change_pct": 0.05, "hit": True}
        ],
    }

    result = calculate_calibration_error(predictions, comparison_results)
    assert result["calibration_error"] is None
    assert result["reliable_bins"] == 0


def test_calibration_same_date_predictions_keep_row_identity():
    predictions = [
        {
            "date": "2024-01-01",
            "direction": "偏多",
            "confidence": 0.9,
            "trust_score": 0.5,
        },
        {
            "date": "2024-01-01",
            "direction": "偏空",
            "confidence": 0.1,
            "trust_score": 0.5,
        },
    ]
    bars = _make_bars(
        [
            ("2024-01-01", 100.0),
            ("2024-01-02", 110.0),
        ]
    )
    comparison = compare_predictions(predictions, bars, horizons=(1,))
    assert [detail["prediction_index"] for detail in comparison["details"]] == [0, 1]
    assert [detail["hit"] for detail in comparison["details"]] == [True, False]

    result = calculate_calibration_error(predictions, comparison)
    assert result["confidence_correctness_roc_auc"]["value"] == 1.0
    nonempty_bins = [item for item in result["bins"] if item["count"]]
    assert sum(item["count"] for item in nonempty_bins) == 2
    assert sorted(item["empirical_hit_rate"] for item in nonempty_bins) == [0.0, 1.0]


def test_legacy_same_date_details_fail_closed_as_ambiguous():
    predictions = [
        {
            "date": "2024-01-01",
            "direction": "偏多",
            "confidence": 0.9,
            "trust_score": 0.5,
        },
        {
            "date": "2024-01-01",
            "direction": "偏空",
            "confidence": 0.1,
            "trust_score": 0.5,
        },
    ]
    legacy_comparison = {
        "details": [
            {
                "date": "2024-01-01",
                "horizon": 1,
                "hit": True,
            },
            {
                "date": "2024-01-01",
                "horizon": 1,
                "hit": False,
            },
        ]
    }

    result = calculate_calibration_error(predictions, legacy_comparison)

    assert sum(item["count"] for item in result["bins"]) == 0
    assert result["confidence_correctness_roc_auc"]["value"] is None
    assert result["row_alignment"]["excluded_ambiguous_legacy_rows"] == 2
    assert "require a unique prediction" in result["row_alignment"]["reason"]


# ─── test_run_calibration_integration ────────────────────────────────────────


def test_run_calibration_integration(tmp_path: Path):
    """端到端整合測試：training JSONL + OHLCV CSV → 完整 calibration report。"""
    # 建立 training data
    training_dir = tmp_path / "training"
    training_dir.mkdir()

    # 20 天的預測，全部偏多
    records = [
        {"date": f"2024-01-{i:02d}", "direction": "偏多", "confidence": 0.7,
         "trust_score": 0.6, "coin": "BTC", "evidence_count": 5}
        for i in range(1, 21)
    ]
    _write_training_jsonl(training_dir, "BTC", records)

    # 建立 OHLCV CSV（30 天上漲趨勢）
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    csv_path = data_dir / "BTC_daily_ohlcv.csv"
    with csv_path.open("w", encoding="utf-8") as fh:
        fh.write("date,open,high,low,close,volume\n")
        for i in range(1, 31):
            price = 40000 + i * 100  # 每天漲 100
            fh.write(f"2024-01-{i:02d},{price},{price+50},{price-50},{price},1000\n")

    result = run_calibration("BTC", data_dir=data_dir, training_dir=training_dir)

    assert result["coin"] == "BTC"
    assert result["available_snapshot_count"] == 20
    assert result["ohlcv_bar_count"] == 30
    assert "T+1" in result["horizons"]
    assert "T+7" in result["horizons"]
    assert "T+14" in result["horizons"]

    # 全部偏多 + 上漲趨勢 → hit_rate 應該很高
    t1 = result["horizons"]["T+1"]
    assert t1["eligible_predictions"] > 0
    assert t1["hit_rate"] is not None
    assert t1["hit_rate"] > 0.8  # 上漲趨勢中偏多幾乎全命中

    # calibration 結構存在
    assert "calibration" in result
    assert "bins" in result["calibration"]


def test_run_calibration_same_date_reliability_uses_row_identity(tmp_path: Path):
    training_dir = tmp_path / "training"
    training_dir.mkdir()
    _write_training_jsonl(
        training_dir,
        "BTC",
        [
            {
                "date": "2024-01-01",
                "direction": "偏多",
                "confidence": 0.9,
                "trust_score": 0.5,
            },
            {
                "date": "2024-01-01",
                "direction": "偏空",
                "confidence": 0.1,
                "trust_score": 0.5,
            },
        ],
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "BTC_daily_ohlcv.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2024-01-01,100,101,99,100,1000\n"
        "2024-01-02,110,111,109,110,1000\n",
        encoding="utf-8",
    )

    result = run_calibration("BTC", data_dir=data_dir, training_dir=training_dir)
    reliability = result["horizons"]["T+1"]["reliability"]

    assert [item["count"] for item in reliability] == [1, 1]
    assert [item["mean_information_completeness"] for item in reliability] == [
        0.1,
        0.9,
    ]
    assert [item["empirical_hit_rate"] for item in reliability] == [0.0, 1.0]


def test_run_calibration_no_data(tmp_path: Path):
    """無訓練資料時應回傳空結構不崩。"""
    training_dir = tmp_path / "training"
    training_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    result = run_calibration("BTC", data_dir=data_dir, training_dir=training_dir)

    assert result["coin"] == "BTC"
    assert result["available_snapshot_count"] == 0
    assert result["horizons"] == {}


# ─── _check_hit unit tests ──────────────────────────────────────────────────


class TestCheckHit:
    def test_neutral_small_change_is_hit(self):
        assert _check_hit("中性", 0.01) is True  # 1% < 2%

    def test_neutral_exact_threshold_is_miss(self):
        assert _check_hit("中性", 0.02) is False  # 2% == threshold → miss

    def test_bullish_positive_is_hit(self):
        assert _check_hit("偏多", 0.001) is True

    def test_bullish_zero_is_miss(self):
        assert _check_hit("偏多", 0.0) is False

    def test_bearish_negative_is_hit(self):
        assert _check_hit("偏空", -0.05) is True

    def test_bearish_zero_is_miss(self):
        assert _check_hit("偏空", 0.0) is False
