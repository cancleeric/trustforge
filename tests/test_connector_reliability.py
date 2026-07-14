from trustforge.connector_reliability import build_reliability_report


def _run(ts, calls=None, failures=None):
    return {
        "ts": ts,
        "targets": ["price", "reddit"],
        "source_calls": calls or {},
        "failures": failures or [],
    }


def test_reliability_gate_counts_attempts_not_freshness_skips():
    records = [_run("09", {"price": 1})]
    records += [_run(f"0{i}", {"price": 1, "reddit": 1}) for i in range(2, 9)]
    report = build_reliability_report(records)
    rows = {row["source"]: row for row in report["sources"]}
    assert rows["price"]["consecutive_successes"] == 8
    assert rows["price"]["meets_reliability_gate"] is True
    assert rows["reddit"]["attempted_runs"] == 7
    assert rows["reddit"]["meets_reliability_gate"] is True


def test_latest_failure_resets_streak_and_parses_coin_suffix():
    records = [
        _run("03", {"price": 1}, ["reddit:BTC"]),
        _run("02", {"price": 1, "reddit": 1}),
        _run("01", {"price": 1, "reddit": 1}),
    ]
    report = build_reliability_report(records)
    rows = {row["source"]: row for row in report["sources"]}
    assert rows["reddit"]["failed_runs"] == 1
    assert rows["reddit"]["consecutive_successes"] == 0
    assert rows["reddit"]["last_failure_at"] == "03"
    assert rows["reddit"]["failure_rate"] == 0.3333


def test_success_streak_stops_at_most_recent_failure():
    records = [
        _run("04", {"reddit": 1}),
        _run("03", {"reddit": 1}),
        _run("02", failures=["reddit:ETH"]),
        _run("01", {"reddit": 1}),
    ]
    row = build_reliability_report(records)["sources"][1]
    assert row["source"] == "reddit"
    assert row["consecutive_successes"] == 2
    assert row["meets_reliability_gate"] is False
