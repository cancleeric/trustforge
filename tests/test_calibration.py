from trustforge.calibration import outcomes_for_horizon, replay_report
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
