"""#9 codex HIGH 追加 — 並行 TOCTOU 修復驗證：process-local 原子預留。

背景（見 `budget_guard.py` 對應 docstring）：`daily_cap_exceeded()` 讀的是
「已完成 run」的歷史帳本彙總，但成本要等 pipeline 跑完才 append 回帳本。
公開 `/api/analyze` 走 `ThreadingHTTPServer`（每請求一條執行緒、同 process），
N 個並行請求在同一瞬間看到的是同一份「今日已花費」總額 → check-then-spend
race，daily cap 被並行撐爆。修法是 `budget_guard.BudgetReservation`：呼叫
Bedrock 前先在 `threading.Lock` 內原子預留這次請求的保守成本上界。

⛔ 全程不打真 AWS/Bedrock：
- 單元層測試（第 1 節）直接對 `BudgetReservation`/`try_reserve_request_budget`
  下手，純記憶體 + `threading.Lock`，無網路呼叫、無需 `BEDROCK_MODEL_ID`。
- 整合層測試（第 2 節）沿用 `test_budget_guard.py` 的 `_fake_collect`/單一
  news 來源慣例（避免觸發 `classify_stance`），且刻意不設
  `BEDROCK_MODEL_ID`——`offline=False` 的 `BedrockClient.complete()` 會在
  呼叫 boto3 之前就先因「模型未設定」丟 `RuntimeError`（見
  `bedrock.py::complete()`），而 `orchestrator.py` 的 Step3/Step4 都把這個
  例外接住、降級為結構化行文繼續跑完，`pipeline.run()` 正常回傳、成本 $0
  ——100% 不會真的碰網路，但完整走過「拿到預留 → 建構 offline=False 的
  BedrockClient → 呼叫、失敗、降級 → 正常返回 → release」整條路徑。
"""
from __future__ import annotations

import threading

import pytest

import trustforge.budget_guard as bg
import trustforge.pipeline as pl
from trustforge.ingestion.base import Document
from trustforge.ledger import JsonlLedger
from trustforge.schema import QuestionType


# ---------------------------------------------------------------------------
# 1) 單元層：BudgetReservation 本身的並行/邊界行為
# ---------------------------------------------------------------------------


def test_concurrent_reservations_only_admit_budget_bounded_count(monkeypatch):
    """N 個執行緒用 `threading.Barrier` 逼真同時呼叫 `try_reserve()`：
    daily cap=$1.0、單次保守上界=$0.1 → 理論上恰好只能有 10 個成功預留
    （10 * 0.1 = 1.0，未超過 cap），其餘一律回傳 `None`（degrade）。這是
    修復前會被打穿的核心場景：修復前每個執行緒各自讀到「今日已花費=0」
    就直接放行，N 個全部成功；修復後靠鎖內累加 `reserved` 擋住超額。
    """
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "1.0")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.1")

    reservation = bg.BudgetReservation()
    n_threads = 25
    barrier = threading.Barrier(n_threads)
    results: list[float | None] = [None] * n_threads

    def _worker(idx: int) -> None:
        barrier.wait()  # 逼真所有執行緒同一瞬間進 try_reserve()
        results[idx] = reservation.try_reserve()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    admitted = [r for r in results if r is not None]
    denied = [r for r in results if r is None]

    assert len(admitted) == 10, f"應恰好 10 個成功預留，實際 {len(admitted)}：{results}"
    assert len(denied) == n_threads - 10
    assert all(a == pytest.approx(0.1) for a in admitted)
    # reserved 總額不可能超過 cap（核心安全不變式）
    assert reservation._reserved == pytest.approx(1.0)


def test_concurrent_reservations_respect_existing_daily_spend(monkeypatch):
    """已花費（帳本）$0.7 + cap $1.0，單次上界 $0.1 → 只剩 $0.3 空間，
    並行搶只能有 3 個成功（0.3/0.1），驗證「reserved 疊加在真實已花費之
    上」而非取代它。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "1.0")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.1")

    def _now():
        from datetime import datetime, timezone
        return datetime.fromisoformat("2026-07-01T12:00:00").replace(
            tzinfo=timezone.utc
        ).timestamp()

    ledger = JsonlLedger.__new__(JsonlLedger)  # 不落地檔案，純記憶體覆寫 read_all
    ledger.read_all = lambda: [
        {"ts": "2026-07-01T01:00:00+00:00", "total_cost_usd": 0.7},
    ]

    reservation = bg.BudgetReservation()
    n_threads = 12
    barrier = threading.Barrier(n_threads)
    results: list[float | None] = [None] * n_threads

    def _worker(idx: int) -> None:
        barrier.wait()
        results[idx] = reservation.try_reserve(ledger, now_fn=_now)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    admitted = [r for r in results if r is not None]
    assert len(admitted) == 3, f"應恰好 3 個成功預留，實際 {len(admitted)}：{results}"


def test_boundary_remaining_budget_below_single_request_cost_degrades(monkeypatch):
    """邊界：剩餘預算 < 單次最大可能成本 → 該請求必須 degrade（不得四捨五入
    誤放行）。cap=$1.0，先佔用 $0.95（`_reserved` 直接注入模擬先前已預留/
    已花費逼近上限），單次上界 $0.1 → 0.95+0.1=1.05 > 1.0，應拒絕。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "1.0")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.1")

    reservation = bg.BudgetReservation()
    reservation._reserved = 0.95
    assert reservation.try_reserve() is None
    # 拒絕不應該意外動到既有 reserved（不能因為拒絕就多扣/多加）
    assert reservation._reserved == pytest.approx(0.95)


def test_boundary_remaining_budget_exactly_equal_admits() -> None:
    """邊界另一端：剩餘預算恰好等於單次上界（非嚴格小於）→ 應放行
    （`daily_cap_exceeded()` 既有語意是 `spent >= cap` 才算達標，這裡對應
    `spent + reserved + cost > cap` 才拒絕，等於時仍放行，兩者邊界語意
    一致，不應該因為加了並行預留就變嚴格）。"""
    reservation = bg.BudgetReservation()
    reservation._reserved = 0.0
    import os

    os.environ["TRUSTFORGE_BEDROCK_DAILY_USD_CAP"] = "1.0"
    os.environ["TRUSTFORGE_BEDROCK_REQUEST_MAX_USD"] = "1.0"
    try:
        assert reservation.try_reserve() == pytest.approx(1.0)
    finally:
        del os.environ["TRUSTFORGE_BEDROCK_DAILY_USD_CAP"]
        del os.environ["TRUSTFORGE_BEDROCK_REQUEST_MAX_USD"]


def test_reconcile_release_frees_capacity_for_next_request(monkeypatch):
    """完成/失敗後 reconcile：release() 必須把預留歸還，讓下一個請求能用
    回這段額度（不是永久佔用）。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "0.1")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.1")

    reservation = bg.BudgetReservation()
    first = reservation.try_reserve()
    assert first == pytest.approx(0.1)

    # 額度已滿，第二次應該被拒絕
    assert reservation.try_reserve() is None

    # release 之後，額度恢復，下一個請求應該能成功
    reservation.release(first)
    assert reservation._reserved == pytest.approx(0.0)
    second = reservation.try_reserve()
    assert second == pytest.approx(0.1)


def test_release_none_is_noop():
    """呼叫端沒真的拿到預留（`try_reserve()` 回 `None`）時，release(None)
    必須是 no-op，不能拋例外、也不能動到 reserved。"""
    reservation = bg.BudgetReservation()
    reservation._reserved = 0.3
    reservation.release(None)
    assert reservation._reserved == pytest.approx(0.3)


def test_zero_cap_never_reserves(monkeypatch):
    """cap<=0（緊急關閉）沿用既有保護性解讀：直接拒絕預留，不需先讀帳本。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "0")
    reservation = bg.BudgetReservation()
    assert reservation.try_reserve() is None
    assert reservation._reserved == pytest.approx(0.0)


def test_ledger_read_failure_denies_reservation_failsafe():
    """帳本讀取失敗（DynamoDB outage 等）：跟 `daily_cap_exceeded()` 同一套
    fail-safe——無法確認今日已花費就不能放行新的 Bedrock 花費，`try_reserve`
    必須回 `None`（呼叫端視同已達上限，強制離線），不得往下猜測。"""

    class _BrokenLedger:
        def read_all(self):
            raise RuntimeError("simulated DynamoDB read outage")

    reservation = bg.BudgetReservation()
    assert reservation.try_reserve(_BrokenLedger()) is None
    assert reservation._reserved == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 2) 整合層：pipeline.run() 並行呼叫，驗證只有預算內數量真的放行 Bedrock
# ---------------------------------------------------------------------------


def _doc(id: str, kind: str, source: str, text: str = "") -> Document:
    return Document(id=id, kind=kind, source=source, text=text, ts=1_000.0)


def _make_real_docs(coin: str) -> list[Document]:
    """單一 price + 單一 news（不同來源），比照 `test_budget_guard.py` 慣例：
    刻意只有一筆情緒類主張，讓跨源 stance_pairs 沒有候選配對，
    `classify_stance` 在資料層面就不會被呼叫。"""
    return [
        _doc(f"{coin}_p1", "price", "real-hoya-ohlcv", f"{coin} 現價 30000。"),
        _doc(f"{coin}_n1", "news", "real-coindesk-rss", f"{coin} 市場情緒正向。"),
    ]


def _fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
    return _make_real_docs(coin)


def test_pipeline_run_concurrent_requests_only_admit_budget_bounded_bedrock(monkeypatch):
    """N 個執行緒同時呼叫 `pipeline.run(..., llm_mode="bedrock")`：daily
    cap=$0.3、單次上界=$0.1 → 恰好只有 3 個請求真的拿到
    `BedrockClient(offline=False, ...)`（放行 Bedrock），其餘全部被強制
    降級為 `offline=True`（離線 abstain）。修復前（沒有 process-local
    原子預留）：每個執行緒各自讀到帳本「今日已花費=0」（因為誰都還沒
    `append_run()` 寫回），全部 N 個都會被放行——這正是 codex HIGH 描述
    的並行 TOCTOU。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "0.3")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.1")
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)  # 保證不會真的打 boto3
    # 這裡驗證的是「並行預留」本身，跟 codex HIGH（unpriced model 破壞
    # cap）的 model 計價檢查是兩個獨立護欄——後者有自己的專屬測試（見
    # test_pipeline_run_unpriced_narrative_model_never_admits_bedrock 等），
    # 這裡固定當作「narrative model 已計價」以免互相干擾。
    monkeypatch.setattr(pl, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(pl, "collect", _fake_collect)

    orig_cls = pl.BedrockClient
    captured: list[dict] = []
    captured_lock = threading.Lock()

    def spy_cls(*args, **kwargs):
        with captured_lock:
            captured.append(kwargs)
        return orig_cls(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    n_threads = 12
    barrier = threading.Barrier(n_threads)
    # 第二道 barrier：卡在「reservation 決策已完成、即將真的呼叫
    # run_agent_pipeline」這一刻，逼所有 12 個執行緒都做完各自的
    # try_reserve() 決策後才一起放行進入 release()。沒有這道 barrier，
    # 完全離線（無真網路呼叫）的 run_agent_pipeline 會跑得極快，先完成的
    # 執行緒可能在其他執行緒都還沒做出 try_reserve() 決策前就已經
    # release() 釋出額度——那樣「同一時刻最多 3 個 admitted」的不變式沒被
    # 打破（依然安全，只是額度被合法釋出重複利用，不是 race），但會讓
    # 「整場恰好 3 個 admitted」這個斷言變成看時序的假陽性/假陰性，
    # 不適合拿來驗證修復本身。用這道 barrier 把「決策」與「釋放」兩個
    # 階段徹底分開，才能精準驗證「同一瞬間並行時最多只有 cap 允許的數量
    # 被放行」這個核心不變式。
    release_gate = threading.Barrier(n_threads)
    orig_run_agent_pipeline = pl.run_agent_pipeline

    def gated_run_agent_pipeline(*args, **kwargs):
        release_gate.wait(timeout=10)
        return orig_run_agent_pipeline(*args, **kwargs)

    monkeypatch.setattr(pl, "run_agent_pipeline", gated_run_agent_pipeline)

    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def _worker(idx: int) -> None:
        barrier.wait()
        try:
            pl.run(
                "BTC", "分析 BTC", QuestionType.MULTI_SOURCE,
                data_mode="live", llm_mode="bedrock",
            )
        except BaseException as exc:  # pragma: no cover - 只在真的爆炸時記錄
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"pl.run() 不應拋出未預期例外：{errors}"
    assert len(captured) == n_threads

    admitted = [c for c in captured if c["offline"] is False]
    degraded = [c for c in captured if c["offline"] is True]
    assert len(admitted) == 3, f"應恰好 3 個放行真 Bedrock，實際 {len(admitted)}：{captured}"
    assert len(degraded) == n_threads - 3

    # reconcile：所有請求都跑完後，預留額度必須完全釋放歸零，不留任何
    # 永久佔位（下一輪請求應該能重新拿到滿額 $0.3）。
    from trustforge.budget_guard import _RESERVATION
    assert _RESERVATION._reserved == pytest.approx(0.0)


def test_pipeline_run_single_request_degrades_when_remaining_budget_below_max_cost(
    monkeypatch,
):
    """邊界（整合層）：daily cap 已經只剩下小於單次上界的空間 → 單一（非
    並行）請求也必須誠實 degrade 到離線，不得放行。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "1.0")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.5")
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    # 與上一測試同理：跟 unpriced-model 護欄脫鉤，固定當作已計價。
    monkeypatch.setattr(pl, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(pl, "collect", _fake_collect)

    from trustforge.budget_guard import _RESERVATION
    _RESERVATION._reserved = 0.6  # 模擬其他 in-flight 請求已預留掉大半額度

    captured = []
    orig_cls = pl.BedrockClient

    def spy_cls(*args, **kwargs):
        captured.append(kwargs)
        return orig_cls(*args, **kwargs)

    monkeypatch.setattr(pl, "BedrockClient", spy_cls)

    report, evidence, log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE,
        data_mode="live", llm_mode="bedrock",
    )
    assert captured[0] == {"offline": True, "stance_offline": True}
    assert any("今日 Bedrock 預算已達上限" in s for s in report.limits)
    # 這次沒拿到預留，release(None) 應是 no-op，不動到既有 0.6
    assert _RESERVATION._reserved == pytest.approx(0.6)


def test_pipeline_run_reservation_released_even_when_run_agent_pipeline_raises(
    monkeypatch,
):
    """reconcile：即使 pipeline 內部丟例外，`finally` 也必須釋放預留，
    不能永久卡住額度。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "1.0")
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.1")
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    # 與上面測試同理：跟 unpriced-model 護欄脫鉤，固定當作已計價。
    monkeypatch.setattr(pl, "narrative_model_priced", lambda: True)
    monkeypatch.setattr(pl, "collect", _fake_collect)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated run_agent_pipeline crash")

    monkeypatch.setattr(pl, "run_agent_pipeline", _boom)

    from trustforge.budget_guard import _RESERVATION

    with pytest.raises(RuntimeError, match="simulated run_agent_pipeline crash"):
        pl.run(
            "BTC", "分析 BTC", QuestionType.MULTI_SOURCE,
            data_mode="live", llm_mode="bedrock",
        )

    assert _RESERVATION._reserved == pytest.approx(0.0)
