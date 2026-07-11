"""D2.5 測試：真實 worst-case accounting（#76）+ 跨實例 durable idempotency
lease（避免重複計費）。

驗收：
  - `estimate_request_max_cost_usd()` 以實際模型單價 × worst-case token
    用量估算，取代固定 $0.05；env 覆寫仍優先。
  - durable lease：跨實例租約原子 acquire/release；別的 owner 持有時本實例
    不 compute（回 `_AnalyzeDedupLeaseBusy`）。
  - 循序相同請求仍 fresh 計算（租約結束即釋放，不被 TTL 卡住）。
"""
from __future__ import annotations

import threading
import time

import pytest

from trustforge import budget_guard
from trustforge.idempotency_lease import (
    JsonLeaseBackend,
    analyze_lease_ttl_seconds,
    set_lease_backend,
)


# ---------------------------------------------------------------------------
# D2.5 / #76 真實 worst-case accounting
# ---------------------------------------------------------------------------
def test_estimate_request_max_cost_uses_priced_models(monkeypatch):
    # 預設無 BEDROCK_MODEL_ID → narrative 貢獻 0；stance（預設 haiku 已計價）
    # 貢獻 > 0。計算值應為正、有限。
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", raising=False)
    val = budget_guard.estimate_request_max_cost_usd()
    assert val > 0 and val == round(val, 6)


def test_estimate_request_max_cost_includes_narrative_when_priced(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", raising=False)
    # narrative 模型不在 PRICING → 貢獻 0（fail-closed 一致）
    monkeypatch.setenv("BEDROCK_MODEL_ID", "totally.unknown.model-9-9")
    without_narrative = budget_guard.estimate_request_max_cost_usd()
    # narrative 模型在 PRICING → 貢獻額外成本
    monkeypatch.setenv("BEDROCK_MODEL_ID", "apac.anthropic.claude-sonnet-4-6")
    with_narrative = budget_guard.estimate_request_max_cost_usd()
    assert with_narrative > without_narrative


def test_request_max_cost_env_override_takes_precedence(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "0.5")
    assert budget_guard.request_max_cost_usd() == 0.5


def test_request_max_cost_floor_is_default_on_bad_env(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", "inf")
    # 壞 env → 計算值；但仍 ≥ DEFAULT_REQUEST_MAX_USD 安全下界
    val = budget_guard.request_max_cost_usd()
    assert val >= budget_guard.DEFAULT_REQUEST_MAX_USD


# ---------------------------------------------------------------------------
# D2.5 durable lease backend
# ---------------------------------------------------------------------------
def test_lease_backend_acquire_release_is_held(tmp_path):
    b = JsonLeaseBackend(tmp_path / "l.json")
    key = "coindesk:BTC|real"
    assert not b.is_held(key)
    assert b.try_acquire(key, "owner-A", 900)
    assert b.is_held(key)
    # 別的 owner 拿不到
    assert not b.try_acquire(key, "owner-B", 900)
    # 原 owner 釋放後可重新取得
    b.release(key, "owner-A")
    assert not b.is_held(key)
    assert b.try_acquire(key, "owner-B", 900)


def test_lease_backend_expired_lease_can_be_reacquired(tmp_path):
    b = JsonLeaseBackend(tmp_path / "l2.json")
    key = "k"
    # TTL=0 → 立刻過期，第二次 acquire 應成功（搶回過期租約）
    assert b.try_acquire(key, "owner-A", 0)
    assert b.try_acquire(key, "owner-B", 0)  # 同一瞬間已過期（expires_at==now）→ 可被搶回
    # 真正「未過期」租約：用短 TTL + 實際等待驗證 is_held 在過期後放行
    b2 = JsonLeaseBackend(tmp_path / "l3.json")
    assert b2.try_acquire(key, "owner-A", 1)
    time.sleep(1.1)
    assert not b2.is_held(key)
    assert b2.try_acquire(key, "owner-B", 900)


def test_lease_ttl_default_is_15_minutes():
    assert analyze_lease_ttl_seconds() == 15 * 60


# ---------------------------------------------------------------------------
# D2.5 _dedup_analyze_call 接上 durable lease
# ---------------------------------------------------------------------------
def test_dedup_lease_busy_blocks_compute(monkeypatch, tmp_path):
    """別的實例已持有租約 → 本實例不 compute，回 `_AnalyzeDedupLeaseBusy`。"""
    from trustforge import web

    backend = JsonLeaseBackend(tmp_path / "lease_busy.json")
    assert backend.try_acquire("shared-key", "other-instance", 900)
    monkeypatch.setattr("trustforge.idempotency_lease.get_lease_backend", lambda: backend)

    called = []

    def compute():
        called.append(1)
        return "done"

    with pytest.raises(web._AnalyzeDedupLeaseBusy):
        web._dedup_analyze_call("shared-key", compute)
    assert called == []  # 絕對不能 compute（避免重複計費）


def test_dedup_happy_path_acquires_and_releases_lease(monkeypatch, tmp_path):
    """租約空閒 → 正常 compute，結束後租約釋放（循序相同請求可再算）。"""
    from trustforge import web

    backend = JsonLeaseBackend(tmp_path / "lease_ok.json")
    monkeypatch.setattr("trustforge.idempotency_lease.get_lease_backend", lambda: backend)

    assert web._dedup_analyze_call("k2", lambda: "result") == "result"
    # 結束後租約已釋放 → 可被下一個「實例」取得（自癒 / 循序 fresh 計算）
    assert backend.try_acquire("k2", "next-instance", 900)


def test_dedup_concurrent_same_param_computes_once(monkeypatch, tmp_path):
    """並發同參數只跑一次 compute（in-memory single-flight + lease 雙重保證）。"""
    from trustforge import web

    backend = JsonLeaseBackend(tmp_path / "lease_conc.json")
    monkeypatch.setattr("trustforge.idempotency_lease.get_lease_backend", lambda: backend)

    counter = {"n": 0}
    lock = threading.Lock()

    def slow_compute():
        with lock:
            counter["n"] += 1
        time.sleep(0.3)  # 讓併發請求都先 join 成 follower
        return "x"

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(web._dedup_analyze_call("same", slow_compute)))
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["x"] * 5
    assert counter["n"] == 1  # 只真的 compute 一次
