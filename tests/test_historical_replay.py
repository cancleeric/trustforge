import os
import subprocess
import sys
from datetime import datetime, timezone
import pytest
from trustforge.historical_replay import replay_date_range, replay_snapshot

def _snapshot(document, *, snapshot_epoch=None):
    boundary = (
        datetime(2021, 7, 1, tzinfo=timezone.utc).timestamp()
        if snapshot_epoch is None
        else snapshot_epoch
    )
    return {"coin": "BTC", "snapshot_at": "2021-07-01T00:00:00Z", "snapshot_epoch": boundary, "archive_type": "backfilled_archive", "sources": [{"source": "government", "documents": [document]}]}

def test_daily_replay_outputs_report_evidence_and_execution_log():
    result = replay_snapshot(_snapshot({"id": "a", "kind": "regulatory", "text": "BTC regulation update", "published_at": "2021-06-30T12:00:00Z"}), query="test")
    assert result["report"]["coin"] == "BTC"
    assert result["evidence"]
    assert "historical_replay.done" in result["execution_log_jsonl"]

def test_daily_replay_rejects_future_document():
    with pytest.raises(ValueError, match="future document"):
        replay_snapshot(_snapshot({"id": "future", "text": "bad", "published_at": "2021-07-02T00:00:00Z"}), query="test")


@pytest.mark.parametrize("snapshot_epoch", ["nan", "inf", "-inf", 0, -1, "not-a-number"])
def test_daily_replay_rejects_invalid_snapshot_epoch_before_evidence_binding(snapshot_epoch):
    snapshot = _snapshot(
        {"id": "future", "text": "bad", "published_at": "9999-12-31T23:59:59Z"},
        snapshot_epoch=snapshot_epoch,
    )

    with pytest.raises(ValueError, match="finite positive snapshot_epoch"):
        replay_snapshot(snapshot, query="test")


def test_daily_replay_rejects_missing_snapshot_epoch_before_evidence_binding():
    snapshot = _snapshot({"id": "future", "text": "bad", "published_at": "9999-12-31T23:59:59Z"})
    del snapshot["snapshot_epoch"]

    with pytest.raises(ValueError, match="finite positive snapshot_epoch"):
        replay_snapshot(snapshot, query="test")


def test_daily_replay_rejects_timezone_unknown_document():
    with pytest.raises(ValueError, match="timezone-aware published_at"):
        replay_snapshot(_snapshot({"id": "naive", "text": "bad", "published_at": "2021-06-30T12:00:00"}), query="test")


def test_daily_replay_accepts_explicit_offsets_at_boundary():
    for published_at in (
        "2021-06-30T14:00:00+02:00",
        "2021-07-01T13:59:59+14:00",
        "2021-06-30T09:59:59-14:00",
    ):
        result = replay_snapshot(
            _snapshot({"id": published_at, "text": "BTC update", "published_at": published_at}),
            query="test",
        )
        assert result["evidence"][0]["content_reference"] == "BTC update"


def test_daily_replay_normalizes_dst_offset_deterministically():
    result = replay_snapshot(
        _snapshot({"id": "dst", "text": "BTC update", "published_at": "2021-03-14T01:30:00-05:00"}),
        query="test",
    )

    assert result["evidence"][0]["content_reference"] == "BTC update"


def test_replay_timestamp_boundary_is_host_timezone_independent():
    code = (
        "from trustforge.historical_replay import replay_snapshot;"
        "from tests.test_historical_replay import _snapshot;"
        "replay_snapshot(_snapshot({'id':'offset','text':'BTC update','published_at':'2021-06-30T14:00:00+02:00'}), query='test')"
    )
    env = dict(os.environ)
    env["TZ"] = "Pacific/Kiritimati"
    env["PYTHONPATH"] = os.pathsep.join(("src", ".", env.get("PYTHONPATH", "")))
    subprocess.run([sys.executable, "-c", code], check=True, cwd=os.getcwd(), env=env)

def test_replay_range_records_missing_days_without_fabricating_data():
    available = {"2021-07-01": _snapshot({"id": "a", "text": "BTC update", "published_at": "2021-06-30T12:00:00Z"})}
    results, skipped = replay_date_range("BTC", datetime(2021, 7, 1).date(), datetime(2021, 7, 2).date(), query="test", load_snapshot=lambda _coin, day: available.get(day))
    assert len(results) == 1
    assert skipped == [{"date": "2021-07-02", "reason": "snapshot_missing"}]
