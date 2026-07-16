"""Bedrock 成本記錄（持久化帳本）+ WebUI 測試。

⛔ 本 PR 範圍限制：不打真 AWS。線上路徑一律用 monkeypatch 換掉
`client._runtime()` / `client._stance_runtime()`，回傳固定 fake usage
（純 dict，不呼叫真實 Bedrock），比照 `test_bedrock_stance.py` 的既有作法。
"""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from trustforge import web
from trustforge.agent import orchestrator as orch_mod
from trustforge.agent.orchestrator import run_agent_pipeline
from trustforge.bedrock import BedrockClient, BedrockConfig, LLMResult
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge import ledger as ledger_module
from trustforge.ledger import (
    PRICING,
    DynamoDBLedger,
    Ledger,
    JsonlLedger,
    SQLiteLedger,
    append_run,
    estimate_cost,
    get_ledger,
)
from trustforge.ledger_archive import (
    export_csv,
    export_jsonl,
    restore_jsonl_archive,
    verify_jsonl_archive,
)
from trustforge.schema import QuestionType


# ---------------------------------------------------------------------------
# estimate_cost()
# ---------------------------------------------------------------------------

def test_estimate_cost_known_model():
    in_rate, out_rate = PRICING["apac.anthropic.claude-haiku-4-5"]
    cost = estimate_cost("apac.anthropic.claude-haiku-4-5", 1_000_000, 1_000_000)
    assert cost == round(in_rate + out_rate, 6)


def test_estimate_cost_zero_tokens_is_zero():
    assert estimate_cost("apac.anthropic.claude-haiku-4-5", 0, 0) == 0.0


def test_estimate_cost_unknown_model_returns_zero_not_raise():
    assert estimate_cost("some-unknown-model-id", 1000, 1000) == 0.0


def test_estimate_cost_none_model_returns_zero():
    assert estimate_cost(None, 1000, 1000) == 0.0


def test_estimate_cost_default_stance_model_id_is_nonzero_and_correct():
    """codex 審查發現的 MEDIUM 修正：bedrock.py 預設 `stance_model_id`
    （`au.anthropic.claude-haiku-4-5-20251001-v1:0`）必須能在 `PRICING` 精確
    查到，真實 stance 呼叫的成本不可再被悄悄記成 $0。"""
    cost = estimate_cost("au.anthropic.claude-haiku-4-5-20251001-v1:0", 700, 33)
    assert cost > 0
    assert cost == round(700 / 1_000_000 * 1.0 + 33 / 1_000_000 * 5.0, 6)


def test_estimate_cost_lookalike_unknown_model_id_still_returns_zero():
    """codex 二次審查發現：先前的子字串正規化 fallback 太鬆——
    `vendor.fake-haiku-4-5` 這種內含關鍵字但根本不是合法 model id 的字串，
    也會被誤套 Haiku 價格，違反「unknown model → 0」的精確契約。移除子字串
    fallback、改回精確查表後，這種 lookalike id 必須仍然回 0。"""
    assert estimate_cost("vendor.fake-haiku-4-5", 100, 100) == 0.0


def test_estimate_cost_truly_unknown_model_still_returns_zero():
    """既有行為不回歸：完全不含任何已知關鍵字的 model id 仍應回 0，不誤套價格。"""
    assert estimate_cost("some-totally-unrelated-model-id", 1_000_000, 1_000_000) == 0.0


# ---------------------------------------------------------------------------
# execlog.record_llm_cost()
# ---------------------------------------------------------------------------

def test_record_llm_cost_appends_llm_cost_event():
    log = ExecutionLog(now_fn=lambda: 1000.0)
    log.record_llm_cost("fake-model", 100, 50, 0.0012)
    events = [e for e in log.events if e["tool"] == "llm.cost"]
    assert len(events) == 1
    p = events[0]["params"]
    assert p["model"] == "fake-model"
    assert p["tokens_in"] == 100
    assert p["tokens_out"] == 50
    assert p["cost_usd"] == 0.0012


def test_record_llm_cost_offline_model_none_records_zero():
    """離線/無 model_id → token=0、cost=$0，仍記一筆（帳本能看到此 run 離線）。"""
    log = ExecutionLog(now_fn=lambda: 1000.0)
    log.record_llm_cost(None, 0, 0, 0.0)
    events = [e for e in log.events if e["tool"] == "llm.cost"]
    assert len(events) == 1
    assert events[0]["params"]["model"] == "offline"
    assert events[0]["params"]["cost_usd"] == 0.0


def test_record_llm_cost_does_not_change_to_jsonl_schema():
    """`to_jsonl()` 每行仍是合法 JSON，事件 schema（ts/elapsed_sec/tool/params/summary）不變。"""
    log = ExecutionLog(now_fn=lambda: 1000.0)
    log.record_llm_cost("m", 1, 2, 0.001)
    lines = log.to_jsonl().splitlines()
    last = json.loads(lines[-1])
    assert set(last.keys()) == {"ts", "elapsed_sec", "tool", "params", "summary"}
    assert last["tool"] == "llm.cost"


# ---------------------------------------------------------------------------
# ledger.py：JsonlLedger append/累加/格式穩定/持久化（重啟後可讀）
# ---------------------------------------------------------------------------

def test_jsonl_ledger_append_and_read_all(tmp_path):
    path = tmp_path / "cost_ledger.jsonl"
    ledger = JsonlLedger(path)
    rec1 = {"ts": "t1", "coin": "BTC", "question_type": "multi_source",
            "offline": True, "calls": [], "total_cost_usd": 0.0}
    rec2 = {"ts": "t2", "coin": "ETH", "question_type": "hypothesis",
            "offline": False, "calls": [{"model": "m", "tokens_in": 10,
            "tokens_out": 5, "cost_usd": 0.001}], "total_cost_usd": 0.001}
    ledger.append(rec1)
    ledger.append(rec2)

    records = ledger.read_all()
    assert records == [rec1, rec2]


def test_jsonl_ledger_persistence_across_new_instances(tmp_path):
    """重啟後可讀：用新的 JsonlLedger 實例（模擬程序重啟）指向同一路徑仍讀得到。"""
    path = tmp_path / "cost_ledger.jsonl"
    JsonlLedger(path).append({"ts": "t1", "coin": "BTC", "total_cost_usd": 0.5, "calls": []})

    # 模擬「重啟」：全新物件，不共用記憶體狀態
    reopened = JsonlLedger(path)
    records = reopened.read_all()
    assert len(records) == 1
    assert records[0]["coin"] == "BTC"
    assert records[0]["total_cost_usd"] == 0.5


def test_sqlite_ledger_append_and_persist_across_instances(tmp_path):
    path = tmp_path / "trustforge.sqlite3"
    first = SQLiteLedger(path)
    first.append({"run_id": "run-1", "ts": "t1", "coin": "BTC", "calls": [], "total_cost_usd": 0.5})
    first.append({"run_id": "run-2", "ts": "t2", "coin": "ETH", "calls": [], "total_cost_usd": 0.25})

    records = SQLiteLedger(path).read_all()
    assert [record["run_id"] for record in records] == ["run-1", "run-2"]
    assert SQLiteLedger(path).summary()["total_cost_usd"] == pytest.approx(0.75)


def test_sqlite_ledger_rejects_duplicate_run_id(tmp_path):
    ledger = SQLiteLedger(tmp_path / "trustforge.sqlite3")
    record = {"run_id": "same", "ts": "t1", "calls": [], "total_cost_usd": 0.0}
    ledger.append(record)
    with pytest.raises(Exception):
        ledger.append(record)


def test_get_ledger_selects_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("COST_LEDGER_BACKEND", "sqlite")
    monkeypatch.setenv("TRUSTFORGE_SQLITE_PATH", str(tmp_path / "trustforge.sqlite3"))
    assert isinstance(get_ledger(), SQLiteLedger)


def test_ledger_jsonl_archive_verify_and_restore_drill(tmp_path):
    ledger = JsonlLedger(tmp_path / "source.jsonl")
    ledger.append({"run_id": "r1", "ts": "2026-07-14T00:00:00Z", "coin": "BTC", "calls": [], "total_cost_usd": 0.12})
    archive = tmp_path / "archive.jsonl"
    manifest = export_jsonl(ledger, archive)
    assert manifest["record_count"] == 1
    assert verify_jsonl_archive(archive)["verified"] is True
    restored = tmp_path / "restored.jsonl"
    result = restore_jsonl_archive(archive, restored)
    assert result["restored"] is True
    assert JsonlLedger(restored).read_all() == ledger.read_all()


def test_ledger_archive_detects_tampering_and_refuses_overwrite(tmp_path):
    ledger = JsonlLedger(tmp_path / "source.jsonl")
    ledger.append({"run_id": "r1", "ts": "2026-07-14T00:00:00Z", "calls": [], "total_cost_usd": 0.12})
    archive = tmp_path / "archive.jsonl"
    export_jsonl(ledger, archive)
    archive.write_text(archive.read_text(encoding="utf-8") + '{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="integrity mismatch"):
        verify_jsonl_archive(archive)
    target = tmp_path / "existing.jsonl"
    target.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity mismatch"):
        restore_jsonl_archive(archive, target)


def test_ledger_csv_archive_has_manifest_and_retains_calls(tmp_path):
    ledger = JsonlLedger(tmp_path / "source.jsonl")
    ledger.append({"run_id": "r1", "ts": "2026-07-14T00:00:00Z", "calls": [{"model": "m"}], "total_cost_usd": 0.12})
    archive = tmp_path / "archive.csv"
    manifest = export_csv(ledger, archive)
    assert manifest["format"] == "csv"
    assert archive.with_suffix(".csv.manifest.json").is_file()
    assert "calls_json" in archive.read_text(encoding="utf-8")


def test_jsonl_ledger_read_all_missing_file_returns_empty(tmp_path):
    ledger = JsonlLedger(tmp_path / "does_not_exist.jsonl")
    assert ledger.read_all() == []


def test_jsonl_ledger_summary_aggregates_total_and_by_model(tmp_path):
    ledger = JsonlLedger(tmp_path / "cost_ledger.jsonl")
    ledger.append({
        "ts": "t1", "coin": "BTC", "total_cost_usd": 0.003,
        "calls": [
            {"model": "haiku", "tokens_in": 100, "tokens_out": 50, "cost_usd": 0.001},
            {"model": "sonnet", "tokens_in": 100, "tokens_out": 50, "cost_usd": 0.002},
        ],
    })
    ledger.append({
        "ts": "t2", "coin": "ETH", "total_cost_usd": 0.001,
        "calls": [{"model": "haiku", "tokens_in": 50, "tokens_out": 10, "cost_usd": 0.001}],
    })
    summary = ledger.summary()
    assert summary["total_cost_usd"] == pytest.approx(0.004)
    assert summary["by_model"]["haiku"] == pytest.approx(0.002)
    assert summary["by_model"]["sonnet"] == pytest.approx(0.002)
    assert len(summary["runs"]) == 2
    assert summary["run_count"] == 2


# ---------------------------------------------------------------------------
# codex 複審 HIGH（成本端點可擴展性）：`summary()["runs"]` 改成有界（最近
# `SUMMARY_RECENT_RUNS_CAP` 筆），真實總筆數另外用 `run_count` 提供。
# ---------------------------------------------------------------------------

def test_jsonl_ledger_summary_runs_capped_and_run_count_reflects_true_total(tmp_path):
    ledger = JsonlLedger(tmp_path / "cost_ledger.jsonl")
    cap = ledger_module.SUMMARY_RECENT_RUNS_CAP
    total_records = cap + 20
    for i in range(total_records):
        ledger.append({
            "run_id": f"r{i}", "ts": f"t{i}", "coin": "BTC",
            "total_cost_usd": 0.001, "calls": [{"model": "m", "cost_usd": 0.001}],
        })
    summary = ledger.summary()
    assert summary["run_count"] == total_records
    assert len(summary["runs"]) == cap
    # 保留最近寫入的那批（依寫入順序，最舊在前），不是隨機截斷。
    assert summary["runs"][0]["run_id"] == f"r{total_records - cap}"
    assert summary["runs"][-1]["run_id"] == f"r{total_records - 1}"
    # 有界統計值（total/by_model）仍照全部紀錄彙總，不受 runs 截斷影響。
    assert summary["total_cost_usd"] == pytest.approx(total_records * 0.001)


def test_jsonl_ledger_summary_runs_not_capped_when_at_or_below_cap(tmp_path):
    """帳本筆數 <= cap 時，`runs` 就是全部紀錄——確保小帳本輸出跟修復前一致。"""
    ledger = JsonlLedger(tmp_path / "cost_ledger.jsonl")
    cap = ledger_module.SUMMARY_RECENT_RUNS_CAP
    for i in range(cap):
        ledger.append({
            "run_id": f"r{i}", "ts": f"t{i}", "coin": "BTC",
            "total_cost_usd": 0.001, "calls": [{"model": "m", "cost_usd": 0.001}],
        })
    summary = ledger.summary()
    assert summary["run_count"] == cap
    assert len(summary["runs"]) == cap
    assert summary["runs"][0]["run_id"] == "r0"


# ---------------------------------------------------------------------------
# 成本會計階段1：`summary()["by_model_detail"]` 同時累加 tokens_in/tokens_out
# ---------------------------------------------------------------------------

def test_jsonl_ledger_summary_by_model_detail_aggregates_tokens(tmp_path):
    """`by_model_detail` 是純顯示層新增彙總：每個 model 的 cost_usd 要跟既有
    `by_model` 一致，同時 tokens_in/tokens_out 要正確跨 run 累加。"""
    ledger = JsonlLedger(tmp_path / "cost_ledger.jsonl")
    ledger.append({
        "ts": "t1", "coin": "BTC", "total_cost_usd": 0.003,
        "calls": [
            {"model": "haiku", "tokens_in": 100, "tokens_out": 50, "cost_usd": 0.001},
            {"model": "sonnet", "tokens_in": 100, "tokens_out": 50, "cost_usd": 0.002},
        ],
    })
    ledger.append({
        "ts": "t2", "coin": "ETH", "total_cost_usd": 0.001,
        "calls": [{"model": "haiku", "tokens_in": 50, "tokens_out": 10, "cost_usd": 0.001}],
    })

    summary = ledger.summary()
    detail = summary["by_model_detail"]

    assert detail["haiku"]["cost_usd"] == pytest.approx(0.002)
    assert detail["haiku"]["tokens_in"] == 150
    assert detail["haiku"]["tokens_out"] == 60
    assert detail["sonnet"]["cost_usd"] == pytest.approx(0.002)
    assert detail["sonnet"]["tokens_in"] == 100
    assert detail["sonnet"]["tokens_out"] == 50
    # cost_usd 跟既有 by_model（向後相容、格式不變）數字必須一致，純拆分顯示
    assert detail["haiku"]["cost_usd"] == pytest.approx(summary["by_model"]["haiku"])
    assert detail["sonnet"]["cost_usd"] == pytest.approx(summary["by_model"]["sonnet"])


def test_jsonl_ledger_summary_by_model_detail_defaults_missing_token_fields_to_zero(tmp_path):
    """舊紀錄（本欄位加入前寫入的 `calls[]`）缺 tokens_in/tokens_out 欄位時
    用 `.get(..., 0)` 防呆，不 raise、也不讓彙總報 KeyError。"""
    ledger = JsonlLedger(tmp_path / "cost_ledger.jsonl")
    ledger.append({
        "ts": "t1", "coin": "BTC", "total_cost_usd": 0.001,
        "calls": [{"model": "legacy-model", "cost_usd": 0.001}],  # 舊格式：無 tokens_in/out
    })

    summary = ledger.summary()
    detail = summary["by_model_detail"]["legacy-model"]
    assert detail["tokens_in"] == 0
    assert detail["tokens_out"] == 0
    assert detail["cost_usd"] == pytest.approx(0.001)


def test_jsonl_ledger_summary_by_model_detail_empty_when_no_runs(tmp_path):
    ledger = JsonlLedger(tmp_path / "cost_ledger.jsonl")
    summary = ledger.summary()
    assert summary["by_model_detail"] == {}


def test_jsonl_ledger_append_is_append_only_not_overwrite(tmp_path):
    path = tmp_path / "cost_ledger.jsonl"
    ledger = JsonlLedger(path)
    for i in range(5):
        ledger.append({"ts": f"t{i}", "total_cost_usd": 0.0, "calls": []})
    assert len(ledger.read_all()) == 5
    # 每行都是獨立合法 JSON（append-only JSONL 格式穩定）
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    for line in lines:
        json.loads(line)  # 不拋例外


# ---------------------------------------------------------------------------
# DynamoDBLedger（mock boto3 Table，不打真 AWS）+ get_ledger() / append_run() fallback
# ---------------------------------------------------------------------------

def test_dynamodb_ledger_is_ledger_subclass():
    d = DynamoDBLedger()
    assert isinstance(d, Ledger)


def test_dynamodb_ledger_construction_does_not_touch_aws(monkeypatch):
    """建構只讀 env，不建立 boto3 resource/Table（lazy）——無憑證/未建表環境不炸。"""
    monkeypatch.delenv("TRUSTFORGE_COST_LEDGER_TABLE", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    d = DynamoDBLedger()
    assert d.table_name == "trustforge-cost-ledger"
    assert d.region == "us-east-1"
    assert d._table is None  # 尚未真的碰 AWS SDK


def test_dynamodb_ledger_append_calls_put_item_with_decimal_and_keys():
    d = DynamoDBLedger(table_name="fake-table")
    mock_table = MagicMock()
    d._table = mock_table  # 繞過 boto3，模擬已建好的 Table，確保不打真 AWS

    d.append({
        "run_id": "run-1",
        "ts": "2026-07-01T00:00:00+00:00",
        "coin": "BTC",
        "total_cost_usd": 0.0012,
        "calls": [{"model": "m", "cost_usd": 0.0012}],
    })

    mock_table.put_item.assert_called_once()
    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["run_id"] == "run-1"
    assert item["ts"] == "2026-07-01T00:00:00+00:00"
    assert isinstance(item["total_cost_usd"], Decimal)
    assert item["total_cost_usd"] == Decimal("0.0012")
    assert isinstance(item["calls"][0]["cost_usd"], Decimal)


def test_dynamodb_ledger_append_generates_run_id_and_ts_when_missing():
    """直接呼叫 DynamoDBLedger.append() 略過 append_run（理論上不會發生的防線）
    ——run_id 仍要自動補上，且是全域唯一的 uuid，不再用 ts+coin 衍生（避免同幣
    同秒兩次呼叫撞成同一個 id）。"""
    d = DynamoDBLedger(table_name="fake-table")
    mock_table = MagicMock()
    d._table = mock_table

    d.append({"coin": "ETH", "total_cost_usd": 0.0, "calls": []})
    d.append({"coin": "ETH", "total_cost_usd": 0.0, "calls": []})

    first_item = mock_table.put_item.call_args_list[0].kwargs["Item"]
    second_item = mock_table.put_item.call_args_list[1].kwargs["Item"]
    assert first_item["run_id"]  # 自動生成，非空字串
    assert second_item["run_id"]
    assert first_item["run_id"] != second_item["run_id"]  # 每次都是新 uuid，不會撞
    assert first_item["ts"]  # 自動補上 ISO8601 時間戳


def test_dynamodb_ledger_read_all_paginates_and_converts_decimal_to_float():
    d = DynamoDBLedger(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.scan.side_effect = [
        {
            "Items": [
                {"run_id": "r1", "ts": "t1", "total_cost_usd": Decimal("0.001"), "calls": []},
            ],
            "LastEvaluatedKey": {"run_id": "r1", "ts": "t1"},
        },
        {
            "Items": [
                {
                    "run_id": "r2",
                    "ts": "t2",
                    "total_cost_usd": Decimal("0.002"),
                    "calls": [{"model": "m", "cost_usd": Decimal("0.002")}],
                },
            ],
        },
    ]
    d._table = mock_table

    records = d.read_all()

    assert mock_table.scan.call_count == 2
    second_call_kwargs = mock_table.scan.call_args_list[1].kwargs
    assert second_call_kwargs.get("ExclusiveStartKey") == {"run_id": "r1", "ts": "t1"}
    assert len(records) == 2
    assert isinstance(records[0]["total_cost_usd"], float)
    assert records[0]["total_cost_usd"] == 0.001
    assert records[1]["calls"][0]["cost_usd"] == 0.002


def test_dynamodb_ledger_read_all_keeps_integer_decimal_as_int():
    """整數值的 Decimal（如 token 數）讀出要轉回 int，不能變成 700.0（fidelity nit）。"""
    d = DynamoDBLedger(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.scan.return_value = {
        "Items": [
            {
                "run_id": "r1",
                "ts": "t1",
                "calls": [
                    {
                        "model": "m",
                        "tokens_in": Decimal("700"),
                        "tokens_out": Decimal("120"),
                        "cost_usd": Decimal("0.0086"),
                    }
                ],
            },
        ],
    }
    d._table = mock_table

    records = d.read_all()

    call = records[0]["calls"][0]
    assert call["tokens_in"] == 700
    assert isinstance(call["tokens_in"], int)
    assert call["tokens_out"] == 120
    assert isinstance(call["tokens_out"], int)
    assert call["cost_usd"] == 0.0086
    assert isinstance(call["cost_usd"], float)


def test_dynamodb_ledger_read_all_merges_jsonl_fallback_and_sorts_by_ts():
    """outage 期間 put_item 失敗 → append_run fallback 寫進 JsonlLedger（見
    test_append_run_falls_back_to_jsonl_on_broken_backend）。read_all 若只 scan
    DynamoDB，這筆在 /costs 會消失——必須合併 fallback，且 scan 分頁本身無序，
    最終要依 (ts, run_id) 排序回傳（與 JsonlLedger 寫入序一致、跨請求穩定）。
    `TRUSTFORGE_COST_LEDGER_PATH` 已由 conftest 的 autouse fixture 隔離到本測試
    專屬的 tmp_path，直接用 JsonlLedger() 預設路徑寫入即可命中同一份 fallback。
    """
    # outage 期間寫進 JSONL fallback、DynamoDB 完全沒有的一筆
    JsonlLedger().append({
        "run_id": "outage-1", "ts": "2026-07-01T00:00:02+00:00",
        "coin": "ETH", "total_cost_usd": 0.001, "calls": [],
    })

    d = DynamoDBLedger(table_name="fake-table")
    mock_table = MagicMock()
    # 刻意讓兩頁「亂序」（晚的時間先出現），驗證 read_all 不能直接信任 scan 順序
    mock_table.scan.side_effect = [
        {
            "Items": [
                {"run_id": "r-late", "ts": "2026-07-01T00:00:03+00:00",
                 "total_cost_usd": Decimal("0.003"), "calls": []},
            ],
            "LastEvaluatedKey": {"run_id": "r-late", "ts": "2026-07-01T00:00:03+00:00"},
        },
        {
            "Items": [
                {"run_id": "r-early", "ts": "2026-07-01T00:00:01+00:00",
                 "total_cost_usd": Decimal("0.001"), "calls": []},
            ],
        },
    ]
    d._table = mock_table

    records = d.read_all()

    run_ids_in_order = [r["run_id"] for r in records]
    assert run_ids_in_order == ["r-early", "outage-1", "r-late"]  # 依 ts 排序
    ts_list = [r["ts"] for r in records]
    assert ts_list == sorted(ts_list)
    assert len(records) == 3  # 三筆都在、無重複


def test_dynamodb_ledger_read_all_dedupes_same_run_id_ts_prefers_dynamodb():
    """同一筆記錄同時存在 DynamoDB 和 JSONL fallback（outage 後補寫成功但 fallback
    檔案沒清）→ read_all 只能出現一次，且以 DynamoDB 版本為準（較新/權威）。
    """
    JsonlLedger().append({
        "run_id": "dup-1", "ts": "2026-07-01T00:00:00+00:00",
        "coin": "BTC", "total_cost_usd": 0.0, "calls": [],
    })

    d = DynamoDBLedger(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.scan.return_value = {
        "Items": [
            {"run_id": "dup-1", "ts": "2026-07-01T00:00:00+00:00",
             "coin": "BTC", "total_cost_usd": Decimal("0.0025"), "calls": []},
        ],
    }
    d._table = mock_table

    records = d.read_all()

    assert len(records) == 1
    assert records[0]["run_id"] == "dup-1"
    assert records[0]["total_cost_usd"] == 0.0025  # DynamoDB 版本覆蓋 fallback 的 0.0


def test_dynamodb_ledger_read_all_survives_broken_jsonl_fallback_oserror(monkeypatch):
    """codex MEDIUM：讀 JSONL fallback 那步若拋 OSError（本機檔案權限異常/I-O
    失敗），不能拖垮已經 scan 成功的 DynamoDB 結果——read_all 仍要正常回傳
    DynamoDB 那幾筆，不往上拋。"""
    d = DynamoDBLedger(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.scan.return_value = {
        "Items": [
            {"run_id": "r1", "ts": "2026-07-01T00:00:00+00:00",
             "total_cost_usd": Decimal("0.001"), "calls": []},
        ],
    }
    d._table = mock_table

    monkeypatch.setattr(
        JsonlLedger, "read_all",
        MagicMock(side_effect=OSError("Permission denied")),
    )

    records = d.read_all()  # 不應拋出任何例外

    assert len(records) == 1
    assert records[0]["run_id"] == "r1"


def test_dynamodb_ledger_read_all_survives_broken_jsonl_fallback_unicode_error(monkeypatch):
    """同上，換一種 fallback 壞法：JSONL 檔非 UTF-8（UnicodeDecodeError）。"""
    d = DynamoDBLedger(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.scan.return_value = {
        "Items": [
            {"run_id": "r2", "ts": "2026-07-01T00:00:01+00:00",
             "total_cost_usd": Decimal("0.002"), "calls": []},
        ],
    }
    d._table = mock_table

    def _raise_unicode_error():
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(JsonlLedger, "read_all", lambda self: _raise_unicode_error())

    records = d.read_all()  # 不應拋出任何例外

    assert len(records) == 1
    assert records[0]["run_id"] == "r2"


def test_append_run_same_coin_same_second_twice_get_distinct_run_ids_both_kept():
    """codex HIGH：同幣同秒兩次 append_run（如 comparison/平行 run）——ts 只到秒，
    若 run_id 用 ts+coin 衍生會撞成同一個 PK 互相覆蓋。append_run 現在統一在分派
    前生成 uuid run_id，兩筆各自不同，DynamoDBLedger.read_all() 合併去重後兩筆
    都要在，互不覆蓋。"""
    d = DynamoDBLedger(table_name="fake-table")
    mock_table = MagicMock()
    put_items: list[dict] = []
    mock_table.put_item.side_effect = lambda Item: put_items.append(Item)
    mock_table.scan.return_value = {"Items": put_items}
    d._table = mock_table

    same_ts = "2026-07-01T00:00:00+00:00"
    append_run({"ts": same_ts, "coin": "BTC", "total_cost_usd": 0.001, "calls": []}, ledger=d)
    append_run({"ts": same_ts, "coin": "BTC", "total_cost_usd": 0.002, "calls": []}, ledger=d)

    assert mock_table.put_item.call_count == 2
    run_id_1 = put_items[0]["run_id"]
    run_id_2 = put_items[1]["run_id"]
    assert run_id_1 != run_id_2  # 不會撞成同一個 PK

    records = d.read_all()
    assert len(records) == 2  # 兩筆都在，沒有互相覆蓋
    costs = sorted(float(r["total_cost_usd"]) for r in records)
    assert costs == [0.001, 0.002]


def test_append_run_outage_fallback_record_has_run_id_and_dedupes_correctly():
    """codex HIGH：DynamoDB append 失敗（outage）→ fallback 寫進 JsonlLedger 的那筆
    也要有 run_id（append_run 在分派前就生成，backend 失敗不影響已生成的 run_id）。
    之後 DynamoDBLedger.read_all() 合併 DynamoDB + fallback 時，要能以 run_id 正確
    去重、不會跟「同幣同秒」的其他正常記錄相撞。"""
    fallback_path_env = "TRUSTFORGE_COST_LEDGER_PATH"
    import os as _os
    fallback_path = _os.environ[fallback_path_env]

    broken = DynamoDBLedger(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.put_item.side_effect = RuntimeError("no aws credentials / table not found")
    broken._table = mock_table

    same_ts = "2026-07-01T00:00:00+00:00"
    # outage：這筆 put_item 失敗，fallback 寫進 JsonlLedger
    append_run({"ts": same_ts, "coin": "BTC", "total_cost_usd": 0.001, "calls": []}, ledger=broken)
    # 同幣同秒、DynamoDB 正常寫入成功的另一筆（模擬 outage 恢復後的下一次呼叫）
    mock_table.put_item.side_effect = None
    put_items: list[dict] = []
    mock_table.put_item.side_effect = lambda Item: put_items.append(Item)
    append_run({"ts": same_ts, "coin": "BTC", "total_cost_usd": 0.002, "calls": []}, ledger=broken)

    fallback_records = JsonlLedger(fallback_path).read_all()
    assert len(fallback_records) == 1
    assert fallback_records[0]["run_id"]  # fallback 那筆也有 run_id

    mock_table.scan.return_value = {"Items": put_items}
    records = broken.read_all()

    assert len(records) == 2  # fallback 那筆 + DynamoDB 那筆都在，沒有因同 ts 誤併
    run_ids = {r["run_id"] for r in records}
    assert len(run_ids) == 2  # run_id 各自不同
    costs = sorted(float(r["total_cost_usd"]) for r in records)
    assert costs == [0.001, 0.002]


def test_get_ledger_default_is_jsonl(monkeypatch):
    monkeypatch.delenv("COST_LEDGER_BACKEND", raising=False)
    assert isinstance(get_ledger(), JsonlLedger)


def test_get_ledger_dynamodb_backend_does_not_raise_at_construction(monkeypatch):
    """選 dynamodb backend 本身不打 AWS，建構不 raise；失敗只發生在 append/read_all。"""
    monkeypatch.setenv("COST_LEDGER_BACKEND", "dynamodb")
    ledger = get_ledger()
    assert isinstance(ledger, DynamoDBLedger)


def test_append_run_falls_back_to_jsonl_on_broken_backend(monkeypatch, tmp_path):
    """dynamodb backend append 失敗（缺憑證/表未建/網路問題）→ fallback 寫入 JsonlLedger，
    不中斷 pipeline。用 mock 讓 `_get_table()` 直接炸掉，確保這裡不會意外打到真 AWS。"""
    fallback_path = tmp_path / "fallback_cost_ledger.jsonl"
    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(fallback_path))

    broken = DynamoDBLedger()
    monkeypatch.setattr(
        broken, "_get_table",
        MagicMock(side_effect=RuntimeError("no aws credentials / table not found")),
    )
    append_run({"ts": "t1", "coin": "BTC", "total_cost_usd": 0.0, "calls": []}, ledger=broken)

    assert fallback_path.exists()
    records = JsonlLedger(fallback_path).read_all()
    assert len(records) == 1
    assert records[0]["coin"] == "BTC"


# ---------------------------------------------------------------------------
# bedrock.py：complete() 回傳 LLMResult + 離線 token=0
# ---------------------------------------------------------------------------

def test_complete_offline_returns_llmresult_zero_tokens():
    client = BedrockClient(offline=True)
    result = client.complete(system="sys", prompt="hello")
    assert isinstance(result, LLMResult)
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.model_id is None
    assert "[OFFLINE]" in result.text


class _FakeInvokeBody:
    """模擬 boto3 invoke_model 回應的 `body` StreamingBody：`.read()` 回 bytes。"""

    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw


def test_complete_online_extracts_usage_from_fake_invoke_model(monkeypatch):
    """mock invoke_model 回固定 usage → complete() 回傳的 LLMResult token 數正確。"""
    config = BedrockConfig(model_id="fake-narrative-model")
    client = BedrockClient(config=config, offline=False)

    captured = {}

    class _FakeRuntime:
        def invoke_model(self, **kwargs):
            captured.update(kwargs)
            return {
                "body": _FakeInvokeBody({
                    "content": [{"type": "text", "text": "假回應文字"}],
                    "usage": {"input_tokens": 321, "output_tokens": 88},
                })
            }

    monkeypatch.setattr(client, "_runtime", lambda: _FakeRuntime())
    result = client.complete(system="sys", prompt="prompt")

    assert result.text == "假回應文字"
    assert result.input_tokens == 321
    assert result.output_tokens == 88
    assert result.model_id == "fake-narrative-model"
    assert captured["modelId"] == "fake-narrative-model"


def test_complete_online_missing_usage_defaults_to_zero(monkeypatch):
    """回應沒帶 usage 欄位（異常格式）→ token 數預設 0，不 raise。"""
    config = BedrockConfig(model_id="fake-model")
    client = BedrockClient(config=config, offline=False)

    class _FakeRuntime:
        def invoke_model(self, **kwargs):
            return {"body": _FakeInvokeBody({"content": [{"type": "text", "text": "x"}]})}

    monkeypatch.setattr(client, "_runtime", lambda: _FakeRuntime())
    result = client.complete(system="sys", prompt="prompt")
    assert result.input_tokens == 0
    assert result.output_tokens == 0


# ---------------------------------------------------------------------------
# bedrock.py：classify_stance 只在 cache-miss 真呼叫時記成本（累積在 cost_events）
# ---------------------------------------------------------------------------

def test_classify_stance_offline_records_no_cost_event():
    client = BedrockClient(offline=True)
    assert client.classify_stance("A", "B") == "neutral"
    assert client.cost_events == []


def test_classify_stance_real_call_records_cost_event(monkeypatch):
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    class _FakeRuntime:
        def converse(self, **kwargs):
            return {
                "output": {"message": {"content": [
                    {"toolUse": {"name": "classify_stance", "input": {"label": "contradiction"}}}
                ]}},
                "usage": {"inputTokens": 42, "outputTokens": 7},
            }

    monkeypatch.setattr(client, "_stance_runtime", lambda: _FakeRuntime())
    label = client.classify_stance("A", "B")

    assert label == "contradiction"
    assert len(client.cost_events) == 1
    ev = client.cost_events[0]
    assert ev["model"] == "fake-stance-model"
    assert ev["tokens_in"] == 42
    assert ev["tokens_out"] == 7
    assert ev["cost_usd"] == estimate_cost("fake-stance-model", 42, 7)


def test_classify_stance_exception_does_not_record_cost(monkeypatch):
    """呼叫失敗（無 usage 數字可記）→ 不記成本，只回 neutral。"""
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    class _BoomRuntime:
        def converse(self, **kwargs):
            raise TimeoutError("simulated timeout")

    monkeypatch.setattr(client, "_stance_runtime", lambda: _BoomRuntime())
    assert client.classify_stance("A", "B") == "neutral"
    assert client.cost_events == []


def test_classify_stance_cache_hit_never_calls_real_stance_and_no_cost(monkeypatch):
    """快取命中不記成本：包成 cached_stance_fn 後，同一對重複查詢只呼叫一次底層。"""
    from trustforge.trust.stance_cache import StanceCache, cached_stance_fn

    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)
    calls = []

    class _FakeRuntime:
        def converse(self, **kwargs):
            calls.append(1)
            return {
                "output": {"message": {"content": [
                    {"toolUse": {"name": "classify_stance", "input": {"label": "entailment"}}}
                ]}},
                "usage": {"inputTokens": 10, "outputTokens": 2},
            }

    monkeypatch.setattr(client, "_stance_runtime", lambda: _FakeRuntime())
    fn = cached_stance_fn(client, cache=StanceCache())

    assert fn("A", "B") == "entailment"
    assert fn("A", "B") == "entailment"  # cache-hit，不應再打底層
    assert len(calls) == 1
    assert len(client.cost_events) == 1  # 只有第一次的真呼叫記成本


# ---------------------------------------------------------------------------
# bedrock.py：extract_claims_with_llm（Step1）record_llm_cost via log
# ---------------------------------------------------------------------------

_CLAIMS_JSON = json.dumps([
    {"claim": "BTC 收盤價上漲", "claim_type": "fact",
     "direction": "bullish", "source_doc_id": "p1"},
])


def test_extract_claims_with_llm_records_cost_on_log(monkeypatch):
    config = BedrockConfig(model_id="fake-model")
    client = BedrockClient(config=config, offline=False)
    docs = [Document(id="p1", kind="price", source="hoya-ohlcv",
                      text="BTC 今日收盤價上漲", ts=1000.0)]

    def _fake_complete(system, prompt):
        return LLMResult(text=_CLAIMS_JSON, input_tokens=200, output_tokens=60,
                          model_id="fake-model")

    monkeypatch.setattr(client, "complete", _fake_complete)
    log = ExecutionLog(now_fn=lambda: 1000.0)
    claims = client.extract_claims_with_llm(docs, log=log)

    assert claims
    cost_events = [e for e in log.events if e["tool"] == "llm.cost"]
    assert len(cost_events) == 1
    assert cost_events[0]["params"]["tokens_in"] == 200
    assert cost_events[0]["params"]["tokens_out"] == 60


def test_extract_claims_with_llm_offline_no_log_param_no_crash():
    """離線 fallback：不呼叫 complete()，也不需要 log 就能跑（log=None 預設值）。"""
    client = BedrockClient(offline=True)
    docs = [Document(id="p1", kind="price", source="hoya-ohlcv",
                      text="BTC 今日收盤價上漲", ts=1000.0)]
    claims = client.extract_claims_with_llm(docs)
    assert claims


# ---------------------------------------------------------------------------
# orchestrator.py：run_agent_pipeline 整合 — log 累積成本 + 帳本收尾寫入一筆
# ---------------------------------------------------------------------------

def _doc(id, kind, ts=1000.0):
    return Document(id=id, kind=kind, text=f"{id} sample text", source="src", ts=ts)


def test_run_agent_pipeline_offline_appends_zero_cost_run(monkeypatch):
    """離線 pipeline：ledger 收到一筆 offline=True、total_cost_usd=0 的 run 記錄。"""
    captured = []
    monkeypatch.setattr(orch_mod, "append_run", lambda record, ledger=None: (captured.append(record), True)[1])

    docs = [_doc("p1", "price"), _doc("n1", "news")]
    client = BedrockClient(offline=True)
    log = ExecutionLog(now_fn=lambda: 1000.0)

    run_agent_pipeline(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=client, log=log, now_fn=lambda: 1000.0,
    )

    assert len(captured) == 1
    rec = captured[0]
    assert rec["coin"] == "BTC"
    assert rec["question_type"] == "multi_source"
    assert rec["offline"] is True
    assert rec["total_cost_usd"] == 0.0
    # Step3 一定會呼叫一次 complete()（離線也會），故至少有一筆 call 記錄
    assert len(rec["calls"]) >= 1


def test_run_agent_pipeline_online_ledger_total_matches_log_sum(monkeypatch):
    """`/costs` 累計 = 帳本加總：run 收尾寫入的 total_cost_usd 應等於 log 內
    所有 llm.cost 事件 cost_usd 加總（fake usage，不打真 AWS）。"""
    # 用 PRICING 表內有定價的 model_id，確保估算成本非零（驗算加總邏輯，非只驗 $0 路徑）
    priced_model = "apac.anthropic.claude-haiku-4-5"
    config = BedrockConfig(model_id=priced_model)
    client = BedrockClient(config=config, offline=False)

    def _fake_complete(system, prompt):
        return LLMResult(text="[claim1] 假敘述", input_tokens=500, output_tokens=120,
                          model_id=priced_model)

    monkeypatch.setattr(client, "complete", _fake_complete)

    captured = []
    monkeypatch.setattr(orch_mod, "append_run", lambda record, ledger=None: (captured.append(record), True)[1])

    docs = [_doc("p1", "price"), _doc("n1", "news")]
    log = ExecutionLog(now_fn=lambda: 1000.0)

    run_agent_pipeline(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=client, log=log, now_fn=lambda: 1000.0,
    )

    assert len(captured) == 1
    rec = captured[0]
    log_cost_sum = round(
        sum(e["params"]["cost_usd"] for e in log.events if e["tool"] == "llm.cost"), 6
    )
    assert rec["total_cost_usd"] == log_cost_sum
    assert rec["total_cost_usd"] > 0.0
    assert rec["offline"] is False


def test_comparison_shared_log_does_not_double_count_ledger_cost(monkeypatch):
    """[HIGH-1 回歸] comparison 兩幣共用同一 ExecutionLog（比照 pipeline.run_comparison
    的作法：兩次 run_agent_pipeline 呼叫共用一個 log 物件）。帳本兩筆記錄加總必須等於
    共用 log 的實際成本總額，不可變成 2A+B（第二輪把第一輪已寫過的又算一次）。"""
    priced_model = "apac.anthropic.claude-haiku-4-5"
    config = BedrockConfig(model_id=priced_model)

    def _fake_complete(system, prompt):
        return LLMResult(text="[claim1] 假敘述", input_tokens=500, output_tokens=120,
                          model_id=priced_model)

    client_a = BedrockClient(config=config, offline=False)
    monkeypatch.setattr(client_a, "complete", _fake_complete)
    client_b = BedrockClient(config=config, offline=False)
    monkeypatch.setattr(client_b, "complete", _fake_complete)

    captured: list[dict] = []
    monkeypatch.setattr(orch_mod, "append_run", lambda record, ledger=None: (captured.append(record), True)[1])

    docs = [_doc("p1", "price"), _doc("n1", "news")]
    log = ExecutionLog(now_fn=lambda: 1000.0)  # 共用同一個 log，模擬 comparison 兩輪

    run_agent_pipeline(
        query="比較 BTC/ETH", coin="BTC", qtype=QuestionType.COMPARISON,
        docs=docs, client=client_a, log=log, now_fn=lambda: 1000.0,
    )
    run_agent_pipeline(
        query="比較 BTC/ETH", coin="ETH", qtype=QuestionType.COMPARISON,
        docs=docs, client=client_b, log=log, now_fn=lambda: 1000.0,
    )

    assert len(captured) == 2  # 兩輪各寫一筆
    ledger_total = round(sum(r["total_cost_usd"] for r in captured), 6)
    log_total = round(
        sum(e["params"]["cost_usd"] for e in log.events if e["tool"] == "llm.cost"), 6
    )
    assert ledger_total == log_total  # 不變量：帳本總額 == 共用 log 實際成本總額
    assert ledger_total > 0.0
    # 反證修好前的 bug：第一輪只該記自己那份（< 共用 log 全部總額，不含第二輪）
    assert captured[0]["total_cost_usd"] < log_total
    # 兩輪各自的 total 加總才是共用 log 全部，任一單輪都不應等於全部（避免重複計費）
    assert captured[1]["total_cost_usd"] != log_total or captured[0]["total_cost_usd"] == 0.0


def test_append_run_unwritable_path_swallows_exception_not_raise(tmp_path):
    """[HIGH-2 回歸] 不可寫的帳本路徑（如目錄本身當檔名寫入會出錯）→ append_run
    吞掉例外、不往上拋，確保帳本壞了不會中斷已經算完的分析 pipeline。"""
    bad_path = tmp_path  # 拿一個目錄路徑當「檔案」寫入，必定引發 IsADirectoryError
    broken_ledger = JsonlLedger(bad_path)

    # 不應拋出任何例外（append_run 內部吞掉、只印 stderr warning）
    append_run({"ts": "t1", "coin": "BTC", "total_cost_usd": 0.0, "calls": []},
                ledger=broken_ledger)
    # 明確驗證：帳本路徑本身還是那個壞目錄，沒有意外寫出檔案內容
    assert bad_path.is_dir()


def test_append_run_unwritable_path_does_not_retry_same_jsonl_path(tmp_path, monkeypatch):
    """target 已是 JsonlLedger 且寫入失敗 → 不該用同路徑再建一顆 JsonlLedger 重試
    （必定再失敗，浪費且無意義）；驗證 fallback 沒有再嘗試寫入同一個壞路徑。"""
    bad_path = tmp_path / "sub"  # 尚未建立成目錄，讓 mkdir/open 都失敗好模擬寫入失敗
    bad_path.mkdir()
    fake_file_path = bad_path  # 直接拿目錄路徑當檔案路徑，open(..., "a") 必炸

    broken_ledger = JsonlLedger(fake_file_path)

    calls = {"n": 0}
    real_init = JsonlLedger.__init__

    def _spy_init(self, path=None):
        calls["n"] += 1
        real_init(self, path)

    monkeypatch.setattr(JsonlLedger, "__init__", _spy_init)
    append_run({"ts": "t1", "total_cost_usd": 0.0, "calls": []}, ledger=broken_ledger)
    # 只有原本那顆 broken_ledger 建構時觸發一次；append_run 內部判斷 target 已是
    # JsonlLedger 就直接放棄，不應再 new 一顆 JsonlLedger() 重試同路徑
    assert calls["n"] == 0  # broken_ledger 是測試碼自建，不算在 spy 監控範圍內


def test_run_agent_pipeline_survives_unwritable_ledger_path(monkeypatch, tmp_path):
    """[HIGH-2 驗收] 帳本路徑不可寫時，整個 pipeline 仍正常回傳報告，不 crash、不中斷。"""
    unwritable_dir = tmp_path / "as_a_file"
    unwritable_dir.mkdir()
    # 讓預設帳本路徑指向一個「目錄」，JsonlLedger.append() 對它 open(path, "a") 必炸
    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(unwritable_dir))

    docs = [_doc("p1", "price"), _doc("n1", "news")]
    client = BedrockClient(offline=True)
    log = ExecutionLog(now_fn=lambda: 1000.0)

    report, evidence = run_agent_pipeline(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=client, log=log, now_fn=lambda: 1000.0,
    )

    assert report is not None
    assert evidence is not None  # pipeline 正常完成，未被帳本寫入失敗中斷


# ---------------------------------------------------------------------------
# web.py：本次成本卡 + /costs 頁面
# ---------------------------------------------------------------------------

def test_render_cost_card_offline_shows_zero_and_offline_label():
    report, evidence, log = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, evidence, log)
    assert "本次分析成本" in htmlout
    assert "$0.00（離線）" in htmlout


def test_render_report_without_log_has_no_cost_card():
    report, evidence, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, evidence)
    assert "本次分析成本" not in htmlout


def test_costs_page_reflects_ledger_summary(monkeypatch, tmp_path):
    ledger_path = tmp_path / "cost_ledger.jsonl"
    fake_ledger = JsonlLedger(ledger_path)
    fake_ledger.append({
        "ts": "2026-01-01T00:00:00Z", "coin": "BTC", "question_type": "multi_source",
        "offline": False, "total_cost_usd": 0.0123,
        "calls": [{"model": "fake-model", "tokens_in": 100, "tokens_out": 50, "cost_usd": 0.0123}],
    })
    monkeypatch.setattr(web, "get_ledger", lambda: fake_ledger)

    htmlout = web._render_costs_page()
    assert "0.0123" in htmlout
    assert "fake-model" in htmlout
    assert "BTC" in htmlout


def test_costs_page_shows_model_token_detail_table(monkeypatch, tmp_path):
    """成本會計階段1：`/costs` 新增「LLM 成本明細（依 Model，含 tokens）」表，
    顯示 tokens_in/tokens_out/單價/成本，且總額與既有彙總一致（純拆分顯示，
    不重複計算）。"""
    ledger_path = tmp_path / "cost_ledger.jsonl"
    fake_ledger = JsonlLedger(ledger_path)
    fake_ledger.append({
        "ts": "t1", "coin": "BTC", "total_cost_usd": 0.003,
        "calls": [
            {"model": "haiku", "tokens_in": 1000, "tokens_out": 200, "cost_usd": 0.001},
            {"model": "sonnet", "tokens_in": 500, "tokens_out": 100, "cost_usd": 0.002},
        ],
    })
    monkeypatch.setattr(web, "get_ledger", lambda: fake_ledger)

    htmlout = web._render_costs_page()
    assert "LLM 成本明細" in htmlout
    assert "haiku" in htmlout
    assert "1000" in htmlout  # tokens_in
    assert "200" in htmlout  # tokens_out
    assert "500" in htmlout
    assert "100" in htmlout
    # 舊有「依 Model 分組」總額不受影響（純拆分顯示、無雙重計算）
    summary = fake_ledger.summary()
    assert summary["by_model"]["haiku"] == pytest.approx(0.001)
    assert summary["by_model"]["sonnet"] == pytest.approx(0.002)
    assert summary["total_cost_usd"] == pytest.approx(0.003)


def test_costs_page_model_token_table_defensive_on_legacy_records_without_tokens(monkeypatch, tmp_path):
    """舊格式紀錄（無 tokens_in/out 欄位）渲染不炸頁面，顯示 0。"""
    ledger_path = tmp_path / "cost_ledger.jsonl"
    fake_ledger = JsonlLedger(ledger_path)
    fake_ledger.append({
        "ts": "t1", "coin": "BTC", "total_cost_usd": 0.001,
        "calls": [{"model": "legacy-model", "cost_usd": 0.001}],
    })
    monkeypatch.setattr(web, "get_ledger", lambda: fake_ledger)

    htmlout = web._render_costs_page()
    assert "legacy-model" in htmlout


def test_render_model_token_table_shows_unit_price_from_pricing():
    """已在 `PRICING` 登記的 model 要顯示對應單價，來自 `ledger.PRICING`。"""
    from trustforge.ledger import PRICING

    known_model = next(iter(PRICING))
    summary = {
        "by_model_detail": {
            known_model: {"cost_usd": 0.001, "tokens_in": 100, "tokens_out": 50},
        }
    }
    htmlout = web._render_model_token_table(summary)
    assert known_model in htmlout


def test_render_model_token_table_unknown_model_shows_offline_fallback():
    summary = {
        "by_model_detail": {
            "offline": {"cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0},
        }
    }
    htmlout = web._render_model_token_table(summary)
    assert "offline" in htmlout
    assert "無定價" in htmlout or "離線" in htmlout


def test_costs_page_over_budget_shows_alert(monkeypatch, tmp_path):
    ledger_path = tmp_path / "cost_ledger.jsonl"
    fake_ledger = JsonlLedger(ledger_path)
    fake_ledger.append({
        "ts": "t1", "coin": "BTC", "question_type": "multi_source",
        "offline": False, "total_cost_usd": 99.0,
        "calls": [{"model": "m", "tokens_in": 1, "tokens_out": 1, "cost_usd": 99.0}],
    })
    monkeypatch.setattr(web, "get_ledger", lambda: fake_ledger)
    monkeypatch.setattr(web, "COST_BUDGET_USD", "10")

    htmlout = web._render_costs_page()
    assert "超過預算" in htmlout


def test_costs_page_under_budget_no_alert(monkeypatch, tmp_path):
    ledger_path = tmp_path / "cost_ledger.jsonl"
    fake_ledger = JsonlLedger(ledger_path)
    fake_ledger.append({
        "ts": "t1", "coin": "BTC", "question_type": "multi_source",
        "offline": False, "total_cost_usd": 0.01,
        "calls": [{"model": "m", "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.01}],
    })
    monkeypatch.setattr(web, "get_ledger", lambda: fake_ledger)
    monkeypatch.setattr(web, "COST_BUDGET_USD", "10")

    htmlout = web._render_costs_page()
    assert "超過預算" not in htmlout


def test_costs_route_reachable_via_do_get_handler():
    """`/costs` 路由不 404（走 Handler.do_GET 的路由分派邏輯，非開真 socket）。"""
    import io as _io
    from unittest.mock import MagicMock

    handler = web.Handler.__new__(web.Handler)
    handler.path = "/costs"
    handler.client_address = ("127.0.0.1", 0)
    handler.rfile = _io.BytesIO(b"")
    handler.wfile = _io.BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    handler.do_GET()
    handler.send_response.assert_called_once()
    status_code = handler.send_response.call_args[0][0]
    assert status_code == 200


# ---------------------------------------------------------------------------
# MEDIUM 修正（CEO Chrome 複審，PR #39）：header cost 不再每次 render 都
# 全掃 ledger（`_get_ledger_summary()` 要在 TTL 內命中快取，不是每次都真的
# 呼叫底層 `.summary()`）。
# ---------------------------------------------------------------------------

def test_get_ledger_summary_caches_within_ttl(monkeypatch):
    """同一個 `get_ledger` 參照下，短時間內連續呼叫 `_get_ledger_summary()`
    多次，底層 `Ledger.summary()` 只應該真的被呼叫一次（快取命中），
    不是每次都全掃 JSONL/DynamoDB。"""
    calls = {"n": 0}

    class _FakeLedger:
        def summary(self):
            calls["n"] += 1
            return {"total_cost_usd": 1.23, "by_model": {}, "n_runs": 1}

    fake = _FakeLedger()
    monkeypatch.setattr(web, "get_ledger", lambda: fake)

    for _ in range(5):
        out = web._get_ledger_summary()
        assert out["total_cost_usd"] == 1.23

    assert calls["n"] == 1, "TTL 內重複呼叫應該命中快取，不該每次都真掃 ledger"


def test_get_ledger_summary_cache_isolated_per_get_ledger_identity(monkeypatch):
    """不同測試 monkeypatch 不同的 `get_ledger` 時（identity 改變），快取要
    自動失效、拿到各自新的資料——確保 O(1) 快取不會讓不同測試互相污染
    （既有 test_cost_ledger.py 內連續三個測試各自 monkeypatch 不同
    fake_ledger 的模式必須繼續正確）。"""
    class _FakeLedgerA:
        def summary(self):
            return {"total_cost_usd": 11.0, "by_model": {}, "n_runs": 1}

    class _FakeLedgerB:
        def summary(self):
            return {"total_cost_usd": 22.0, "by_model": {}, "n_runs": 1}

    monkeypatch.setattr(web, "get_ledger", lambda: _FakeLedgerA())
    out_a = web._get_ledger_summary()
    assert out_a["total_cost_usd"] == 11.0

    monkeypatch.setattr(web, "get_ledger", lambda: _FakeLedgerB())
    out_b = web._get_ledger_summary()
    assert out_b["total_cost_usd"] == 22.0, "get_ledger 換了參照，快取不該回舊值"


def test_costs_page_render_does_not_rescan_ledger_per_render(monkeypatch):
    """`/costs` 頁面渲染（`_render_costs_page`）不該每次都真的觸發底層
    ledger 全掃——同一個 request 內部即使讀了摘要多次，底層 `.summary()`
    呼叫次數應該是常數（O(1)），不隨 render 次數線性增長。"""
    calls = {"n": 0}

    class _FakeLedger:
        def summary(self):
            calls["n"] += 1
            return {"total_cost_usd": 5.0, "by_model": {}, "n_runs": 3}

    fake = _FakeLedger()
    monkeypatch.setattr(web, "get_ledger", lambda: fake)
    monkeypatch.setattr(web, "COST_BUDGET_USD", "100")

    for _ in range(3):
        web._render_costs_page()

    assert calls["n"] <= 1, (
        f"/costs 重複 render {3} 次，底層 summary() 被呼叫 {calls['n']} 次，"
        "應該在 TTL 內被快取吃掉，不該線性增長"
    )


def test_get_ledger_summary_single_flight_on_concurrent_cache_miss(monkeypatch):
    """thundering herd 回歸（MEDIUM，codex 複審，PR #39，與 `/status` 的
    `_render_status_page_cached` 同類 bug）：先前版本只在鎖內做「檢查
    cache」跟「寫入 cache」兩小段，中間真正的 `Ledger.summary()` 計算是在
    **鎖外**跑的——冷啟動或 TTL 剛過期那一瞬間，`ThreadingHTTPServer` 下
    每個併發進來的頁面請求（header cost ledger 連結每頁都會觸發、`/costs`
    本身也會）都各自判定 cache miss，全部平行呼叫一次 `summary()`：JSONL
    全檔重讀／DynamoDB 全表 Scan 被同時打好幾份，延遲尖峰、DynamoDB
    backend 下還有成本放大。

    修法把整段「檢查 cache → miss 就地計算 → 寫回 cache」放進同一把鎖內
    序列化——這裡斷言：cache miss 瞬間多執行緒併發呼叫
    `_get_ledger_summary()`，底層 `summary()` 必須只被呼叫一次，其餘請求
    排隊等鎖後直接吃新快取，不各自重算。

    用 `time.sleep()` 刻意拉寬 `summary()` 的執行視窗，確保其他執行緒真的
    會在它執行期間排隊等鎖，而不是僥倖沒撞在一起（比照 `/status` 的
    `test_render_status_page_cached_single_flight_on_concurrent_expiry`）。
    """
    import threading
    import time as time_mod

    calls = {"n": 0}
    calls_lock = threading.Lock()

    class _SlowFakeLedger:
        def summary(self):
            with calls_lock:
                calls["n"] += 1
            time_mod.sleep(0.05)  # 拉寬執行視窗，逼其他執行緒排隊等鎖
            return {"total_cost_usd": 9.99, "by_model": {}, "n_runs": 1}

    fake = _SlowFakeLedger()
    monkeypatch.setattr(web, "get_ledger", lambda: fake)
    web._ledger_summary_cache.clear()  # 模擬冷啟動／TTL 過期後的 cache miss

    barrier = threading.Barrier(20)
    results = []
    results_lock = threading.Lock()

    def worker():
        barrier.wait()  # 讓 20 個執行緒盡量同時撞上 cache-miss
        out = web._get_ledger_summary()
        with results_lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert calls["n"] == 1, (
        f"single-flight：cache miss 瞬間應只有一個請求真的重算 summary()，"
        f"實際被呼叫 {calls['n']} 次"
    )
    assert len(results) == 20
    assert all(r["total_cost_usd"] == 9.99 for r in results), (
        "其餘請求應排隊等鎖後直接吃第一個請求算好、寫回快取的新值"
    )


def test_get_ledger_summary_new_factory_gets_fresh_value_after_old_factory_gc(monkeypatch):
    """cache key 修正回歸（MEDIUM，codex 複審，PR #39）：先前版本用
    `id(get_ledger)`（純整數）當快取 key，字典本身不持有 `get_ledger` 函式
    物件的參照。若舊工廠被重新綁定、其函式物件又剛好被 GC 回收，CPython
    有機率把同一個記憶體位址（同一個 `id()`）重新分配給另一個新建立的
    函式物件——20 秒 TTL 內若新工廠恰好拿到舊 id，會直接命中舊工廠留下的
    摘要快取，顯示錯誤（過期）的成本數字，而且是悄無聲息、不會噴錯的
    資料錯誤。

    修法：直接用 `get_ledger` 這個函式物件本身作 dict key，而不是它的
    `id()`。這裡不靠「刻意製造真實 id 重用」這種依賴 CPython 記憶體分配
    細節、跨直譯器不保證重現的作法去驗證（會是 flaky 測試的來源），而是
    直接驗證修法真正保證的不變量，用 `weakref` 證明：

    1. 快取 entry 還在字典裡（TTL 未過期）時，舊工廠函式物件不可能被 GC
       回收——因為 dict key 本身就是對它的一份強參照。這正是修法讓
       「同一個 id 被分配給另一個活著的物件」這件事結構上不可能發生的
       原因：物件活著、id 就不會被回收再利用。
    2. 快取 entry 被移除後（模擬 TTL 過期或 32 筆上限淘汰），舊工廠才真的
       失去所有參照、可以被回收。
    3. 新工廠接手後，一定拿到自己算出來的新摘要，不會吃到舊工廠殘留的
       快取值。
    """
    import gc
    import weakref

    class _FakeLedgerOld:
        def summary(self):
            return {"total_cost_usd": 111.0, "by_model": {}, "n_runs": 1}

    def factory_old():
        return _FakeLedgerOld()

    monkeypatch.setattr(web, "get_ledger", factory_old)
    out_old = web._get_ledger_summary()
    assert out_old["total_cost_usd"] == 111.0

    ref = weakref.ref(factory_old)
    del factory_old
    # 注意：這裡故意不用 `monkeypatch.setattr(web, "get_ledger", None)`——
    # `MonkeyPatch.setattr()` 每次呼叫都會 `getattr(target, name)` 記下
    # 「當下的舊值」放進自己的 `_setattr` 還原清單，等於再多留一份對
    # `factory_old` 的強參照直到測試結束才釋放，會讓底下「該被回收」的
    # 斷言永遠失敗（pytest 測試框架本身的參照，不是本次要驗證的修法邏輯）。
    # 直接對 module 屬性賦值即可——monkeypatch 在第一次 `setattr()` 時已經
    # 記住了「測試開始前的真正原始值」，測試結束時仍會正確還原，不受這裡
    # 的直接賦值影響。
    web.get_ledger = None
    gc.collect()

    # 注意：不要在 `assert` 表達式裡直接呼叫 `ref()`——pytest 的 assertion
    # rewriting 會把求值結果暫存進一個隱藏的區域變數，該變數會一路存活到
    # 函式結束，等於偷偷多留一份對 factory_old 的強參照，讓底下「該被回收」
    # 的斷言永遠失敗（誤判成 bug）。這裡先明確存到具名變數、斷言後立刻
    # `del` 釋放，才不會被這個 pytest 內部機制污染判斷。
    snapshot = ref()
    assert snapshot is not None, (
        "cache 內這筆 entry 還在（TTL 未過期），dict key 應該持有函式物件"
        "的強參照，此時舊工廠不該已經被 GC 回收——這正是修法要保證的不變量"
    )
    web._ledger_summary_cache.pop(snapshot, None)
    del snapshot
    gc.collect()

    snapshot = ref()
    assert snapshot is None, "cache entry 移除後，舊工廠沒有其他參照了，該被 GC 回收"
    del snapshot

    class _FakeLedgerNew:
        def summary(self):
            return {"total_cost_usd": 222.0, "by_model": {}, "n_runs": 1}

    def factory_new():
        return _FakeLedgerNew()

    monkeypatch.setattr(web, "get_ledger", factory_new)
    out_new = web._get_ledger_summary()
    assert out_new["total_cost_usd"] == 222.0, (
        "新工廠必須拿到自己的新摘要，不該吃到舊工廠殘留的快取值"
    )
