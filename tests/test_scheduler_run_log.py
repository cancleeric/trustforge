"""排程執行紀錄（`scheduler_log.py`，Phase3 `/status` 「最近排程執行」用）測試。

⛔ 不打真 AWS：`DynamoDBSchedulerRunLog` 一律用 `unittest.mock` 繞過 boto3，
比照 `test_cost_ledger.py::DynamoDBLedger` 的既有 mock 慣例。
"""
from __future__ import annotations

import threading
import time
from decimal import Decimal
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from trustforge.scheduler_log import (
    DynamoDBSchedulerRunLog,
    JsonlSchedulerRunLog,
    SchedulerRunLog,
    append_scheduler_run,
    get_last_scheduler_run,
    get_scheduler_run_log,
)


class FakeConditionalTable:
    """輕量記憶體版 DynamoDB Table 假物件，模擬真正 DynamoDB 對
    `ConditionExpression` 的原子檢查語意（`put_item` 的比較與寫入不可被
    打斷）——用來驗證 `_update_latest_pointer`／`_update_recent_window` 的
    compare-and-set（前者）/樂觀鎖重試（後者）在真實交錯下不會 lost
    update。純記憶體、不打真 AWS，不用 moto。

    支援本檔用到的兩種 condition 形狀：
    - `_update_latest_pointer`：`source_ts not_exists OR source_ts < 新值`，
      直接比較 Item 自帶的 `source_ts` 與目前已存的值。
    - `_update_recent_window`：`_version not_exists OR _version == 剛讀到的
      版本號`（樂觀鎖），用 Item 自帶的 `_version` 與目前已存版本號比較：
      只有當寫入者這次要寫入的版本號剛好是「目前版本號 + 1」（代表它是
      基於目前最新狀態算出來的），才允許寫入；否則視為版本已被別的併發
      寫入者搶先動過，回傳 `ConditionalCheckFailedException`，模擬真實
      DynamoDB 樂觀鎖重試語意。
    不解析 boto3 `ConditionExpression` 物件本身（那是 SDK 內部細節，不該
    綁死在測試替身的實作上），改用 Item 內容本身的欄位形狀判斷是哪一種
    condition。用 `threading.Lock` 包住檢查+寫入，正確模擬 DynamoDB 伺服器
    端「條件檢查與寫入同一個原子操作」的保證，讓多執行緒交錯測試有意義。
    """

    def __init__(self):
        self._store: dict[tuple, dict] = {}
        self._lock = threading.Lock()

    def put_item(self, Item, ConditionExpression=None):  # noqa: N803 — 比照 boto3 API 命名
        key = (Item["run_id"], Item["ts"])
        with self._lock:
            if ConditionExpression is not None:
                existing = self._store.get(key)
                if "_version" in Item:
                    # 樂觀鎖：incoming version 必須恰為「目前已存版本 + 1」
                    incoming_version = Item.get("_version")
                    existing_version = existing.get("_version") if existing else 0
                    expected_prev = (
                        incoming_version - 1 if isinstance(incoming_version, int) else None
                    )
                    if existing is not None and existing_version != expected_prev:
                        raise ClientError(
                            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}},
                            "PutItem",
                        )
                else:
                    new_source_ts = Item.get("source_ts")
                    if existing is not None and existing.get("source_ts") is not None:
                        if not (str(existing["source_ts"]) < str(new_source_ts)):
                            raise ClientError(
                                {
                                    "Error": {
                                        "Code": "ConditionalCheckFailedException",
                                        "Message": "x",
                                    }
                                },
                                "PutItem",
                            )
            self._store[key] = dict(Item)

    def get_item(self, Key):  # noqa: N803
        with self._lock:
            item = self._store.get((Key["run_id"], Key["ts"]))
            return {"Item": dict(item)} if item is not None else {}


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
# JsonlSchedulerRunLog.recent()（成本會計階段2：/status「連接器用量」用）
# ---------------------------------------------------------------------------

def test_jsonl_run_log_recent_returns_newest_first_bounded_by_n(tmp_path):
    log = JsonlSchedulerRunLog(path=tmp_path / "runs.jsonl")
    log.append({"run_id": "r1", "ts": "2026-01-01T00:00:00+00:00"})
    log.append({"run_id": "r2", "ts": "2026-01-03T00:00:00+00:00"})
    log.append({"run_id": "r3", "ts": "2026-01-02T00:00:00+00:00"})

    recent = log.recent(2)
    assert [r["run_id"] for r in recent] == ["r2", "r3"]


def test_jsonl_run_log_recent_empty_when_no_records(tmp_path):
    log = JsonlSchedulerRunLog(path=tmp_path / "runs.jsonl")
    assert log.recent() == []


def test_jsonl_run_log_recent_never_calls_read_all(tmp_path, monkeypatch):
    """回歸測試（scalability，同 `latest()` 慣例）：`recent()` 必須讀 bounded
    window 檔，不管歷史檔案累積多少筆，都不該去掃 `read_all()`。"""
    log = JsonlSchedulerRunLog(path=tmp_path / "runs.jsonl")
    for i in range(200):
        log.append({"run_id": f"r{i}", "ts": f"2026-01-{(i % 28) + 1:02d}T00:00:00+00:00"})

    calls = {"n": 0}
    original_read_all = JsonlSchedulerRunLog.read_all

    def spy_read_all(self):
        calls["n"] += 1
        return original_read_all(self)

    monkeypatch.setattr(JsonlSchedulerRunLog, "read_all", spy_read_all)

    result = log.recent()
    assert len(result) > 0
    assert calls["n"] == 0  # recent() 完全沒碰 read_all()


def test_jsonl_run_log_recent_window_bounded_to_recent_window_size(tmp_path):
    """視窗檔本身只保留最近 `RECENT_WINDOW_SIZE` 筆，append 超過視窗大小的
    紀錄不會讓視窗無限增長（bounded 儲存/讀取成本，不隨歷史筆數線性增長）。"""
    from trustforge.scheduler_log import RECENT_WINDOW_SIZE

    log = JsonlSchedulerRunLog(path=tmp_path / "runs.jsonl")
    total = RECENT_WINDOW_SIZE + 20
    for i in range(total):
        log.append({"run_id": f"r{i}", "ts": f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}T00:00:00+00:00"})

    recent = log.recent(n=1000)  # 要求比視窗大小還多，也只能拿到視窗內已有的筆數
    assert len(recent) == RECENT_WINDOW_SIZE
    # 視窗內是「最近」的那批（append 順序後段），不是最早的那批
    assert "r0" not in {r["run_id"] for r in recent}


def test_jsonl_run_log_recent_source_calls_field_roundtrips(tmp_path):
    """`fetch_scheduler.py` 寫入的 `source_calls` 欄位在 recent window 內
    原封不動保留（成本會計階段2：`/status`「連接器用量」表直接讀這個欄位）。"""
    log = JsonlSchedulerRunLog(path=tmp_path / "runs.jsonl")
    log.append({
        "run_id": "r1", "ts": "2026-01-01T00:00:00+00:00",
        "source_calls": {"coingecko-price": 1, "coindesk": 1},
    })

    recent = log.recent()
    assert recent[0]["source_calls"] == {"coingecko-price": 1, "coindesk": 1}


def test_scheduler_run_log_base_recent_default_is_unoptimized_reference(monkeypatch):
    """`SchedulerRunLog.recent()` 的預設實作（未被子類別 override 時）走
    `read_all()` 排序取前 n 筆——驗證這個未優化參考實作本身邏輯正確
    （`DynamoDBSchedulerRunLog` 目前沿用此預設，見該類別註解）。"""

    class _FakeLog(SchedulerRunLog):
        def append(self, record):
            raise NotImplementedError

        def read_all(self):
            return [
                {"run_id": "a", "ts": "2026-01-01T00:00:00+00:00"},
                {"run_id": "b", "ts": "2026-01-03T00:00:00+00:00"},
                {"run_id": "c", "ts": "2026-01-02T00:00:00+00:00"},
            ]

    fake = _FakeLog()
    recent = fake.recent(2)
    assert [r["run_id"] for r in recent] == ["b", "c"]


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


def test_jsonl_run_log_latest_pointer_interleaved_writers_no_regression(tmp_path, monkeypatch):
    """交錯回歸（HIGH）：`append()` 對 latest 指標的讀-比較-寫是跨執行緒/
    跨行程臨界區（`fcntl.flock`），兩個「排程」交錯呼叫時，較舊的一筆事後
    才嘗試寫入指標不該把指標蓋回舊值。用 monkeypatch 在
    `_write_latest_pointer` 中插入延遲拉長臨界區窗口，逼真交錯（沒有鎖的
    話這個延遲會讓 race 幾乎必然重現；有鎖則序列化執行，結果仍正確）。"""
    log = JsonlSchedulerRunLog(path=tmp_path / "runs.jsonl")

    original_write = JsonlSchedulerRunLog._write_latest_pointer

    def slow_write(self, record):
        time.sleep(0.03)
        original_write(self, record)

    monkeypatch.setattr(JsonlSchedulerRunLog, "_write_latest_pointer", slow_write)

    newer = {"run_id": "run-newer", "ts": "2026-01-03T00:00:00+00:00", "success_count": 2}
    older = {"run_id": "run-older", "ts": "2026-01-01T00:00:00+00:00", "success_count": 1}

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def writer(item, delay_before):
        try:
            barrier.wait(timeout=5)
            time.sleep(delay_before)
            log.append(item)
        except BaseException as exc:  # noqa: BLE001 — 測試執行緒需回報例外供主執行緒斷言
            errors.append(exc)

    t_newer = threading.Thread(target=writer, args=(newer, 0.0))
    t_older = threading.Thread(target=writer, args=(older, 0.01))
    t_newer.start()
    t_older.start()
    t_newer.join(timeout=5)
    t_older.join(timeout=5)

    assert not errors, f"writer 執行緒不該拋例外：{errors}"
    pointer = log.latest()
    assert pointer is not None
    assert pointer["run_id"] == "run-newer"
    assert pointer["ts"] == "2026-01-03T00:00:00+00:00"


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
    mock_table.get_item.return_value = {}  # recent window 尚無項目（表剛建）
    d._table = mock_table  # 繞過 boto3

    d.append({
        "run_id": "run-1", "ts": "2026-07-01T00:00:00+00:00",
        "success_count": 3, "failure_count": 0, "total_docs": 12.0,
    })

    # append() 寫三筆：完整歷史記錄 + O(1) latest 指標（固定 Key，條件式
    # PutItem，見 `_update_latest_pointer`）+ O(1) bounded recent-window 項目
    # （固定 Key，樂觀鎖版本號條件式 PutItem，見 `_update_recent_window`，
    # codex MEDIUM PR #41）。latest 指標維護**不用**GetItem 比較（純原子
    # PutItem+Condition）；recent window 因為是 read-modify-write（無法只
    # 靠比較 ts 表達），才需要先 GetItem 讀目前 window 再樂觀鎖寫回——這是
    # `_update_recent_window` 特有、`_update_latest_pointer` 沒有的行為。
    assert mock_table.get_item.call_count == 1  # 只有 recent window 讀一次
    mock_table.get_item.assert_called_once_with(
        Key=DynamoDBSchedulerRunLog._RECENT_KEY
    )
    assert mock_table.put_item.call_count == 3
    history_item = mock_table.put_item.call_args_list[0].kwargs["Item"]
    pointer_call = mock_table.put_item.call_args_list[1]
    pointer_item = pointer_call.kwargs["Item"]
    window_call = mock_table.put_item.call_args_list[2]
    window_item = window_call.kwargs["Item"]

    assert history_item["run_id"] == "run-1"
    assert history_item["ts"] == "2026-07-01T00:00:00+00:00"
    assert isinstance(history_item["total_docs"], Decimal)

    assert pointer_item["run_id"] == "__latest__"
    assert pointer_item["ts"] == "__latest__"
    assert pointer_item["source_run_id"] == "run-1"
    assert pointer_item["source_ts"] == "2026-07-01T00:00:00+00:00"
    assert "ConditionExpression" in pointer_call.kwargs  # 比較與覆寫原子化

    assert window_item["run_id"] == "__recent__"
    assert window_item["ts"] == "__recent__"
    assert window_item["_version"] == 1  # 第一次寫入，版本號從 1 開始
    assert len(window_item["records"]) == 1
    assert window_item["records"][0]["run_id"] == "run-1"
    assert "ConditionExpression" in window_call.kwargs  # 樂觀鎖原子化

    mock_table.scan.assert_not_called()


def test_dynamodb_run_log_append_generates_run_id_and_ts_when_missing():
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    mock_table = MagicMock()
    d._table = mock_table

    d.append({"success_count": 1})
    d.append({"success_count": 2})

    # 每次 append 各寫 3 筆（歷史 + latest 指標 + recent window），只取
    # 歷史那筆比對 run_id/ts（排除兩個固定 Key 的指標/window 項目）。
    fixed_keys = {"__latest__", "__recent__"}
    history_puts = [
        c.kwargs["Item"] for c in mock_table.put_item.call_args_list
        if c.kwargs["Item"].get("run_id") not in fixed_keys
    ]
    assert len(history_puts) == 2
    first, second = history_puts
    assert first["run_id"] and second["run_id"]
    assert first["run_id"] != second["run_id"]
    assert first["ts"]


def test_dynamodb_run_log_append_only_overwrites_latest_pointer_when_strictly_newer():
    """out-of-order append（如時鐘漂移/並發）不該讓較舊的一筆蓋掉較新的
    latest 指標，比照 `JsonlSchedulerRunLog` 對平手/較舊一律不覆寫的語意。
    用 `FakeConditionalTable`（真的執行條件檢查）而非無腦 MagicMock，才能
    驗證「覆寫被擋下」而不是只驗證「呼叫了 put_item」。"""
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    table = FakeConditionalTable()
    d._table = table

    d.append({"run_id": "newer", "ts": "2026-01-03T00:00:00+00:00", "success_count": 1})
    d.append({"run_id": "older", "ts": "2026-01-01T00:00:00+00:00", "success_count": 2})

    pointer = table.get_item(Key=DynamoDBSchedulerRunLog._LATEST_KEY)["Item"]
    assert pointer["source_run_id"] == "newer"  # 較舊一筆的條件式覆寫被擋下


def test_dynamodb_run_log_update_latest_pointer_swallows_conditional_check_failed():
    """`ConditionalCheckFailedException` 是預期的「已有更新指標」訊號，不該
    往上拋、不該讓 `append()` 的歷史記錄寫入受影響。"""
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.put_item.side_effect = [
        None,  # 歷史記錄寫入成功
        ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "PutItem"
        ),
        None,  # recent window 寫入成功（獨立於 latest 指標，不受影響）
    ]
    d._table = mock_table

    d.append({"run_id": "r1", "ts": "2026-01-01T00:00:00+00:00", "success_count": 1})
    # 沒有拋例外就是成功；歷史記錄 + recent window 那兩筆 put_item 確實有打
    # （side_effect 第一、第三個 None 已被消耗），latest 指標那筆的
    # ConditionalCheckFailedException 被正確吞掉、不影響後續 window 寫入。
    assert mock_table.put_item.call_count == 3


def test_dynamodb_run_log_update_latest_pointer_reraises_other_client_errors():
    """只吞 `ConditionalCheckFailedException`，其他 DynamoDB 錯誤（如
    AccessDenied）仍該往上拋，不能被誤吞成靜默失敗。"""
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.put_item.side_effect = [
        None,
        ClientError({"Error": {"Code": "AccessDeniedException", "Message": "x"}}, "PutItem"),
    ]
    d._table = mock_table

    try:
        d.append({"run_id": "r1", "ts": "2026-01-01T00:00:00+00:00", "success_count": 1})
        assert False, "應該要往上拋 AccessDeniedException"
    except ClientError as exc:
        assert exc.response["Error"]["Code"] == "AccessDeniedException"


def test_dynamodb_run_log_latest_pointer_interleaved_writers_no_regression():
    """交錯回歸（HIGH）：兩個「排程」真的用多執行緒交錯呼叫
    `_update_latest_pointer`（較舊的一筆刻意在較新的一筆之後才嘗試寫入，
    模擬重疊排程的時間差），原子條件式 PutItem 應保證最終指標維持較新的
    ts，不會被較舊的一筆蓋回去——不會出現「A 讀到舊指標 → B 寫入新指標
    → A 仍照著舊狀態把指標蓋回舊值」的 lost update。"""
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    table = FakeConditionalTable()
    d._table = table

    newer_item = {"run_id": "run-newer", "ts": "2026-01-03T00:00:00+00:00", "success_count": 2}
    older_item = {"run_id": "run-older", "ts": "2026-01-01T00:00:00+00:00", "success_count": 1}

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def writer(item, delay_before):
        try:
            barrier.wait(timeout=5)
            time.sleep(delay_before)  # 刻意錯開，逼兩邊都進入臨界區嘗試寫入
            d._update_latest_pointer(table, dict(item))
        except BaseException as exc:  # noqa: BLE001 — 測試執行緒需回報例外供主執行緒斷言
            errors.append(exc)

    t_newer = threading.Thread(target=writer, args=(newer_item, 0.0))
    t_older = threading.Thread(target=writer, args=(older_item, 0.02))
    t_newer.start()
    t_older.start()
    t_newer.join(timeout=5)
    t_older.join(timeout=5)

    assert not errors, f"writer 執行緒不該拋例外：{errors}"
    pointer = table.get_item(Key=DynamoDBSchedulerRunLog._LATEST_KEY)["Item"]
    assert pointer["source_run_id"] == "run-newer"
    assert pointer["source_ts"] == "2026-01-03T00:00:00+00:00"


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


# ---------------------------------------------------------------------------
# DynamoDBSchedulerRunLog.recent()（codex MEDIUM，PR #41：原本繼承
# `SchedulerRunLog.recent()` 的未優化 read_all()+全 Scan 參考實作，改為
# O(1) 固定 Key GetItem，比照 latest()，見 `_update_recent_window`）
# ---------------------------------------------------------------------------

def test_dynamodb_run_log_recent_uses_get_item_not_scan():
    """回歸鎖：`recent()` 只能對固定 Key 做單筆 `GetItem`，**絕不 `Scan`**
    ——這正是 codex 抓到的原始 bug（沒 override，繼承 read_all()+全 Scan）。"""
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        "Item": {
            "run_id": "__recent__", "ts": "__recent__",
            "_version": Decimal("2"),
            "records": [
                {
                    "run_id": "run-2", "ts": "2026-07-02T00:00:00+00:00",
                    "success_count": Decimal("1"), "source_calls": {"coindesk": Decimal("2")},
                },
                {
                    "run_id": "run-1", "ts": "2026-07-01T00:00:00+00:00",
                    "success_count": Decimal("3"), "source_calls": {"coindesk": Decimal("1")},
                },
            ],
        }
    }
    d._table = mock_table

    records = d.recent()

    mock_table.get_item.assert_called_once_with(Key=DynamoDBSchedulerRunLog._RECENT_KEY)
    mock_table.scan.assert_not_called()
    assert [r["run_id"] for r in records] == ["run-2", "run-1"]
    assert records[0]["success_count"] == 1  # Decimal 轉回 int，格式對齊 JsonlSchedulerRunLog
    assert records[0]["source_calls"] == {"coindesk": 2}


def test_dynamodb_run_log_recent_empty_when_no_window_item():
    """表剛建/尚未有任何 append 時，recent window 項目不存在，視為「尚無
    紀錄」，回傳空清單，不拋例外、不 Scan。"""
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.get_item.return_value = {}
    d._table = mock_table

    assert d.recent() == []
    mock_table.scan.assert_not_called()


def test_dynamodb_run_log_recent_window_bounded_to_recent_window_size():
    """append 超過 `RECENT_WINDOW_SIZE` 筆 → `recent()` 只回傳最近 30 筆
    （新到舊），比照 `JsonlSchedulerRunLog` 的 window 語意——DynamoDB
    backend 的 recent window 項目不能無限增長。"""
    from trustforge.scheduler_log import RECENT_WINDOW_SIZE

    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    table = FakeConditionalTable()
    d._table = table

    total = RECENT_WINDOW_SIZE + 10
    for i in range(total):
        d.append({
            "run_id": f"run-{i}", "ts": f"2026-01-01T00:{i:02d}:00+00:00",
            "success_count": 1, "source_calls": {"coindesk": 1},
        })

    records = d.recent()
    assert len(records) == RECENT_WINDOW_SIZE
    assert records[0]["run_id"] == f"run-{total - 1}"  # 新到舊，最新一筆排第一
    ids = {r["run_id"] for r in records}
    # 最舊的 10 筆（run-0..run-9）已被截斷出 window，不該再出現
    for i in range(10):
        assert f"run-{i}" not in ids


def test_dynamodb_run_log_recent_window_interleaved_writers_no_regression():
    """交錯回歸（比照 `_update_latest_pointer` 的既有交錯測試，這次驗證
    recent window 的樂觀鎖重試）：多個「排程」併發 append，不該有任何一筆
    因競態被漏記（lost update）——樂觀鎖版本號衝突時該重試，不是靜默丟棄
    對方剛寫入的記錄。"""
    d = DynamoDBSchedulerRunLog(table_name="fake-table")
    table = FakeConditionalTable()
    d._table = table

    n_writers = 5
    barrier = threading.Barrier(n_writers)
    errors: list[BaseException] = []

    def writer(i):
        try:
            barrier.wait(timeout=5)
            d.append({
                "run_id": f"run-{i}", "ts": f"2026-01-01T00:0{i}:00+00:00",
                "success_count": 1, "source_calls": {"coindesk": 1},
            })
        except BaseException as exc:  # noqa: BLE001 — 測試執行緒需回報例外供主執行緒斷言
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"writer 執行緒不該拋例外：{errors}"
    records = d.recent()
    assert len(records) == n_writers  # 5 筆全都在 window 內，沒有任何一筆因競態被漏記
    assert {r["run_id"] for r in records} == {f"run-{i}" for i in range(n_writers)}


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


class _ThrottledLatestPointerTable(FakeConditionalTable):
    """歷史記錄 PutItem 正常，但 latest 指標（固定 sentinel key）PutItem
    一律拋一個**非** ConditionalCheckFailedException 的 ClientError——模擬
    「歷史寫入成功、latest 指標寫入被節流/暫時失敗」的 split-brain 情境。"""

    def put_item(self, Item, ConditionExpression=None):
        if Item.get("run_id") == DynamoDBSchedulerRunLog._LATEST_KEY["run_id"]:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ProvisionedThroughputExceededException",
                        "Message": "節流（模擬暫時性失敗，非 conditional check）",
                    }
                },
                "PutItem",
            )
        super().put_item(Item, ConditionExpression=ConditionExpression)


def test_get_last_scheduler_run_split_brain_prefers_newer_fallback_over_stale_primary(
    monkeypatch, tmp_path
):
    """split-brain 回歸（MEDIUM）：

    1. 第一輪 append 正常，primary（DynamoDB）latest 指標寫到 run-old。
    2. 第二輪 append：歷史 PutItem 成功，但 latest 指標 PutItem 被節流
       （非 ConditionalCheckFailedException）而失敗 → `DynamoDBSchedulerRunLog
       .append()` 整體拋例外 → `append_scheduler_run()` 判定失敗，fallback
       把完整的新記錄（run-new）寫進本地 JsonlSchedulerRunLog（含它自己的
       新 latest 指標）。
    3. 此時 primary 的 latest 指標仍停在 run-old——`primary.latest()`
       讀取本身**不會拋例外**，只是回傳這份「讀得到但是舊的」指標。

    `get_last_scheduler_run()` 必須兩邊都讀、依 ts 取較新者，回傳 fallback
    裡真正較新的 run-new，不能因為 primary 沒噴例外就直接採信它、隱藏
    fallback 裡更新的一筆。
    """
    monkeypatch.setenv("SCHEDULER_RUN_LOG_BACKEND", "dynamodb")
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "fallback.jsonl"))

    table = FakeConditionalTable()
    healthy_log = DynamoDBSchedulerRunLog(table_name="fake-table")
    healthy_log._table = table

    # 第一輪：一切正常，primary 指標寫到 run-old。
    append_scheduler_run(
        {"run_id": "run-old", "ts": "2026-01-01T00:00:00+00:00", "success_count": 1},
        log=healthy_log,
    )

    # 第二輪：沿用同一份既有資料，但這次的 table 對 latest 指標寫入一律節流失敗。
    throttled_table = _ThrottledLatestPointerTable()
    throttled_table._store = dict(table._store)
    throttled_log = DynamoDBSchedulerRunLog(table_name="fake-table")
    throttled_log._table = throttled_table

    append_scheduler_run(
        {"run_id": "run-new", "ts": "2026-01-02T00:00:00+00:00", "success_count": 2},
        log=throttled_log,
    )

    # primary 的指標依然停在 run-old（節流那次沒寫成功）。
    assert throttled_log.latest()["run_id"] == "run-old"
    # 但 fallback 本地 JSONL 已經有 run-new 這筆真正較新的紀錄。
    assert JsonlSchedulerRunLog().latest()["run_id"] == "run-new"

    monkeypatch.setattr(
        "trustforge.scheduler_log.get_scheduler_run_log", lambda: throttled_log
    )

    result = get_last_scheduler_run()
    assert result is not None
    assert result["run_id"] == "run-new"  # 不被 primary 舊指標蓋掉


def test_get_last_scheduler_run_prefers_primary_when_primary_is_newer(monkeypatch, tmp_path):
    """正常情況（沒有 split-brain）：primary 比本地 fallback JSONL 新，回
    primary 那筆，不會被本地舊的 fallback 蓋過去。"""
    monkeypatch.setenv("SCHEDULER_RUN_LOG_BACKEND", "dynamodb")
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "fallback.jsonl"))

    # 本地 fallback JSONL 裡有一筆很舊的紀錄（例如很久以前真的失敗過一次）。
    JsonlSchedulerRunLog().append(
        {"run_id": "run-ancient", "ts": "2020-01-01T00:00:00+00:00", "success_count": 0}
    )

    table = FakeConditionalTable()
    healthy_log = DynamoDBSchedulerRunLog(table_name="fake-table")
    healthy_log._table = table
    append_scheduler_run(
        {"run_id": "run-fresh", "ts": "2026-01-02T00:00:00+00:00", "success_count": 5},
        log=healthy_log,
    )

    monkeypatch.setattr(
        "trustforge.scheduler_log.get_scheduler_run_log", lambda: healthy_log
    )

    result = get_last_scheduler_run()
    assert result is not None
    assert result["run_id"] == "run-fresh"


def test_get_last_scheduler_run_dual_read_stays_constant_cost(monkeypatch, tmp_path):
    """讀兩邊取較新，仍要保持 O(1)：primary 用不存在 `scan` 方法的假 table
    （呼叫到就會 AttributeError），fallback 只允許呼叫 `latest()`（讀 pointer
    檔），不能呼叫 `read_all()`（掃全表 jsonl）。"""
    monkeypatch.setenv("SCHEDULER_RUN_LOG_BACKEND", "dynamodb")
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "fallback.jsonl"))

    table = FakeConditionalTable()  # 沒有 .scan()，一呼叫就 AttributeError
    primary_log = DynamoDBSchedulerRunLog(table_name="fake-table")
    primary_log._table = table
    append_scheduler_run(
        {"run_id": "run-1", "ts": "2026-01-01T00:00:00+00:00", "success_count": 1},
        log=primary_log,
    )
    monkeypatch.setattr(
        "trustforge.scheduler_log.get_scheduler_run_log", lambda: primary_log
    )

    calls = {"n": 0}
    original_read_all = JsonlSchedulerRunLog.read_all

    def spy_read_all(self):
        calls["n"] += 1
        return original_read_all(self)

    monkeypatch.setattr(JsonlSchedulerRunLog, "read_all", spy_read_all)

    result = get_last_scheduler_run()

    assert result is not None
    assert result["run_id"] == "run-1"
    assert calls["n"] == 0  # fallback 沒有掃全表
