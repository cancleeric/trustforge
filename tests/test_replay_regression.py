from trustforge.replay_regression import evaluate_replay_regression_gate


def test_replay_regression_gate_passes_all_horizons_above_thresholds():
    report = {
        "coin": "BTC",
        "horizons": {
            "T+1": {"eligible_predictions": 31, "hit_rate": 0.55, "brier_score_proxy": 0.2},
            "T+7": {"eligible_predictions": 40, "hit_rate": 0.6, "brier_score_proxy": 0.18},
        },
    }

    result = evaluate_replay_regression_gate(report)

    assert result["status"] == "pass"
    assert not result["requires_human_review"]


def test_replay_regression_gate_reports_failures_per_horizon():
    report = {
        "coin": "BTC",
        "horizons": {
            "T+1": {"eligible_predictions": 2, "hit_rate": 0.4, "brier_score_proxy": 0.5},
        },
    }

    result = evaluate_replay_regression_gate(report)

    assert result["status"] == "fail"
    assert result["requires_human_review"]
    assert result["checks"][0]["failures"] == [
        "insufficient_eligible_predictions",
        "hit_rate_below_gate",
        "brier_score_above_gate",
    ]
