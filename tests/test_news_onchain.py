"""P0-1 / P0-2 真實連接器測試 — CI 不打真網路（monkeypatch _fetch_url）。"""
from __future__ import annotations

import pytest

# ── 本地固定 fixture ──────────────────────────────────────────────────────────

RSS_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk</title>
    <item>
      <title>Bitcoin BTC surges past $70,000</title>
      <link>https://www.coindesk.com/markets/2026/08/01/btc-surge</link>
      <description>Bitcoin BTC has surged amid strong institutional demand and on-chain metrics.</description>
      <pubDate>Wed, 01 Aug 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

FNG_FIXTURE = b"""{
  "data": [
    {"value": "38", "value_classification": "Fear", "timestamp": "1785542400"},
    {"value": "42", "value_classification": "Fear", "timestamp": "1785456000"}
  ]
}"""

BINFO_FIXTURE = b"""{
  "market_price_usd": 67823.45,
  "hash_rate": 650000000,
  "timestamp": 1785542400000
}"""

CRYPTOPANIC_FIXTURE = b"""{
  "results": [
    {
      "title": "BTC reaches new ATH",
      "url": "https://cryptopanic.com/news/12345/btc-ath",
      "published_at": "2026-08-01T10:00:00Z"
    }
  ]
}"""


# ── news.py 測試 ──────────────────────────────────────────────────────────────

def test_coindesk_rss_document_fields(monkeypatch):
    """CoinDesk RSS 解析結果必須有真實 URL / ts / content_reference ≤ 120 字。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_FIXTURE)
    docs = news.CoinDeskRSSSource().fetch("BTC", coin="BTC")
    assert len(docs) >= 1
    d = docs[0]
    assert d.kind == "news"
    assert d.source == "coindesk"
    assert "coindesk.com" in d.url
    assert d.ts > 0
    assert d.meta.get("content_reference")
    assert len(d.meta["content_reference"]) <= 120


def test_decrypt_rss_document_fields(monkeypatch):
    """Decrypt RSS 解析 source 名稱正確。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_FIXTURE)
    docs = news.DecryptRSSSource().fetch("BTC", coin="BTC")
    assert docs
    assert docs[0].source == "decrypt"
    assert docs[0].kind == "news"


def test_rss_keyword_filter(monkeypatch):
    """query/coin 不符關鍵字時，RSS 條目被過濾掉。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_FIXTURE)
    # fixture 內容是 BTC/Bitcoin，用 XRP 關鍵字應全部過濾
    docs = news.CoinDeskRSSSource().fetch("XRP price prediction", coin="XRP")
    assert len(docs) == 0


def test_rss_no_keyword_returns_all(monkeypatch):
    """query 和 coin 均空時，不過濾，全部回傳。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_FIXTURE)
    docs = news.CoinDeskRSSSource().fetch("", coin="")
    assert len(docs) >= 1


def test_cryptopanic_no_token_returns_empty(monkeypatch):
    """無 CRYPTOPANIC_TOKEN 時 fetch 安靜回傳 []。"""
    from trustforge.ingestion import news
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)
    docs = news.CryptoPanicSource().fetch("BTC", coin="BTC")
    assert docs == []


def test_cryptopanic_with_token_parses_fields(monkeypatch):
    """有 token 時正確解析 url / ts / content_reference。"""
    from trustforge.ingestion import news
    monkeypatch.setenv("CRYPTOPANIC_TOKEN", "fake-token")
    monkeypatch.setattr(news, "_fetch_url", lambda url: CRYPTOPANIC_FIXTURE)
    docs = news.CryptoPanicSource().fetch("BTC", coin="BTC")
    assert len(docs) == 1
    d = docs[0]
    assert d.url == "https://cryptopanic.com/news/12345/btc-ath"
    assert d.ts > 0
    assert d.meta.get("content_reference") == "BTC reaches new ATH"


def test_build_news_sources_no_cryptopanic_when_no_token(monkeypatch):
    """未設 token 時，build_news_sources 不包含 CryptoPanicSource。"""
    from trustforge.ingestion import news
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)
    sources = news.build_news_sources()
    assert len(sources) == 2
    types = [type(s).__name__ for s in sources]
    assert "CryptoPanicSource" not in types


def test_news_source_failure_does_not_crash_collect(monkeypatch):
    """連接器逾時/例外 → collect 跳過該來源，不拋例外。"""
    from urllib.error import URLError
    from trustforge.ingestion import news, base
    monkeypatch.setattr(news, "_fetch_url", lambda url: (_ for _ in ()).throw(URLError("refused")))
    src = news.CoinDeskRSSSource()
    docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)  # 不拋例外


# ── onchain.py 測試 ───────────────────────────────────────────────────────────

def test_fear_greed_document_fields(monkeypatch):
    """FNG 文件 kind/source/url/ts/content_reference 格式正確。"""
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: FNG_FIXTURE)
    docs = onchain.FearGreedSource().fetch("", coin="BTC")
    assert len(docs) == 2
    d = docs[0]
    assert d.kind == "onchain"
    assert d.source == "alternative-me-fng"
    assert "alternative.me" in d.url
    assert d.ts == 1785542400.0
    ref = d.meta["content_reference"]
    assert "Fear & Greed Index" in ref
    assert "38" in ref
    assert "Fear" in ref
    assert "2026" in ref


def test_fear_greed_content_reference_format(monkeypatch):
    """content_reference 符合 DEV-PLAN 指定格式 'Fear & Greed Index: N (X), YYYY-MM-DD'。"""
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: FNG_FIXTURE)
    docs = onchain.FearGreedSource().fetch("", coin="")
    ref = docs[0].meta["content_reference"]
    # 格式：Fear & Greed Index: 38 (Fear), 2026-07-31
    assert ref.startswith("Fear & Greed Index: 38 (Fear),")


def test_blockchain_info_document_fields(monkeypatch):
    """Blockchain.info 文件含正確 url 與具體數值。"""
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: BINFO_FIXTURE)
    docs = onchain.BlockchainInfoSource().fetch("", coin="BTC")
    assert len(docs) == 1
    d = docs[0]
    assert d.kind == "onchain"
    assert d.source == "blockchain-info"
    assert "blockchain.info" in d.url
    assert "67823" in d.meta["content_reference"]


def test_blockchain_info_skipped_for_non_btc(monkeypatch):
    """非 BTC 幣種時 BlockchainInfoSource 回傳空 list。"""
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: BINFO_FIXTURE)
    assert onchain.BlockchainInfoSource().fetch("", coin="ETH") == []
    assert onchain.BlockchainInfoSource().fetch("", coin="SOL") == []


def test_blockchain_info_btc_coin_empty_string(monkeypatch):
    """coin='' 時 BlockchainInfoSource 不跳過（通用查詢）。"""
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: BINFO_FIXTURE)
    docs = onchain.BlockchainInfoSource().fetch("", coin="")
    assert len(docs) == 1


def test_onchain_source_failure_does_not_crash_collect(monkeypatch):
    """FearGreedSource 逾時 → collect 跳過不崩。"""
    from urllib.error import URLError
    from trustforge.ingestion import onchain, base
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: (_ for _ in ()).throw(URLError("timeout")))
    src = onchain.FearGreedSource()
    docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)


# ── collect 整合測試 ──────────────────────────────────────────────────────────

def test_collect_online_produces_news_and_onchain(monkeypatch):
    """collect offline=False + sources=None 應同時產出 news 與 onchain 文件。"""
    from trustforge.ingestion import news, onchain, base
    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_FIXTURE)
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: FNG_FIXTURE)
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)

    docs = base.collect("BTC", coin="BTC", offline=False)
    kinds = {d.kind for d in docs}
    assert "news" in kinds, f"缺 news，got kinds={kinds}"
    assert "onchain" in kinds, f"缺 onchain，got kinds={kinds}"


def test_collect_offline_still_uses_sample_data():
    """offline=True 路徑不受新連接器影響，仍用 sample json。"""
    from trustforge.ingestion import base
    docs = base.collect("BTC", coin="BTC", offline=True)
    assert isinstance(docs, list)
    # sample_data/onchain.json 至少有 1 筆
    onchain_docs = [d for d in docs if d.kind == "onchain"]
    assert len(onchain_docs) >= 1


def test_collect_coin_passed_to_sources(monkeypatch):
    """collect 的 coin 參數正確傳入 source.fetch。"""
    from trustforge.ingestion import base

    received: list[str] = []

    class TrackingSource(base.Source):
        kind = "news"
        name = "tracker"

        def fetch(self, query: str, coin: str = "") -> list[base.Document]:
            received.append(coin)
            return []

    base.collect("query", coin="ETH", sources=[TrackingSource()], offline=False)
    assert received == ["ETH"]


# ── blockchain.info ms 修正後 ts 年份驗證 ─────────────────────────────────────

def test_blockchain_info_ts_year_in_valid_range(monkeypatch):
    """修正後 BlockchainInfoSource 解析出的 ts 對應年份應在 2020–2030（非離譜未來年份）。"""
    from datetime import datetime, timezone
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: BINFO_FIXTURE)
    docs = onchain.BlockchainInfoSource().fetch("", coin="BTC")
    assert len(docs) == 1
    ts = docs[0].ts
    year = datetime.fromtimestamp(ts, tz=timezone.utc).year
    assert 2020 <= year <= 2030, (
        f"blockchain.info ts 對應年份應在 2020–2030，實際 {year}（ts={ts}）"
    )


# ── iso_utc 防禦化測試 ────────────────────────────────────────────────────────

def test_iso_utc_normal_ts_returns_valid_date():
    """正常 epoch 秒應回傳合法 ISO8601 字串。"""
    from trustforge.schema import iso_utc
    result = iso_utc(1785542400.0)
    assert result.startswith("2026-")
    assert result.endswith("Z")


def test_iso_utc_zero_returns_empty():
    """ts=0 應回 ''。"""
    from trustforge.schema import iso_utc
    assert iso_utc(0.0) == ""


def test_iso_utc_negative_returns_empty():
    """ts<0 應回 ''。"""
    from trustforge.schema import iso_utc
    assert iso_utc(-1.0) == ""


def test_iso_utc_millisecond_ts_returns_empty_no_raise():
    """ms 級 ts（如 blockchain.info 未修正前的值）應回 ''，不拋例外。"""
    from trustforge.schema import iso_utc
    # 1785542400000 ms ≈ year 58516，超出合理範圍
    result = iso_utc(1785542400000.0)
    assert result == "", f"預期空字串，實際 '{result}'"


def test_iso_utc_huge_value_no_raise():
    """極大值不應拋例外，應回 ''。"""
    from trustforge.schema import iso_utc
    assert iso_utc(9e18) == ""
