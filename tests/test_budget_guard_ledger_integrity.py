"""#9 codex HIGH 第 6 輪追加 — 記帳完整性：append 失敗吞掉→花費沒記→cap 失效。

背景（見 `budget_guard.py` 對應 docstring）：`ledger.append_run()` 一直以來
的設計是「帳本是 pipeline 的旁路，寫失敗不能讓已算完的分析中斷」，因此
primary（DynamoDB）+ fallback（JsonlLedger）都失敗時只印 stderr warning、
吞掉例外，`daily_cost_usd()` 的 authority 完全建立在這份 best-effort 帳本
上。若 storage 持續性不可用：付費請求完成、原子預留照常釋放，但花費
**沒有**持久化進帳本——之後 `daily_cost_usd()` 讀不到這筆，guard 誤判
「今日還有預算」，讓重複請求無限繞過 $3/day cap（真實花費照樣持續發生，
只是全部沒進帳本，cap 名存實亡）。

修法：`append_run()` 改回傳 `bool`（是否真的持久化成功）；持久化失敗時，
`orchestrator.run_agent_pipeline()` 把這筆真的花掉的成本記到
`budget_guard.record_unledgered_spend()`（process-local fail-closed
計數器）；`daily_cost_usd()` 把這個計數器也算進「今日已花費」，讓沒記
成功的花費仍計入 cap，不會被重複請求繞過。

⛔ 全程不打真 AWS/Bedrock：全部用 fake `client.complete()`/monkeypatch
`ledger.append_run()` 模擬持久化失敗，無網路呼叫。
"""
from __future__ import annotations

import pytest

import trustforge.agent.orchestrator as orch_mod
import trustforge.budget_guard as bg
from trustforge.agent.orchestrator import run_agent_pipeline
from trustforge.bedrock import BedrockClient, BedrockConfig, LLMResult
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.ledger import JsonlLedger, append_run
from trustforge.schema import QuestionType

_PRICED_MODEL = "apac.anthropic.claude-haiku-4-5"


def _doc(id: str, kind: str, ts: float = 1000.0) -> Document:
    return Document(id=id, kind=kind, text=f"{id} sample text", source="src", ts=ts)


def _priced_client() -> BedrockClient:
    config = BedrockConfig(model_id=_PRICED_MODEL)
    client = BedrockClient(config=config, offline=False)

    def _fake_complete(system, prompt):
        return LLMResult(
            text="[claim1] 假敘述", input_tokens=500, output_tokens=120, model_id=_PRICED_MODEL,
        )

    client.complete = _fake_complete  # type: ignore[method-assign]
    return client


# ---------------------------------------------------------------------------
# 1) 單元層：append_run() 回傳值 + record_unledgered_spend / daily_cost_usd
# ---------------------------------------------------------------------------


def test_append_run_returns_true_on_successful_primary_write(tmp_path):
    ledger = JsonlLedger(tmp_path / "ok.jsonl")
    ok = append_run({"ts": "t1", "coin": "BTC", "total_cost_usd": 0.001, "calls": []}, ledger=ledger)
    assert ok is True


def test_append_run_returns_false_when_primary_and_fallback_both_fail(tmp_path):
    """target 本身已是 JsonlLedger 且路徑不可寫（拿目錄當檔案）→ primary 失敗，
    且判定「同路徑 fallback 必再失敗」直接放棄，回傳 False（真的沒記進帳本）。"""
    bad_path = tmp_path  # 目錄當檔案路徑，open(path, "a") 必炸
    broken_ledger = JsonlLedger(bad_path)
    ok = append_run({"ts": "t1", "coin": "BTC", "total_cost_usd": 0.5, "calls": []}, ledger=broken_ledger)
    assert ok is False


def test_append_run_returns_true_when_fallback_succeeds(monkeypatch, tmp_path):
    """primary（DynamoDB）失敗，但 fallback JsonlLedger 寫入成功 → 仍算持久化成功。"""
    from unittest.mock import MagicMock

    from trustforge.ledger import DynamoDBLedger

    fallback_path = tmp_path / "fallback.jsonl"
    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(fallback_path))

    broken = DynamoDBLedger()
    monkeypatch.setattr(
        broken, "_get_table", MagicMock(side_effect=RuntimeError("no aws credentials")),
    )
    ok = append_run({"ts": "t1", "coin": "BTC", "total_cost_usd": 0.2, "calls": []}, ledger=broken)
    assert ok is True
    assert fallback_path.exists()


def test_record_unledgered_spend_counted_in_daily_cost_usd(monkeypatch, tmp_path):
    """帳本本身空的（沒有任何 run 紀錄），但 process-local 未記帳花費計數器
    有值 → daily_cost_usd() 仍要把它算進去（fail-closed，不能因為帳本讀不到
    就當作沒發生）。"""
    empty_ledger = JsonlLedger(tmp_path / "empty.jsonl")
    assert bg.daily_cost_usd(empty_ledger, now_fn=lambda: 1_800_000_000.0) == 0.0

    bg.record_unledgered_spend(1.23)
    assert bg.daily_cost_usd(empty_ledger, now_fn=lambda: 1_800_000_000.0) == pytest.approx(1.23)


def test_record_unledgered_spend_accumulates_across_multiple_calls():
    bg.record_unledgered_spend(0.5)
    bg.record_unledgered_spend(0.3)
    assert bg._UNLEDGERED_SPEND.total() == pytest.approx(0.8)


def test_record_unledgered_spend_ignores_non_positive_and_non_finite():
    bg.record_unledgered_spend(0.0)
    bg.record_unledgered_spend(-1.0)
    bg.record_unledgered_spend(float("nan"))
    bg.record_unledgered_spend(float("inf"))
    assert bg._UNLEDGERED_SPEND.total() == 0.0


def test_reset_unledgered_spend_for_tests_zeroes_counter():
    bg.record_unledgered_spend(2.0)
    assert bg._UNLEDGERED_SPEND.total() > 0.0
    bg._reset_unledgered_spend_for_tests()
    assert bg._UNLEDGERED_SPEND.total() == 0.0


# ---------------------------------------------------------------------------
# 2) 整合層：run_agent_pipeline() 持久化失敗 → 記到 process-local 計數器
# ---------------------------------------------------------------------------


def test_run_agent_pipeline_persist_failure_records_unledgered_spend(monkeypatch):
    """append_run() 回傳 False（模擬 primary+fallback 都失敗）→ 這次真實花費
    要被記到 budget_guard._UNLEDGERED_SPEND，而不是憑空消失。"""
    monkeypatch.setattr(orch_mod, "append_run", lambda record, ledger=None: False)

    client = _priced_client()
    docs = [_doc("p1", "price"), _doc("n1", "news")]
    log = ExecutionLog(now_fn=lambda: 1000.0)

    assert bg._UNLEDGERED_SPEND.total() == 0.0

    run_agent_pipeline(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=client, log=log, now_fn=lambda: 1000.0,
    )

    log_cost_sum = round(
        sum(e["params"]["cost_usd"] for e in log.events if e["tool"] == "llm.cost"), 6
    )
    assert log_cost_sum > 0.0
    assert bg._UNLEDGERED_SPEND.total() == pytest.approx(log_cost_sum)


def test_run_agent_pipeline_persist_success_does_not_touch_unledgered_spend(monkeypatch):
    """append_run() 回傳 True（正常持久化成功）→ 不應該誤記到未記帳花費
    計數器（那是專門給「真的沒記進去」的花費用的）。"""
    monkeypatch.setattr(orch_mod, "append_run", lambda record, ledger=None: True)

    client = _priced_client()
    docs = [_doc("p1", "price"), _doc("n1", "news")]
    log = ExecutionLog(now_fn=lambda: 1000.0)

    run_agent_pipeline(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=client, log=log, now_fn=lambda: 1000.0,
    )

    assert bg._UNLEDGERED_SPEND.total() == 0.0


def test_run_agent_pipeline_offline_persist_failure_does_not_pollute_counter(monkeypatch):
    """離線 run（$0 成本）即使 append_run() 失敗，也不該把 $0 記進未記帳
    計數器（沒有意義，且 `record_unledgered_spend` 本身對 <=0 也是 no-op）。
    """
    monkeypatch.setattr(orch_mod, "append_run", lambda record, ledger=None: False)

    client = BedrockClient(offline=True)
    docs = [_doc("p1", "price"), _doc("n1", "news")]
    log = ExecutionLog(now_fn=lambda: 1000.0)

    run_agent_pipeline(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=client, log=log, now_fn=lambda: 1000.0,
    )

    assert bg._UNLEDGERED_SPEND.total() == 0.0


# ---------------------------------------------------------------------------
# 3) 端對端：帳本持續性失敗 + 重複請求 → cap 仍然擋得住（不會無限繞過）
# ---------------------------------------------------------------------------


def test_daily_cap_exceeded_reflects_repeated_persist_failures_even_with_empty_ledger(
    monkeypatch, tmp_path,
):
    """模擬「storage 持續不可用」：帳本本身永遠是空的（每次 `daily_cost_usd()`
    讀到的歷史紀錄都是 0 筆），但連續好幾次真實請求都花了錢、且每次
    `append_run()` 都持久化失敗——`daily_cap_exceeded()` 必須累積算到這些
    未記帳的花費，最終在 cap 前擋下，不能因為帳本一直空空的就一直放行、
    讓重複請求無限繞過每日上限。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "0.01")
    empty_ledger = JsonlLedger(tmp_path / "always_empty.jsonl")

    # 每次都模擬一次「真的花了 $0.005、但沒能持久化進帳本」的請求
    per_call_cost = 0.005
    assert not bg.daily_cap_exceeded(empty_ledger, now_fn=lambda: 1_800_000_000.0)
    bg.record_unledgered_spend(per_call_cost)
    assert not bg.daily_cap_exceeded(empty_ledger, now_fn=lambda: 1_800_000_000.0)
    bg.record_unledgered_spend(per_call_cost)
    # 累積到 0.01（= cap），cap 判斷是 spent >= cap，此時應該已經擋下
    assert bg.daily_cap_exceeded(empty_ledger, now_fn=lambda: 1_800_000_000.0)
    # 帳本本身確實從頭到尾都是空的（驗證這個 fail-closed 的守護不是靠帳本本身）
    assert empty_ledger.read_all() == []


def test_pipeline_run_repeated_persist_failures_eventually_forces_offline(monkeypatch):
    """更貼近真實情境：反覆呼叫 `pipeline.run(llm_mode="bedrock")`，每次
    `append_run()` 都持久化失敗——即使帳本從未累積出任何紀錄，重複請求
    最終仍必須被 cap 擋下、強制離線，不能無限次真的打 Bedrock（這裡用
    fake client 模擬，验证的是「決策旗標」，非真網路呼叫）。"""
    import trustforge.pipeline as pl

    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "0.01")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.005")
    monkeypatch.setattr(pl, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(orch_mod, "append_run", lambda record, ledger=None: False)

    def _fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return [_doc(f"{coin}_p1", "price"), _doc(f"{coin}_n1", "news")]

    monkeypatch.setattr(pl, "collect", _fake_collect)

    call_count = {"n": 0}
    orig_cls = pl.BedrockClient

    def spy_cls(*args, **kwargs):
        call_count["n"] += 1
        client = orig_cls(*args, **kwargs)
        if not kwargs.get("offline", True):
            config = BedrockConfig(model_id=_PRICED_MODEL)
            client.config = config

            def _fake_complete(system, prompt):
                return LLMResult(
                    text="[claim1] 假敘述", input_tokens=500, output_tokens=120,
                    model_id=_PRICED_MODEL,
                )

            client.complete = _fake_complete  # type: ignore[method-assign]
        return client

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    offline_flags = []
    for _ in range(8):
        report, evidence, log = pl.run(
            "BTC", "分析 BTC", QuestionType.MULTI_SOURCE,
            data_mode="live", llm_mode="bedrock",
        )
        # 每次都重新建構 client，讀取這次建構時 pipeline 決定的 offline 旗標
        # 透過 report.limits 判斷這次是否真的被擋下（"已達上限" 訊息出現）。
        offline_flags.append(any("已達上限" in s for s in report.limits))

    # 前幾次成本($0.005/次)累加到 cap($0.01)前應該還放行，之後必須開始被擋
    assert any(offline_flags), "累積花費超過 cap 後應該有請求被擋下（fail-closed）"
    assert offline_flags[-1] is True, "多次重複請求後最終必須被擋下，不能無限繞過 cap"
