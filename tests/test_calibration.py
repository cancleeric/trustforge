import json

from trustforge.calibration import load_training_snapshots, outcomes_for_horizon, replay_report
from trustforge.ingestion.prices import Bar


def _bars():
    return [
        Bar("2026-01-01", 100, 101, 99, 100, 1),
        Bar("2026-01-02", 110, 111, 109, 110, 1),
        Bar("2026-01-03", 99, 100, 98, 99, 1),
        Bar("2026-01-04", 120, 121, 119, 120, 1),
    ]


def test_outcomes_only_score_directional_snapshots_with_known_future_close():
    snapshots = [
        {"date": "2026-01-01", "direction": "偏多", "calibrated_confidence": 0.8},
        {"date": "2026-01-02", "direction": "偏空", "calibrated_confidence": 0.6},
        {"date": "2026-01-04", "direction": "偏多", "calibrated_confidence": 0.9},
        {"date": "2026-01-03", "direction": "中性", "calibrated_confidence": 0.5},
    ]

    outcomes = outcomes_for_horizon(snapshots, _bars(), 1)

    assert [(row.date, row.hit, row.directional_return_pct) for row in outcomes] == [
        ("2026-01-01", True, 10.0),
        ("2026-01-02", True, 10.0),
    ]


def test_replay_report_labels_information_completeness_as_diagnostic_not_probability():
    report = replay_report(
        "BTC", [{"date": "2026-01-01", "direction": "偏多", "calibrated_confidence": 0.8}], _bars(),
    )

    assert report["horizons"]["T+1"]["eligible_predictions"] == 1
    assert report["horizons"]["T+1"]["hit_rate"] == 1.0
    assert "not a validated forecast probability" in report["important_limit"]


def test_replay_report_calculates_confidence_bin_calibration_error():
    report = replay_report(
        "BTC",
        [
            {"date": "2026-01-01", "direction": "偏多", "confidence": 0.8},
            {"date": "2026-01-02", "direction": "偏多", "confidence": 0.8},
        ],
        _bars(),
    )

    horizon = report["horizons"]["T+1"]
    assert horizon["eligible_predictions"] == 2
    assert horizon["hit_rate"] == 0.5
    assert horizon["calibration_error"] == 0.3
    assert horizon["reliability"] == [{
        "range": [0.8, 1.0],
        "count": 2,
        "mean_confidence": 0.8,
        "mean_information_completeness": 0.8,
        "empirical_hit_rate": 0.5,
    }]


def test_load_training_snapshots_filters_to_directional_predictions(tmp_path):
    training_dir = tmp_path / "training-data"
    training_dir.mkdir()
    rows = [
        {"coin": "BTC", "date": "2026-01-01", "direction": "偏多", "confidence": 0.8},
        {"coin": "BTC", "date": "2026-01-02", "direction": "不明", "confidence": 0.6},
        {"coin": "ETH", "date": "2026-01-01", "direction": "偏空", "confidence": 0.7},
    ]
    (training_dir / "sample.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )

    snapshots = load_training_snapshots(training_dir, coin="BTC")

    assert snapshots == [rows[0]]
