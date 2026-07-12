"""issue #155（PR-X）：驗證 news/regulatory live path 打真實 API（非 sample），
對稱 #141 降級日誌，最小 per-source 通路開關（fail-closed 預設全 ON）。

⚠️ social（Reddit）部分**明確排除**：Reddit 2025-11 終止 self-service，
確認不接（見 milestone 收斂指示）。本檔只驗證 news + regulatory 的真實通路
與降級觀測，不新增任何 social/Reddit 連接器（見 `test_social_reddit_excluded`）。
"""
from __future__ import annotations

import pytest

from urllib.error import URLError

from trustforge.ingestion import news, regulatory, base, safe_fetch
from trustforge.ingestion.news import build_news_sources
from trustforge.ingestion.social import build_social_sources

RSS_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk</title>
    <item>
      <title>Bitcoin BTC surges past $70,000</title>
      <link>https://www.coindesk.com/markets/2026/08/01/btc-surge</link>
      <description>Bitcoin BTC has surged amid strong institutional demand.</description>
      <pubDate>Wed, 01 Aug 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

REGULATORY_FTS_FIXTURE = b"""{
  "hits": {
    "total": {"value": 1, "relation": "eq"},
    "hits": [
      {
        "_id": "0001234567-26-000090:btc-risk-factors.htm",
        "_source": {
          "ciks": ["0001234567"],
          "display_names": ["Acme Holdings Inc. (ACME)"],
          "form": "8-K",
          "file_date": "2026-08-01",
          "items": ["1.01"]
        }
      }
    ]
  }
}"""


@pytest.fixture(autouse=True)
def _reset_source_overrides():
    """每測隔離 per-source 通路開關 override。"""
    base.reset_source_enabled_overrides()
    yield
    base.reset_source_enabled_overrides()


# ── 1. news 降級日誌對稱 #141 ─────────────────────────────────────────────────

def test_news_degradation_logs_warning_and_records_state_real(monkeypatch, caplog):
    """news 源抓取失敗：記 WARNING（含 source/url/error_type），並更新
    last_attempts/last_failures/last_degraded（mirror #141）。"""
    import logging

    monkeypatch.setattr(news, "_fetch_url", lambda url: (_ for _ in ()).throw(URLError("refused")))
    src = news.CoinDeskRSSSource()

    with caplog.at_level(logging.WARNING, logger="trustforge.ingestion.news"):
        with pytest.raises(URLError):
            src.fetch("BTC", coin="BTC")

    # 健康度狀態（對稱 #141）
    assert src.last_attempts == 1
    assert src.last_failures == 1
    assert src.last_degraded is True
    # news 級 WARNING，含 source / error 類型，不含任何 secret
    assert any(
        "news 抓取失敗" in r.message and "coindesk" in r.message and "URLError" in r.message
        for r in caplog.records
    )


# ── 2. 測試證明 live 走真實源（非 demo 樣本）────────────────────────────────

def test_news_live_hits_real_coindesk_url_not_sample(monkeypatch):
    """live 模式打真實 coindesk.com URL，不是 demo/ 樣本檔；產出 doc 標
    meta['live_source']=True（與離線樣本區分）。"""
    captured = {}

    def _fake(url, **kwargs):
        captured["url"] = url
        return RSS_FIXTURE

    monkeypatch.setattr(news.safe_fetch, "fetch_url", _fake)
    docs = news.CoinDeskRSSSource().fetch("BTC", coin="BTC")

    assert "coindesk.com" in captured["url"]
    assert "demo" not in captured["url"]          # 不是離線樣本檔
    assert "sample_data" not in captured["url"]
    assert len(docs) >= 1
    assert docs[0].meta.get("live_source") is True


def test_regulatory_live_hits_real_sec_edgar_url(monkeypatch):
    """live 模式打真實 efts.sec.gov URL，不是 demo/ 樣本檔；產出 doc 標
    meta['live_source']=True。"""
    captured = {}

    def _fake(url, **kwargs):
        captured["url"] = url
        return REGULATORY_FTS_FIXTURE

    monkeypatch.setattr(regulatory, "_fetch_url", _fake)
    # 每個查詢詞都打一次，挑第一筆觀察即可
    docs = regulatory.SECFullTextSearchSource().fetch("", coin="")

    assert captured["url"].startswith("https://efts.sec.gov/")
    assert "demo" not in captured["url"]
    assert "sample_data" not in captured["url"]
    assert len(docs) >= 1
    assert docs[0].meta.get("live_source") is True


def test_offline_does_not_call_safe_fetch_and_uses_sample(monkeypatch):
    """offline=True 走 demo/sample_data 樣本，完全不打 safe_fetch；產出 doc
    絕不標 live_source（與 live 模式明確區分）。"""
    called = {"n": 0}

    def _boom(url, **kwargs):
        called["n"] += 1
        raise AssertionError(f"offline 不該打真連接器 API：{url}")

    monkeypatch.setattr(safe_fetch, "fetch_url", _boom)
    docs = base.collect("BTC", coin="BTC", offline=True)

    assert isinstance(docs, list)
    assert called["n"] == 0                     # 完全沒呼叫真 fetch
    assert len(docs) >= 1                        # 確實從樣本讀到資料
    # offline 樣本不含 live_source 標記
    assert all(d.meta.get("live_source") is not True for d in docs)


# ── 3. 最小 per-source 通路開關（fail-closed 預設全 ON）──────────────────────

def test_per_source_switch_default_enabled_fail_closed():
    """預設（未設任何 override）所有源都啟用——fail-closed：誤配置/漏配置
    時傾向繼續抓真實資料，而非悄悄關掉真實源。"""
    assert base.get_source_enabled("coindesk") is True
    assert base.get_source_enabled("sec-gov") is True
    assert base.get_source_enabled("some-never-heard-of-source") is True


def test_per_source_switch_disabled_skips_source(monkeypatch):
    """明確 disabled 的源（coindesk）在 collect 中被跳過，不產出該源 doc、
    也不進入 _failed（是「跳過」而非「失敗」）。"""
    base.set_source_enabled_override("coindesk", False)
    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_FIXTURE)
    failed: list = []
    docs = base.collect("BTC", coin="BTC", sources=[news.CoinDeskRSSSource()],
                         offline=False, _failed=failed)

    assert all(d.source != "coindesk" for d in docs)
    assert "coindesk" not in failed


def test_per_source_switch_enabled_by_default_collects_source(monkeypatch):
    """預設（未 disabled）coindesk 照常納入，產出該源 doc。"""
    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_FIXTURE)
    docs = base.collect("BTC", coin="BTC", sources=[news.CoinDeskRSSSource()],
                         offline=False)
    assert any(d.source == "coindesk" for d in docs)


def test_per_source_switch_admin_disabled_sources_seam(monkeypatch):
    """`sync_source_enabled_from_admin()` 讀 admin_config.disabled_sources
    套用為 override：設 ["coindesk"] 後 coindesk 被跳過。"""
    from trustforge import admin_config

    class _Table:
        def get_item(self, **kwargs):
            return {"Item": {
                "source_id": admin_config.ADMIN_CONFIG_SOURCE,
                "coin": admin_config.ADMIN_CONFIG_COIN,
                "version": 1,
                "disabled_sources": ["coindesk"],
            }}

    store = admin_config.AdminConfigStore()
    store._table = _Table()
    base.sync_source_enabled_from_admin(store)

    assert base.get_source_enabled("coindesk") is False
    assert base.get_source_enabled("decrypt") is True   # 未列出的仍啟用


# ── 4. social（Reddit）明確排除 ─────────────────────────────────────────────

def test_social_reddit_excluded_from_news_regulatory_live_verification():
    """social（Reddit）部分**明確排除**本里程碑：Reddit 2025-11 終止
    self-service，確認不接。這裡只斷言「仍只有既有的 2 個 reddit 源、沒有
    任何新增的 social 連接器」——live 通路驗證僅涵蓋 news + regulatory。"""
    sources = build_social_sources()
    names = {s.name for s in sources}
    assert names == {"reddit-cryptocurrency", "reddit-bitcoin"}
    # 沒有新增任何 social stub / 非 reddit 的 social 連接器
    assert len(sources) == 2
