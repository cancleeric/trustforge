"""排程執行紀錄（`scheduler_log.py`，Phase3 `/status` 「最近排程執行」用）測試。

⛔ 不打真 AWS：`DynamoDBSchedulerRunLog` 一律用 `unittest.mock` 繞過 boto3，
比照 `test_cost_ledger.py::DynamoDBLedger` 的既有 mock 慣例。
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from trustforge.scheduler_log import (
    DynamoDBSchedulerRunLog,
    JsonlSchedulerRunLog,
    SchedulerRunLog,
    append_scheduler_run,
    get_last_scheduler_run,
    get_scheduler_run_log,
)


# ---------------------------------------------------------------------------
# JsonlSchedulerRunLog
# ---------------------------------------------------------------------------

def test_jsonl_run_log_append_and_read_all_roundtrip(tmp_path):
    log = JsonlSchedulerRunLog(path=tmp_path / "runs.jsonl")
    log.append({"run_id": "r1", "ts": "2026-01-01T00:00:00+00:00", "success_count": 3})
    log.append({"run_id": "r2", "ts": "2026-01-02T00:00:00+00:00", "success_count": 5})

    records = log.read_all()
    assert [r["run_id"] for r in records] == ["r1", "r2"]


def test_jsonl_run_log_read_all_empty_when_file_missing(tmp_path):
    log = JsonlSchedulerRunLog(path=tmp_path / "missing.jsonl")
    assert log.read_all() == []


def test_jsonl_run_log_skips_corrupted_lines(tmp_path):
    path = tmp_path / "runs.jsonl"
    path.write_text('{"run_id": "ok", "ts": "t1"}\nnot json\n', encoding="utf-8")
    log = JsonlSchedulerRunLog(path=path)
    records = log.read_all()
    assert len(records) == 1
    assert records[0]["run_id"] == "ok"


def test_jsonl_run_log_latest_picks_max_ts(tmp_path):
    log = JsonlSchedulerRunLog(path=tmp_path / "runs.jsonl")
    log.append({"run_id": "r1", "ts": "2026-01-01T00:00:00+00:00"})
    log.append({"run_id": "r2", "ts": "2026-01-03T00:00:00+00:00"})
    log.append({"run_id": "r3", "ts": "2026-01-02T00:00:00+00:00"})

    latest = log.latest()
    assert latest is not None
    assert latest["run_id"] == "r2"


def test_jsonl_run_log_latest_none_when_empty(tmp_path):
    log = JsonlSchedulerRunLog(path=tmp_path / "runs.jsonl")
    assert log.latest() is None


# ---------------------------------------------------------------------------
# DynamoDBSchedulerRunLog（mock boto3 Table，不打真 AWS）
# ---------------------------------------------------------------------------

def test_dynamodb_run_log_is_subclass():
    assert isinstance(DynamoDBSchedulerRunLog(), SchedulerRunLog)


def test_dynamodb_run_log_construction_does_not_touch_aws(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_SCHEDULER_RUN_TABLE", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    d = DynamoDBSchedulerRunLog()
    assert d.table_name == "trustforge-scheduler-runs"
    assert d.region == "us-east-1"
    assert d._table is None  # lazy，尚未真的碰 AWS SDK


def test_dynamodb_run_log_append_calls_put_item_with_decimal_and_keys():
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    mock_table = MagicMock()
    d._table = mock_table  # 繞過 boto3

    d.append({
        "run_id": "run-1", "ts": "2026-07-01T00:00:00+00:00",
        "success_count": 3, "failure_count": 0, "total_docs": 12.0,
    })

    mock_table.put_item.assert_called_once()
    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["run_id"] == "run-1"
    assert item["ts"] == "2026-07-01T00:00:00+00:00"
    assert isinstance(item["total_docs"], Decimal)


def test_dynamodb_run_log_append_generates_run_id_and_ts_when_missing():
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    mock_table = MagicMock()
    d._table = mock_table

    d.append({"success_count": 1})
    d.append({"success_count": 2})

    first = mock_table.put_item.call_args_list[0].kwargs["Item"]
    second = mock_table.put_item.call_args_list[1].kwargs["Item"]
    assert first["run_id"] and second["run_id"]
    assert first["run_id"] != second["run_id"]
    assert first["ts"]


def test_dynamodb_run_log_read_all_paginates_and_converts_decimal():
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.scan.side_effect = [
        {
            "Items": [{"run_id": "r1", "ts": "t1", "success_count": Decimal("3")}],
            "LastEvaluatedKey": {"run_id": "r1"},
        },
        {"Items": [{"run_id": "r2", "ts": "t2", "success_count": Decimal("5")}]},
    ]
    d._table = mock_table

    records = d.read_all()
    assert {r["run_id"] for r in records} == {"r1", "r2"}
    assert mock_table.scan.call_count == 2
    for r in records:
        assert isinstance(r["success_count"], int)  # 整數值轉回 int，不留 Decimal/float


# ---------------------------------------------------------------------------
# get_scheduler_run_log() / append_scheduler_run() / get_last_scheduler_run()
# ---------------------------------------------------------------------------

def test_get_scheduler_run_log_default_is_jsonl(monkeypatch):
    monkeypatch.delenv("SCHEDULER_RUN_LOG_BACKEND", raising=False)
    assert isinstance(get_scheduler_run_log(), JsonlSchedulerRunLog)


def test_get_scheduler_run_log_dynamodb_when_env_set(monkeypatch):
    monkeypatch.setenv("SCHEDULER_RUN_LOG_BACKEND", "dynamodb")
    assert isinstance(get_scheduler_run_log(), DynamoDBSchedulerRunLog)


def test_append_scheduler_run_writes_to_given_log(tmp_path):
    log = JsonlSchedulerRunLog(path=tmp_path / "runs.jsonl")
    append_scheduler_run({"success_count": 2, "failure_count": 0}, log=log)

    records = log.read_all()
    assert len(records) == 1
    assert records[0]["success_count"] == 2
    assert records[0]["run_id"]  # 自動補上
    assert records[0]["ts"]  # 自動補上


def test_append_scheduler_run_fallback_to_jsonl_on_primary_failure(monkeypatch, tmp_path):
    """primary backend（如 DynamoDB）append 失敗 → 自動 fallback 寫本地
    JsonlSchedulerRunLog，且**不 raise**（呼叫端/排程不該因為 run log 寫入
    失敗而中斷或被誤判成這輪抓取失敗）。"""
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "fallback.jsonl"))

    class BrokenLog(SchedulerRunLog):
        def append(self, record):
            raise RuntimeError("no aws credentials / table not found")

        def read_all(self):
            return []

    # 不 raise：函式本身要吞掉例外
    append_scheduler_run({"success_count": 1}, log=BrokenLog())

    fallback_records = JsonlSchedulerRunLog().read_all()
    assert len(fallback_records) == 1
    assert fallback_records[0]["success_count"] == 1


def test_append_scheduler_run_no_retry_when_target_already_jsonl(tmp_path):
    """target 本身已是 JsonlSchedulerRunLog 且寫入失敗（如路徑不可寫）—— 不重試
    同一路徑，直接靜默結束，不 raise。"""
    class BrokenJsonlLog(JsonlSchedulerRunLog):
        def append(self, record):
            raise OSError("disk full")

    # 不應拋出例外
    append_scheduler_run({"success_count": 1}, log=BrokenJsonlLog(path=tmp_path / "x.jsonl"))


def test_get_last_scheduler_run_returns_latest(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "runs.jsonl"))
    monkeypatch.delenv("SCHEDULER_RUN_LOG_BACKEND", raising=False)

    append_scheduler_run({"ts": "2026-01-01T00:00:00+00:00", "success_count": 1})
    append_scheduler_run({"ts": "2026-01-02T00:00:00+00:00", "success_count": 2})

    latest = get_last_scheduler_run()
    assert latest is not None
    assert latest["success_count"] == 2


def test_get_last_scheduler_run_none_when_no_records(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "empty.jsonl"))
    monkeypatch.delenv("SCHEDULER_RUN_LOG_BACKEND", raising=False)
    assert get_last_scheduler_run() is None


def test_get_last_scheduler_run_degrades_gracefully_when_primary_broken(monkeypatch):
    """primary backend（如 DynamoDB）讀取失敗（缺憑證/表未建）→ 降級回 None
    （或本地 JSONL fallback），絕不拋例外——`/status` 頁面必須永遠能顯示。"""
    monkeypatch.setenv("SCHEDULER_RUN_LOG_BACKEND", "dynamodb")

    class BrokenDynamo(DynamoDBSchedulerRunLog):
        def latest(self):
            raise RuntimeError("AccessDeniedException")

    monkeypatch.setattr(
        "trustforge.scheduler_log.get_scheduler_run_log", lambda: BrokenDynamo()
    )

    assert get_last_scheduler_run() is None  # 不拋例外
