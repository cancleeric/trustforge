import json

from scripts.audit_historical_replay_batch import COINS, audit


def test_audit_requires_backfill_and_both_execution_events(tmp_path):
    log = "\n".join([
        json.dumps({"tool": "historical_replay.start"}),
        json.dumps({"tool": "historical_replay.done"}),
    ])
    for coin in COINS:
        directory = tmp_path / f"five-year-{coin.lower()}"
        directory.mkdir()
        (directory / "index.json").write_text(json.dumps({
            "replayed": 1, "skipped": [{"date": "2024-10-26", "reason": "snapshot_missing"}],
        }))
        (directory / f"{coin.lower()}-2021-07-17.json").write_text(json.dumps({
            "archive_type": "backfilled_archive", "execution_log_jsonl": log,
        }))
        (tmp_path / f"five-year-{coin.lower()}-outcomes.json").write_text(json.dumps({
            "labels": [{"outcomes": {"T+1": {"status": "unavailable"}}}],
        }))

    report = audit(tmp_path)

    assert report["complete"] is True
    assert report["coins"]["BTC"]["artifact_count"] == 1
    assert report["coins"]["BTC"]["eligible_outcomes"] == {"T+1": 0, "T+7": 0, "T+14": 0}
