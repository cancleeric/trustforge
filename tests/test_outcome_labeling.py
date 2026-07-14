from trustforge.ingestion.prices import Bar
from trustforge.outcome_labeling import label_replay_outcomes

def test_labels_only_available_future_bars_and_keeps_lineage():
    bars = [Bar("2021-01-01", 1, 1, 1, 100, 1), Bar("2021-01-02", 1, 1, 1, 110, 1), Bar("2021-01-03", 1, 1, 1, 90, 1)]
    labels = label_replay_outcomes([{"coin": "BTC", "snapshot_at": "2021-01-01T00:00:00Z", "report": {"direction": "偏多", "calibrated_confidence": .7}}], bars, {"sha256": "abc"}, horizons=(1, 7))
    assert labels[0]["outcomes"]["T+1"]["hit"] is True
    assert labels[0]["outcomes"]["T+7"] == {"status": "unavailable"}
    assert labels[0]["ohlcv_lineage"]["sha256"] == "abc"
