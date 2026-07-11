"""#9 online-stance 預算配額硬化 — 開 Bedrock 給公開 `/api/analyze` 前的護欄測試。

⛔ 全程不打真 AWS/Bedrock：
- `budget_guard` 模組本身只讀 env + 讀既有隔離帳本（`conftest.py` 的
  `_isolate_cost_ledger` autouse fixture 已把 `TRUSTFORGE_COST_LEDGER_PATH`
  指到 tmp_path），不涉及任何網路呼叫。
- `pipeline.run()` 整合測試一律 monkeypatch `pipeline.collect`（比照
  `test_credit_safe_modes.py` 既有慣例）+ 直接 spy `pipeline.BedrockClient`
  建構參數；測試文件刻意只用「單一 news 來源」（`_make_real_docs()`，跟
  `test_credit_safe_modes.py` 完全同款），確保 Step2/Step2.5 沒有第二個
  同類（news/social）主張可配對，`_detect_stance_pairs` 不會產生任何候選
  配對，`classify_stance` 因此**在資料層面就不可能被觸發**，不需要依賴
  `demo/sample_data/stance_cache.json` 的既有快取內容猜測 cache-hit/miss，
  100% 排除真的打到 `_stance_runtime()`/AWS 的可能性。
"""
from __future__ import annotations

import pytest

import trustforge.budget_guard as bg
import trustforge.pipeline as pl
from trustforge.ingestion.base import Document
from trustforge.ledger import JsonlLedger
from trustforge.schema import QuestionType


def _doc(id: str, kind: str, source: str, text: str = "", ts: float = 1_000.0) -> Document:
    return Document(id=id, kind=kind, source=source, text=text, ts=ts)


def _fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
    """比照 `test_credit_safe_modes.py::fake_collect` 慣例，簽名跟
    `pipeline.run()` 實際呼叫 `collect(query, coin=coin, offline=..., ...)`
    的方式對齊，避免用 `*args/**kwargs` 猜位置參數索引。"""
    return _make_real_docs(coin)


def _make_real_docs(coin: str) -> list[Document]:
    """單一 price ＋單一 news（不同來源），跟 `test_credit_safe_modes.py`
    同款——刻意只有一筆情緒類（news）主張，讓跨源 stance_pairs 偵測沒有
    候選配對可用，`classify_stance` 在資料層面就不會被呼叫。"""
    return [
        _doc(f"{coin}_p1", "price", "real-hoya-ohlcv", f"{coin} 現價 30000，24h +1.2%。"),
        _doc(f"{coin}_n1", "news", "real-coindesk-rss", f"{coin} 市場情緒正向。"),
    ]


# ---------------------------------------------------------------------------
# 1) daily_cap_usd()：env 解析
# ---------------------------------------------------------------------------

def test_daily_cap_usd_default_when_unset(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", raising=False)
    assert bg.daily_cap_usd() == pytest.approx(3.0)
    assert bg.DEFAULT_BEDROCK_DAILY_USD_CAP == pytest.approx(3.0)


def test_daily_cap_usd_env_override(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "30")
    assert bg.daily_cap_usd() == pytest.approx(30.0)


def test_daily_cap_usd_empty_string_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "   ")
    assert bg.daily_cap_usd() == pytest.approx(3.0)


def test_daily_cap_usd_invalid_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "not-a-number")
    assert bg.daily_cap_usd() == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# 2) daily_cost_usd()：跨 run 帳本彙總（按 UTC 日期）
# ---------------------------------------------------------------------------

def test_daily_cost_usd_sums_only_today_records(tmp_path):
    ledger = JsonlLedger(tmp_path / "ledger.jsonl")
    ledger.append({"ts": "2026-07-01T01:00:00+00:00", "total_cost_usd": 1.0})
    ledger.append({"ts": "2026-07-01T02:00:00+00:00", "total_cost_usd": 2.5})
    ledger.append({"ts": "2026-06-30T23:59:00+00:00", "total_cost_usd": 100.0})  # 昨天，不計
    total = bg.daily_cost_usd(ledger, day="2026-07-01")
    assert total == pytest.approx(3.5)


def test_daily_cost_usd_ignores_malformed_records(tmp_path):
    ledger = JsonlLedger(tmp_path / "ledger.jsonl")
    ledger.append({"ts": "2026-07-01T01:00:00+00:00", "total_cost_usd": 1.0})
    ledger.append({"ts": "2026-07-01T02:00:00+00:00", "total_cost_usd": "not-a-number"})
    ledger.append({"ts": "2026-07-01T03:00:00+00:00"})  # 缺 total_cost_usd
    ledger.append({"total_cost_usd": 5.0})  # 缺 ts
    total = bg.daily_cost_usd(ledger, day="2026-07-01")
    assert total == pytest.approx(1.0)


def test_daily_cost_usd_empty_ledger_is_zero(tmp_path):
    ledger = JsonlLedger(tmp_path / "empty.jsonl")
    assert bg.daily_cost_usd(ledger, day="2026-07-01") == 0.0


# ---------------------------------------------------------------------------
# 3) daily_cap_exceeded()
# ---------------------------------------------------------------------------

def test_daily_cap_exceeded_true_when_at_cap(tmp_path):
    ledger = JsonlLedger(tmp_path / "ledger.jsonl")
    ledger.append({"ts": "2026-07-01T00:00:00+00:00", "total_cost_usd": 3.0})
    assert bg.daily_cap_exceeded(ledger, now_fn=lambda: _ts("2026-07-01T12:00:00")) is True


def test_daily_cap_exceeded_true_when_above_cap(tmp_path):
    ledger = JsonlLedger(tmp_path / "ledger.jsonl")
    ledger.append({"ts": "2026-07-01T00:00:00+00:00", "total_cost_usd": 3.5})
    assert bg.daily_cap_exceeded(ledger, now_fn=lambda: _ts("2026-07-01T12:00:00")) is True


def test_daily_cap_exceeded_false_when_clearly_below_cap(tmp_path):
    ledger = JsonlLedger(tmp_path / "ledger.jsonl")
    ledger.append({"ts": "2026-07-01T00:00:00+00:00", "total_cost_usd": 0.5})
    assert bg.daily_cap_exceeded(ledger, now_fn=lambda: _ts("2026-07-01T12:00:00")) is False


def test_daily_cap_exceeded_resets_next_utc_day(tmp_path):
    """昨天燒到上限，今天應該重置（帳本按日期彙總，天然重置）。"""
    ledger = JsonlLedger(tmp_path / "ledger.jsonl")
    ledger.append({"ts": "2026-07-01T23:59:00+00:00", "total_cost_usd": 100.0})
    assert bg.daily_cap_exceeded(ledger, now_fn=lambda: _ts("2026-07-02T00:00:01")) is False


def test_daily_cap_exceeded_zero_cap_always_true_even_with_empty_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "0")
    ledger = JsonlLedger(tmp_path / "empty.jsonl")
    assert bg.daily_cap_exceeded(ledger) is True


def test_daily_cap_exceeded_negative_cap_always_true(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "-1")
    ledger = JsonlLedger(tmp_path / "empty.jsonl")
    assert bg.daily_cap_exceeded(ledger) is True


def test_daily_cap_exceeded_uses_default_ledger_when_none_passed(monkeypatch):
    """未傳 ledger 時走 `get_ledger()`（已被 conftest 的 autouse fixture 隔離到
    tmp_path），空帳本 + 預設 $3 cap → 未達標。"""
    monkeypatch.delenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", raising=False)
    assert bg.daily_cap_exceeded() is False


def _ts(iso: str) -> float:
    from datetime import datetime, timezone
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# 4) online_stance_requested()：兩個 env 缺一不可
# ---------------------------------------------------------------------------

def test_online_stance_requested_false_when_neither_env_set(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_ONLINE_STANCE", raising=False)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    assert bg.online_stance_requested() is False


def test_online_stance_requested_false_when_only_switch_set(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_ONLINE_STANCE", "1")
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    assert bg.online_stance_requested() is False


def test_online_stance_requested_false_when_only_model_id_set(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_ONLINE_STANCE", raising=False)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "some-model")
    assert bg.online_stance_requested() is False


@pytest.mark.parametrize("value", ["1", "true", "True", "YES", "on"])
def test_online_stance_requested_true_when_both_set_truthy(monkeypatch, value):
    monkeypatch.setenv("TRUSTFORGE_ONLINE_STANCE", value)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "some-model")
    assert bg.online_stance_requested() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_online_stance_requested_false_when_switch_falsy(monkeypatch, value):
    monkeypatch.setenv("TRUSTFORGE_ONLINE_STANCE", value)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "some-model")
    assert bg.online_stance_requested() is False


# ---------------------------------------------------------------------------
# 5) pipeline.run() 整合：預設（未設任何新 env）行為逐字不變
# ---------------------------------------------------------------------------

def test_run_default_no_new_env_behaves_byte_identical(monkeypatch):
    """完全不設 `TRUSTFORGE_ONLINE_STANCE`/`TRUSTFORGE_BEDROCK_DAILY_USD_CAP`/
    `BEDROCK_MODEL_ID` 時，`BedrockClient` 建構參數應與加入本護欄前完全一致：
    `offline=llm_offline`、`stance_offline` 未顯式指定為不同值（跟 offline
    相同），且 `report.limits` 不應出現任何新的 degrade 說明。"""
    monkeypatch.delenv("TRUSTFORGE_ONLINE_STANCE", raising=False)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", raising=False)

    monkeypatch.setattr(pl, "collect", _fake_collect)

    captured = []
    orig_cls = pl.BedrockClient

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return orig_cls(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE, data_mode="live", llm_mode="off",
    )
    assert len(captured) == 1
    assert captured[0] == {"offline": True, "stance_offline": True}
    assert not any("線上深度分析" in s for s in report.limits)


# ---------------------------------------------------------------------------
# 6) pipeline.run()：每日 $3 上限達標 → 強制整輪 abstain（narrative + stance）
# ---------------------------------------------------------------------------

def test_run_daily_cap_hit_forces_bedrock_mode_to_full_offline_abstain(monkeypatch):
    """`llm_mode="bedrock"`（原本敘事會是線上）+ 今日已達 cap → 強制
    `offline=True`（敘事也 abstain），並補上「已達上限」degrade 說明。"""
    monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: True)
    monkeypatch.setattr(pl, "collect", _fake_collect)

    captured = []
    orig_cls = pl.BedrockClient

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return orig_cls(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE, data_mode="live", llm_mode="bedrock",
    )
    assert captured[0] == {"offline": True, "stance_offline": True}
    assert any("今日 Bedrock 預算已達上限" in s for s in report.limits)


def test_run_daily_cap_hit_forces_online_stance_abstain_in_real_off_mode(monkeypatch):
    """真資料·$0 檔位（data_mode=live, llm_mode=off）+ online-stance 開關已開
    ＋今日已達 cap → stance 仍必須維持離線（不能因為 online-stance 開關開著
    就繞過每日上限），並補上「已達上限」degrade 說明。"""
    monkeypatch.setenv("TRUSTFORGE_ONLINE_STANCE", "1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "fake-model")
    monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: True)
    monkeypatch.setattr(pl, "collect", _fake_collect)

    captured = []
    orig_cls = pl.BedrockClient

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return orig_cls(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE, data_mode="live", llm_mode="off",
    )
    assert captured[0] == {"offline": True, "stance_offline": True}
    assert any("今日 Bedrock 預算已達上限" in s for s in report.limits)


def test_run_daily_cap_not_hit_but_switch_off_stays_fully_offline_no_note(monkeypatch):
    """cap 未達標，但 online-stance 開關沒開（`online_stance_requested()`
    False）→ stance 仍維持離線，且不應出現任何 degrade 說明（沒有嘗試線上、
    談不上「降級」）。"""
    monkeypatch.delenv("TRUSTFORGE_ONLINE_STANCE", raising=False)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: False)
    monkeypatch.setattr(pl, "collect", _fake_collect)

    captured = []
    orig_cls = pl.BedrockClient

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return orig_cls(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE, data_mode="live", llm_mode="off",
    )
    assert captured[0] == {"offline": True, "stance_offline": True}
    assert not any("線上深度分析" in s for s in report.limits)


# ---------------------------------------------------------------------------
# 7) pipeline.run()：online-stance 開關開＋cap 未達標 → stance 改用「真 Bedrock」
#    （narrative 仍離線，$0；只有 stance 判斷的 client 旗標改變，不代表真的
#    觸發呼叫——本測試資料集刻意只有 1 筆 news，沒有候選配對，見檔頭說明）
# ---------------------------------------------------------------------------

def test_run_online_stance_activates_when_switch_on_and_cap_not_exceeded(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_ONLINE_STANCE", "1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "fake-model")
    monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: False)
    monkeypatch.setattr(pl, "collect", _fake_collect)

    captured = []
    orig_cls = pl.BedrockClient

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return orig_cls(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE, data_mode="live", llm_mode="off",
    )
    # 敘事仍離線（$0），但 stance 判斷改成非離線（可能打真 Bedrock）——
    # 本測試資料無候選配對，不會真的觸發呼叫，只驗證「旗標決策」正確。
    assert captured[0] == {"offline": True, "stance_offline": False}
    assert not any("線上深度分析" in s for s in report.limits)  # 沒有被降級


def test_run_force_stance_offline_degrades_even_when_switch_on_and_cap_not_exceeded(monkeypatch):
    """`force_stance_offline=True`（web.py per-IP online-stance 限流觸發）：
    即使 online-stance 開關開著、cap 也還沒達標，這次請求仍必須誠實 degrade
    回離線 stance，並補上「請求過於頻繁」degrade 說明（非 cap 說明）。"""
    monkeypatch.setenv("TRUSTFORGE_ONLINE_STANCE", "1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "fake-model")
    monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: False)
    monkeypatch.setattr(pl, "collect", _fake_collect)

    captured = []
    orig_cls = pl.BedrockClient

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return orig_cls(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE, data_mode="live", llm_mode="off",
        force_stance_offline=True,
    )
    assert captured[0] == {"offline": True, "stance_offline": True}
    assert any("本 IP 線上分析請求過於頻繁" in s for s in report.limits)
    assert not any("今日 Bedrock 預算已達上限" in s for s in report.limits)


# ---------------------------------------------------------------------------
# 8) run_comparison()：force_stance_offline 對兩幣一致套用
# ---------------------------------------------------------------------------

def test_run_comparison_propagates_force_stance_offline_to_both_coins(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_ONLINE_STANCE", "1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "fake-model")
    monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: False)
    monkeypatch.setattr(pl, "collect", _fake_collect)

    captured = []
    orig_cls = pl.BedrockClient

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return orig_cls(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    pl.run_comparison(
        "BTC", "ETH", "比較 BTC 與 ETH", data_mode="live", llm_mode="off",
        force_stance_offline=True,
    )
    assert len(captured) == 2
    assert all(c == {"offline": True, "stance_offline": True} for c in captured)


def test_daily_cap_exceeded_failsafe_on_ledger_read_error():
    """codex HIGH：成本帳本讀取失敗（DynamoDB outage）→ fail-safe 回 True
    （強制本輪 Bedrock 離線、不花錢），不讓帳本故障把分析請求打掛。"""
    from trustforge import budget_guard

    class _BrokenLedger:
        def read_all(self):
            raise RuntimeError("simulated DynamoDB read outage")

    # 帳本讀取炸掉 → daily_cap_exceeded 不 propagate、fail-safe 回 True
    assert budget_guard.daily_cap_exceeded(_BrokenLedger()) is True


def test_daily_cost_usd_failclosed_on_nan_ledger_value():
    """codex HIGH：NaN/Inf/負值帳本成本 → fail-closed(raise)，不讓 cap fail-open。"""
    import math, pytest
    from trustforge import budget_guard
    today = "2026-07-05"
    class _L:
        def read_all(self):
            return [{"ts": today+"T00:00:00", "total_cost_usd": float("nan")}]
    with pytest.raises(ValueError):
        budget_guard.daily_cost_usd(_L(), day=today)
    # daily_cap_exceeded 的 except 接住 → fail-safe True(離線)
    assert budget_guard.daily_cap_exceeded(_L(), now_fn=lambda: __import__('calendar').timegm((2026,7,5,12,0,0,0,0,0))) is True


def test_caps_reject_non_finite_env(monkeypatch):
    from trustforge import budget_guard
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "nan")
    assert budget_guard.daily_cap_usd() == budget_guard.DEFAULT_BEDROCK_DAILY_USD_CAP
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "inf")
    assert budget_guard.request_max_cost_usd() == budget_guard.DEFAULT_REQUEST_MAX_USD


# ---------------------------------------------------------------------------
# #75 CISO 可見性：DynamoDB 後端失效時送 CloudWatch 指標 + 不靜默降級
# ---------------------------------------------------------------------------

class _BrokenBudgetCounter:
    """模擬 `DynamoDBBudgetCounter` 後端不可用：try_reserve / release 都拋
    `BudgetBackendError`（budget_guard 應 fallback 回 process-local，並送
    `BudgetGuardMultiInstanceProtectionDisabled` 指標）。"""

    def try_reserve(self, *, spent_daily=0.0, cost=0.1, cap=1.0, now=None):
        from trustforge.budget_counter import BudgetBackendError

        raise BudgetBackendError("simulated DynamoDB down")

    def release(self, amount, *, now=None):
        from trustforge.budget_counter import BudgetBackendError

        raise BudgetBackendError("simulated DynamoDB down")


class _FakeCW:
    def __init__(self):
        self.calls = []

    def put_metric_data(self, **kwargs):
        self.calls.append(kwargs)


def test_budget_guard_backend_down_emits_metric_and_falls_back(
    monkeypatch, caplog
):
    """#75：DynamoDB 後端不可用時——(1) 不 fail-open：fallback 回 process-local
    預留、第一筆仍成功；(2) 送 CloudWatch 指標 BudgetGuardMultiInstanceProtectionDisabled；
    (3) 記 warning log（證明多實例保護已暫時失效、非靜默降級）。"""
    import logging

    from trustforge import budget_guard, budget_counter, cloudwatch_metrics
    from trustforge.budget_counter import BudgetBackendError

    monkeypatch.setenv("TRUSTFORGE_BUDGET_GUARD_BACKEND", "dynamodb")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "1.0")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.1")
    budget_guard._reset_reservation_for_tests()
    monkeypatch.setattr(
        budget_counter, "_default_counter", lambda: _BrokenBudgetCounter()
    )
    fake_cw = _FakeCW()
    cloudwatch_metrics.set_client_for_tests(fake_cw)
    monkeypatch.setattr(budget_guard, "_last_backend_down_metric_ts", 0.0)

    with caplog.at_level(logging.WARNING, logger="trustforge.budget_guard"):
        cost = budget_guard.try_reserve_request_budget()
    assert cost is not None, "後端失效應 fallback 回 process-local 預留"
    assert "多實例保護已暫時失效" in caplog.text
    metric_calls = [c for c in fake_cw.calls if c.get("MetricData")]
    assert metric_calls, "應送出 BudgetGuardMultiInstanceProtectionDisabled 指標"
    assert (
        metric_calls[0]["MetricData"][0]["MetricName"]
        == cloudwatch_metrics.BUDGET_GUARD_BACKEND_DOWN_METRIC
    )
    # release 也失敗 → 同樣送指標（冷卻期內不重複送）
    budget_guard.release_request_budget(cost)
    # 冷卻：同一瞬間只送一次（reserve 已送，release 在冷卻內不重送）
    assert len([c for c in fake_cw.calls if c.get("MetricData")]) == 1


def test_budget_guard_backend_down_metric_cooldown(monkeypatch):
    """#75：per-process 冷卻——兩次後端失效（不同瞬間）只各送一次指標，不風暴。"""
    from trustforge import budget_guard, budget_counter, cloudwatch_metrics

    monkeypatch.setenv("TRUSTFORGE_BUDGET_GUARD_BACKEND", "dynamodb")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "1.0")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.1")
    budget_guard._reset_reservation_for_tests()
    monkeypatch.setattr(
        budget_counter, "_default_counter", lambda: _BrokenBudgetCounter()
    )
    fake_cw = _FakeCW()
    cloudwatch_metrics.set_client_for_tests(fake_cw)
    # 初始冷卻戳設在很遠的過去，確保第一次（now=0）的呼叫能真的送指標
    monkeypatch.setattr(budget_guard, "_last_backend_down_metric_ts", -1000.0)

    # 第一次（now=0）：送指標
    budget_guard.try_reserve_request_budget(now_fn=lambda: 0.0)
    # 第二次（now=10，仍在 300s 冷卻內）：不重送
    budget_guard.try_reserve_request_budget(now_fn=lambda: 10.0)
    # 第三次（now=400，冷卻過期）：再送一次
    budget_guard.try_reserve_request_budget(now_fn=lambda: 400.0)

    sent = [c for c in fake_cw.calls if c.get("MetricData")]
    assert len(sent) == 2, f"冷卻內只應送 2 次，實際 {len(sent)}"
