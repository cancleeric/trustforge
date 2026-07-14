from trustforge.calibrator_gate import evaluate_calibrator_gate

def test_blocks_before_minimum_leakage_safe_outcomes():
    assert evaluate_calibrator_gate([{"date": "2021-01-01", "hit": True}])["reason"] == "insufficient_eligible_outcomes"

def test_requires_and_constructs_time_separated_holdout():
    rows = [{"date": f"2021-01-{day:03d}", "hit": bool(day % 2)} for day in range(1, 101)]
    result = evaluate_calibrator_gate(rows)
    assert result["eligible"] and result["train_count"] == 80 and result["holdout_count"] == 20
