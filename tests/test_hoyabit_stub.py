"""issue #154（PR-Y）：HOYA BIT 連接器 interface / stub 契約測試。

待 7/13 企業數據工作坊 spec 到位才接真實 API。本檔驗收 stub 的關鍵安全契約
（codex 對抗審重點）：

  1. 預設 disabled：collect 線上分支**不納入** hoyabit（不污染信任計算）。
  2. 啟用時 `fetch()` 回佔位 Document，且**必定標 `meta["sample"]=True`**，
     絕不可被下游當成真實高權威（hoyabit 聲譽 0.85）。
  3. stub **絕不發出任何真實外部 HTTP 請求**：disabled 時零副作用；啟用時
     也只回佔位、不偷打 API。
  4. `get_depth/get_orderbook/get_trades` 尚未實作，回 NotImplementedError。
"""
from __future__ import annotations

import pytest

from trustforge.ingestion import base, safe_fetch
from trustforge.ingestion.hoyabit import HoyaBitSource, build_hoyabit_sources


@pytest.fixture(autouse=True)
def _reset_source_overrides():
    base.reset_source_enabled_overrides()
    yield
    base.reset_source_enabled_overrides()


def test_build_hoyabit_sources_returns_one_disabled_stub():
    sources = build_hoyabit_sources()
    assert len(sources) == 1
    s = sources[0]
    assert isinstance(s, HoyaBitSource)
    assert s.kind == "hoyabit"
    assert s.name == "hoyabit-ticker"
    assert s.enabled is False          # 預設 disabled（未接真實 API 前不啟用）


def test_hoyabit_default_disabled_unit_contract():
    """預設 disabled（fail-closed 的反面：未接真實 API 前絕不啟用），且
    get_source_enabled 預設回 False。"""
    assert build_hoyabit_sources()[0].enabled is False
    assert base.get_source_enabled("hoyabit-ticker") is False


def test_hoyabit_registered_in_collect_but_disabled_default(monkeypatch):
    """hoyabit 已註冊進 base.collect() 線上分支，但預設 disabled → collect
    不產出任何 hoyabit-ticker 的 doc（不污染信任計算）。"""
    docs = base.collect("BTC", coin="BTC", sources=build_hoyabit_sources(), offline=False)
    assert all(d.source != "hoyabit-ticker" for d in docs)


def test_hoyabit_enabled_returns_sample_placeholder(monkeypatch):
    """明確啟用後，collect 納入 hoyabit，其 fetch() 回佔位 Document 且
    **標 meta['sample']=True**（絕不可被當真實高權威）。"""
    base.set_source_enabled_override("hoyabit-ticker", True)
    assert base.get_source_enabled("hoyabit-ticker") is True

    docs = base.collect("BTC", coin="BTC", sources=build_hoyabit_sources(), offline=False)
    hoya_docs = [d for d in docs if d.source == "hoyabit-ticker"]
    assert len(hoya_docs) == 1
    d = hoya_docs[0]
    assert d.kind == "hoyabit"
    assert d.meta.get("sample") is True
    assert d.meta.get("stub") is True
    assert d.meta.get("disabled") is False     # 啟用狀態反映進 meta


def test_hoyabit_stub_marks_sample_and_makes_no_external_call(monkeypatch):
    """codex 對抗審：stub `fetch()` 完全不呼叫 safe_fetch（無真實外部請求）。
    把 safe_fetch.fetch_url 設成 boom，stub 仍能回佔位 doc。"""
    called = {"n": 0}

    def _boom(url, **kwargs):
        called["n"] += 1
        raise AssertionError(f"stub 不該打真實 API：{url}")

    monkeypatch.setattr(safe_fetch, "fetch_url", _boom)
    src = HoyaBitSource()
    docs = src.fetch("BTC", coin="BTC")
    assert called["n"] == 0                     # 零副作用：沒打任何外部請求
    assert docs[0].meta.get("sample") is True


def test_hoyabit_api_methods_not_implemented():
    """真實 API 方法尚未實作，暫回 NotImplementedError（待 7/13 spec）。"""
    src = HoyaBitSource()
    for method in ("get_depth", "get_orderbook", "get_trades"):
        with pytest.raises(NotImplementedError):
            getattr(src, method)("BTC")


def test_hoyabit_configured_connector_emits_real_document(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_HOYABIT_TICKER_URL", "https://api.hoyabit.example/ticker")
    monkeypatch.setattr(safe_fetch, "fetch_url", lambda *_args, **_kwargs: b'{"data":{"symbol":"BTCUSDT","last":"123.4","change_24h":"1.5"}}')
    source = HoyaBitSource()
    docs = source.fetch("BTC", "BTC")
    assert source.enabled is True
    assert docs[0].meta["live_source"] is True
    assert docs[0].meta["price"] == 123.4
    assert "sample" not in docs[0].meta
