from datetime import datetime, timezone
import pytest
from trustforge.historical_replay import replay_date_range, replay_snapshot

def _snapshot(document):
    boundary = datetime(2021, 7, 1, tzinfo=timezone.utc).timestamp()
    return {"coin": "BTC", "snapshot_at": "2021-07-01T00:00:00Z", "snapshot_epoch": boundary, "archive_type": "backfilled_archive", "sources": [{"source": "government", "documents": [document]}]}

def test_daily_replay_outputs_report_evidence_and_execution_log():
    result = replay_snapshot(_snapshot({"id": "a", "kind": "regulatory", "text": "BTC regulation update", "published_at": "2021-06-30T12:00:00Z"}), query="test")
    assert result["report"]["coin"] == "BTC"
    assert result["evidence"]
    assert "historical_replay.done" in result["execution_log_jsonl"]

def test_daily_replay_rejects_future_document():
    with pytest.raises(ValueError, match="future document"):
        replay_snapshot(_snapshot({"id": "future", "text": "bad", "published_at": "2021-07-02T00:00:00Z"}), query="test")

def test_replay_range_records_missing_days_without_fabricating_data():
    available = {"2021-07-01": _snapshot({"id": "a", "text": "BTC update", "published_at": "2021-06-30T12:00:00Z"})}
    results, skipped = replay_date_range("BTC", datetime(2021, 7, 1).date(), datetime(2021, 7, 2).date(), query="test", load_snapshot=lambda _coin, day: available.get(day))
    assert len(results) == 1
    assert skipped == [{"date": "2021-07-02", "reason": "snapshot_missing"}]
