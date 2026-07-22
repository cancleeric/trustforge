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


def test_explicit_split_accepts_exact_100_row_80_20_boundary():
    rows = [
        {"date": f"2021-{1 + index // 28:02d}-{1 + index % 28:02d}", "hit": True,
         "split": "train" if index < 80 else "val"}
        for index in range(100)
    ]
    assert evaluate_calibrator_gate(rows)["eligible"] is True


def test_explicit_split_fails_closed_for_leak_or_noncontiguous_split():
    rows = [
        {"date": f"2021-{1 + index // 28:02d}-{1 + index % 28:02d}", "hit": True,
         "split": "train" if index < 80 else "val"}
        for index in range(100)
    ]
    rows[20]["split"] = "val"
    rows[80]["split"] = "train"
    assert evaluate_calibrator_gate(rows)["reason"] == "explicit_split_not_chronological"
    rows[20]["split"] = "train"
    rows[80]["split"] = "val"
    rows[99]["date"] = rows[0]["date"]
    assert evaluate_calibrator_gate(rows)["reason"] == "duplicate_outcome_identity"


def test_duplicate_outcome_identity_is_rejected_even_within_one_split():
    rows = [
        {"date": f"2021-{1 + index // 28:02d}-{1 + index % 28:02d}", "hit": True,
         "split": "train" if index < 80 else "val"}
        for index in range(100)
    ]
    rows[1]["date"] = rows[0]["date"]
    assert evaluate_calibrator_gate(rows)["reason"] == "duplicate_outcome_identity"


def test_duplicate_identity_cannot_flood_minimum_gate():
    rows = [{"date": "2021-01-01", "coin": "BTC", "hit": True} for _ in range(100)]
    result = evaluate_calibrator_gate(rows)
    assert result["eligible"] is False
    assert result["reason"] == "duplicate_outcome_identity"
    assert result["eligible_outcomes"] == 1
    assert result["remaining"] == 99


def test_explicit_non_80_20_split_uses_actual_boundary_and_counts():
    rows = [
        {"date": f"2021-{1 + index // 28:02d}-{1 + index % 28:02d}", "hit": True,
         "split": "train" if index < 79 else "val"}
        for index in range(101)
    ]
    gate = evaluate_calibrator_gate(rows)
    assert gate["eligible"] is True
    assert gate["train_count"] == 79
    assert gate["holdout_count"] == 22
    assert gate["train_end"] == rows[78]["date"]
    assert gate["holdout_start"] == rows[79]["date"]
