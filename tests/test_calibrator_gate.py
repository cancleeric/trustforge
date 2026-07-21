from trustforge.calibrator_gate import calibrator_model_gate_status, evaluate_calibrator_gate


def test_blocks_before_minimum_leakage_safe_outcomes():
    gate = evaluate_calibrator_gate([{"date": "2021-01-01", "hit": True}])

    assert gate["reason"] == "insufficient_eligible_outcomes"
    assert gate["remaining"] == 99


def test_requires_and_constructs_time_separated_holdout():
    rows = [{"date": f"2021-01-{day:03d}", "hit": bool(day % 2)} for day in range(1, 101)]

    result = evaluate_calibrator_gate(rows)

    assert result["eligible"]
    assert result["train_count"] == 80
    assert result["holdout_count"] == 20
    assert result["train_end"] == "2021-01-080"
    assert result["holdout_start"] == "2021-01-081"


def test_status_keeps_abstain_model_gate_locked_until_enough_outcomes():
    status = calibrator_model_gate_status([{"date": "2021-01-001", "hit": True}])

    assert status["status"] == "blocked"
    assert status["candidate_calibrator"] is None
    assert status["abstain_policy"]["model_gate"] == "locked_until_holdout_passes"
    assert not status["automatic_apply"]
    assert status["requires_human_approval"]
