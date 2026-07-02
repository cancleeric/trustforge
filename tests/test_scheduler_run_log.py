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


def test_jsonl_run_log_latest_never_calls_read_all(tmp_path, monkeypatch):
    """回歸測試（scalability）：`latest()` 必須是 O(1) 單筆指標讀取，不管
    歷史檔案累積多少筆，都不該去掃 `read_all()`。"""
    log = JsonlSchedulerRunLog(path=tmp_path / "runs.jsonl")
    for i in range(500):
        log.append({"run_id": f"r{i}", "ts": f"2026-01-{(i % 28) + 1:02d}T00:00:00+00:00"})

    calls = {"n": 0}
    original_read_all = JsonlSchedulerRunLog.read_all

    def spy_read_all(self):
        calls["n"] += 1
        return original_read_all(self)

    monkeypatch.setattr(JsonlSchedulerRunLog, "read_all", spy_read_all)

    result = log.latest()
    assert result is not None
    assert calls["n"] == 0  # latest() 完全沒碰 read_all()


def test_jsonl_run_log_latest_cost_independent_of_history_size(tmp_path):
    """append 越多筆，latest 指標檔本身大小仍是常數（單筆 JSON），不隨歷史
    筆數增長——用檔案大小間接驗證「latest 查詢成本不隨歷史增長」。"""
    log = JsonlSchedulerRunLog(path=tmp_path / "runs.jsonl")
    log.append({"run_id": "r0", "ts": "2026-01-01T00:00:00+00:00", "success_count": 1})
    size_after_1 = log._latest_path.stat().st_size

    for i in range(1, 300):
        log.append({
            "run_id": f"r{i}", "ts": f"2026-02-{(i % 28) + 1:02d}T00:00:00+00:00",
            "success_count": 1,
        })
    size_after_300 = log._latest_path.stat().st_size

    # 單筆 record 的序列化大小穩定（欄位不變），不會隨歷史筆數線性成長；
    # 允許些微誤差（例如日期字串長度固定，理論上應完全相等）。
    assert abs(size_after_300 - size_after_1) < 32


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
    mock_table.get_item.return_value = {}  # 尚無 latest 指標
    d._table = mock_table  # 繞過 boto3

    d.append({
        "run_id": "run-1", "ts": "2026-07-01T00:00:00+00:00",
        "success_count": 3, "failure_count": 0, "total_docs": 12.0,
    })

    # append() 寫兩筆：完整歷史記錄 + O(1) latest 指標（固定 Key，見
    # `_update_latest_pointer`），不是只寫一筆。
    assert mock_table.put_item.call_count == 2
    history_item = mock_table.put_item.call_args_list[0].kwargs["Item"]
    pointer_item = mock_table.put_item.call_args_list[1].kwargs["Item"]

    assert history_item["run_id"] == "run-1"
    assert history_item["ts"] == "2026-07-01T00:00:00+00:00"
    assert isinstance(history_item["total_docs"], Decimal)

    assert pointer_item["run_id"] == "__latest__"
    assert pointer_item["ts"] == "__latest__"
    assert pointer_item["source_run_id"] == "run-1"
    assert pointer_item["source_ts"] == "2026-07-01T00:00:00+00:00"

    # latest 指標查詢只 GetItem 一次固定 Key，不 Scan。
    mock_table.get_item.assert_called_once_with(Key=DynamoDBSchedulerRunLog._LATEST_KEY)
    mock_table.scan.assert_not_called()


def test_dynamodb_run_log_append_generates_run_id_and_ts_when_missing():
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.get_item.return_value = {}
    d._table = mock_table

    d.append({"success_count": 1})
    d.append({"success_count": 2})

    # 每次 append 各寫 2 筆（歷史 + 指標），只取歷史那筆比對 run_id/ts。
    history_puts = [
        c.kwargs["Item"] for c in mock_table.put_item.call_args_list
        if c.kwargs["Item"].get("run_id") != "__latest__"
    ]
    assert len(history_puts) == 2
    first, second = history_puts
    assert first["run_id"] and second["run_id"]
    assert first["run_id"] != second["run_id"]
    assert first["ts"]


def test_dynamodb_run_log_append_only_overwrites_latest_pointer_when_strictly_newer():
    """out-of-order append（如時鐘漂移/並發）不該讓較舊的一筆蓋掉較新的
    latest 指標，比照 `JsonlSchedulerRunLog` 對平手/較舊一律不覆寫的語意。"""
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.get_item.return_value = {}
    d._table = mock_table

    d.append({"run_id": "newer", "ts": "2026-01-03T00:00:00+00:00", "success_count": 1})
    pointer_after_first = mock_table.put_item.call_args_list[-1].kwargs["Item"]
    assert pointer_after_first["source_run_id"] == "newer"

    # 模擬目前指標已是 newer（GetItem 回傳剛寫入的內容）
    mock_table.get_item.return_value = {"Item": dict(pointer_after_first)}

    d.append({"run_id": "older", "ts": "2026-01-01T00:00:00+00:00", "success_count": 2})
    # 這次 append 只該寫「歷史記錄」那 1 筆，指標不該被較舊的一筆覆寫。
    latest_call_item = mock_table.put_item.call_args_list[-1].kwargs["Item"]
    assert latest_call_item["run_id"] == "older"  # 是歷史記錄本身，不是指標覆寫


def test_dynamodb_run_log_latest_uses_get_item_not_scan():
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        "Item": {
            "run_id": "__latest__", "ts": "__latest__",
            "source_run_id": "run-9", "source_ts": "2026-07-01T00:00:00+00:00",
            "success_count": Decimal("3"),
        }
    }
    d._table = mock_table

    record = d.latest()

    mock_table.get_item.assert_called_once_with(Key=DynamoDBSchedulerRunLog._LATEST_KEY)
    mock_table.scan.assert_not_called()
    assert record["run_id"] == "run-9"
    assert record["ts"] == "2026-07-01T00:00:00+00:00"
    assert record["success_count"] == 3


def test_dynamodb_run_log_latest_none_when_no_pointer_item():
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.get_item.return_value = {}
    d._table = mock_table

    assert d.latest() is None
    mock_table.scan.assert_not_called()


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
