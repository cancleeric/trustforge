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
from trustforge.ledger import (
    PRICING,
    DynamoDBLedger,
    Ledger,
    JsonlLedger,
    append_run,
    estimate_cost,
    get_ledger,
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
    d = DynamoDBLedger(table_name="fake-table")
    mock_table = MagicMock()
    d._table = mock_table

    d.append({"coin": "ETH", "total_cost_usd": 0.0, "calls": []})

    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["run_id"]  # 自動生成，非空字串
    assert "ETH" in item["run_id"]
    assert item["ts"]  # 自動補上 ISO8601 時間戳


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
    monkeypatch.setattr(orch_mod, "append_run", lambda record, ledger=None: captured.append(record))

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
    monkeypatch.setattr(orch_mod, "append_run", lambda record, ledger=None: captured.append(record))

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
    monkeypatch.setattr(orch_mod, "append_run", lambda record, ledger=None: captured.append(record))

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
