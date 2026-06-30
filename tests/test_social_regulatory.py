"""P2-2 / P2-3 社群+監管連接器測試 — CI 不打真網路（monkeypatch _fetch_url）。"""
from __future__ import annotations

import json
import pytest

# ── 本地固定 fixture ──────────────────────────────────────────────────────────

REDDIT_FIXTURE = json.dumps({
    "data": {
        "children": [
            {
                "data": {
                    "id": "abc123",
                    "title": "BTC Bitcoin surges past $70k",
                    "selftext": "Bitcoin is showing strong institutional demand and bullish structure.",
                    "permalink": "/r/CryptoCurrency/comments/abc123/btc_bitcoin_surges/",
                    "created_utc": 1785542400.0,
                    "subreddit": "CryptoCurrency",
                }
            },
            {
                "data": {
                    "id": "def456",
                    "title": "ETH Ethereum price prediction",
                    "selftext": "ETH might reach new highs as network activity increases.",
                    "permalink": "/r/CryptoCurrency/comments/def456/eth_ethereum_prediction/",
                    "created_utc": 1785456000.0,
                    "subreddit": "CryptoCurrency",
                }
            },
        ]
    }
}).encode()

REDDIT_EMPTY_FIXTURE = json.dumps({"data": {"children": []}}).encode()

SEC_ATOM_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>SEC EDGAR Current Events</title>
  <entry>
    <title>SEC Charges Cryptocurrency Exchange for Securities Violations</title>
    <link href="https://www.sec.gov/news/press-release/2026-100" rel="alternate"/>
    <updated>2026-08-01T10:00:00Z</updated>
    <summary>The SEC today charged a cryptocurrency exchange for failing to register as a national securities exchange.</summary>
  </entry>
  <entry>
    <title>Annual Report Filing by Company XYZ</title>
    <link href="https://www.sec.gov/news/press-release/2026-101" rel="alternate"/>
    <updated>2026-08-01T09:00:00Z</updated>
    <summary>Standard annual 10-K filing covering traditional manufacturing operations.</summary>
  </entry>
</feed>"""

SEC_ATOM_NO_CRYPTO_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>SEC EDGAR</title>
  <entry>
    <title>Regular Annual Report Filing</title>
    <link href="https://www.sec.gov/news/press-release/2026-200" rel="alternate"/>
    <updated>2026-08-01T10:00:00Z</updated>
    <summary>Company files annual 10-K report covering traditional manufacturing operations.</summary>
  </entry>
</feed>"""

_RSS_MINIMAL = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>BTC news</title>
    <link>https://www.coindesk.com/btc</link>
    <pubDate>Wed, 01 Aug 2026 10:00:00 +0000</pubDate>
  </item>
</channel></rss>"""

_FNG_MINIMAL = b'{"data": [{"value": "38", "value_classification": "Fear", "timestamp": "1785542400"}]}'


# ── social.py 測試 ─────────────────────────────────────────────────────────────

def test_reddit_document_fields(monkeypatch):
    """Reddit 文件必須有正確 kind/source/url/ts/content_reference。"""
    from trustforge.ingestion import social
    monkeypatch.setattr(social, "_fetch_url", lambda url: REDDIT_FIXTURE)
    docs = social.RedditCryptoSource("CryptoCurrency").fetch("BTC", coin="BTC")
    assert len(docs) >= 1
    d = docs[0]
    assert d.kind == "social"
    assert d.source == "reddit-cryptocurrency"
    assert "reddit.com" in d.url
    assert "/r/CryptoCurrency/comments/" in d.url
    assert d.ts == 1785542400.0
    assert d.meta.get("content_reference")
    assert len(d.meta["content_reference"]) <= 120


def test_reddit_url_is_real_permalink(monkeypatch):
    """url 欄位必須是 https://www.reddit.com/r/... 真實 permalink。"""
    from trustforge.ingestion import social
    monkeypatch.setattr(social, "_fetch_url", lambda url: REDDIT_FIXTURE)
    docs = social.RedditCryptoSource("CryptoCurrency").fetch("", coin="")
    assert docs[0].url.startswith("https://www.reddit.com/r/")


def test_reddit_coin_filter_keeps_matching(monkeypatch):
    """coin=btc 時，只有含 btc/bitcoin 的貼文通過過濾。"""
    from trustforge.ingestion import social
    monkeypatch.setattr(social, "_fetch_url", lambda url: REDDIT_FIXTURE)
    # fixture 第 1 筆含 BTC/Bitcoin；第 2 筆只含 ETH/Ethereum → 應被過濾
    docs = social.RedditCryptoSource("CryptoCurrency").fetch("BTC", coin="btc")
    assert len(docs) == 1
    combined = (docs[0].text + " " + docs[0].meta["content_reference"]).lower()
    assert "btc" in combined or "bitcoin" in combined


def test_reddit_coin_filter_no_match_returns_empty(monkeypatch):
    """coin=XRP 時，fixture 無 XRP 資料 → 回傳空 list。"""
    from trustforge.ingestion import social
    monkeypatch.setattr(social, "_fetch_url", lambda url: REDDIT_FIXTURE)
    docs = social.RedditCryptoSource("CryptoCurrency").fetch("XRP", coin="xrp")
    assert docs == []


def test_reddit_no_coin_returns_all(monkeypatch):
    """coin='' 時不做幣種過濾，fixture 2 筆全部回傳。"""
    from trustforge.ingestion import social
    monkeypatch.setattr(social, "_fetch_url", lambda url: REDDIT_FIXTURE)
    docs = social.RedditCryptoSource("CryptoCurrency").fetch("", coin="")
    assert len(docs) == 2


def test_reddit_empty_result_no_crash(monkeypatch):
    """Reddit 搜尋無結果時回傳空 list，不崩潰。"""
    from trustforge.ingestion import social
    monkeypatch.setattr(social, "_fetch_url", lambda url: REDDIT_EMPTY_FIXTURE)
    docs = social.RedditCryptoSource("CryptoCurrency").fetch("OBSCURE", coin="OBSCURE")
    assert docs == []


def test_reddit_bitcoin_subreddit_source_name(monkeypatch):
    """r/Bitcoin subreddit source 名稱應為 reddit-bitcoin。"""
    from trustforge.ingestion import social
    monkeypatch.setattr(social, "_fetch_url", lambda url: REDDIT_FIXTURE)
    docs = social.RedditCryptoSource("Bitcoin").fetch("", coin="")
    assert len(docs) == 2
    assert docs[0].source == "reddit-bitcoin"


def test_reddit_source_failure_does_not_crash(monkeypatch):
    """RedditCryptoSource 連線失敗 → collect 跳過不崩。"""
    from urllib.error import URLError
    from trustforge.ingestion import social, base
    monkeypatch.setattr(
        social, "_fetch_url",
        lambda url: (_ for _ in ()).throw(URLError("timeout")),
    )
    src = social.RedditCryptoSource("CryptoCurrency")
    docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)  # 不拋例外


def test_build_social_sources_has_both_subreddits():
    """build_social_sources 應回傳 CryptoCurrency + Bitcoin 兩個來源。"""
    from trustforge.ingestion.social import build_social_sources
    sources = build_social_sources()
    assert len(sources) == 2
    names = {s.name for s in sources}
    assert "reddit-cryptocurrency" in names
    assert "reddit-bitcoin" in names


def test_reddit_invalid_subreddit_raises():
    """不在白名單的 subreddit 應拋 ValueError。"""
    from trustforge.ingestion.social import RedditCryptoSource
    with pytest.raises(ValueError):
        RedditCryptoSource("WallStreetBets")


# ── regulatory.py 測試 ────────────────────────────────────────────────────────

def test_sec_document_fields(monkeypatch):
    """SEC 文件必須有 kind=regulatory / source=sec-gov / url 含 sec.gov / ts > 0。"""
    from trustforge.ingestion import regulatory
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: SEC_ATOM_FIXTURE)
    docs = regulatory.SECRSSSource().fetch("", coin="")
    assert len(docs) >= 1
    d = docs[0]
    assert d.kind == "regulatory"
    assert d.source == "sec-gov"
    assert "sec.gov" in d.url
    assert d.ts > 0
    assert d.meta.get("content_reference")
    assert len(d.meta["content_reference"]) <= 120


def test_sec_crypto_keyword_filter_excludes_non_crypto(monkeypatch):
    """SEC feed 全非加密條目 → 回傳空 list。"""
    from trustforge.ingestion import regulatory
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: SEC_ATOM_NO_CRYPTO_FIXTURE)
    docs = regulatory.SECRSSSource().fetch("", coin="")
    assert docs == []


def test_sec_mixed_feed_keeps_only_crypto(monkeypatch):
    """混合 feed：1 含加密 + 1 不含 → 只回傳 1 筆。"""
    from trustforge.ingestion import regulatory
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: SEC_ATOM_FIXTURE)
    docs = regulatory.SECRSSSource().fetch("", coin="")
    assert len(docs) == 1
    # 確認保留的是加密相關條目
    content = (docs[0].text + " " + docs[0].meta["content_reference"]).lower()
    assert "crypto" in content or "bitcoin" in content or "digital asset" in content


def test_sec_url_points_to_sec_gov(monkeypatch):
    """所有 SEC 文件的 url 必須含 sec.gov 域名。"""
    from trustforge.ingestion import regulatory
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: SEC_ATOM_FIXTURE)
    docs = regulatory.SECRSSSource().fetch("", coin="")
    for d in docs:
        assert "sec.gov" in d.url


def test_sec_timestamp_parsed(monkeypatch):
    """SEC 文件的 ts 應從 <updated> ISO 8601 正確解析。"""
    from trustforge.ingestion import regulatory
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: SEC_ATOM_FIXTURE)
    docs = regulatory.SECRSSSource().fetch("", coin="")
    assert docs[0].ts > 0


def test_sec_source_failure_does_not_crash(monkeypatch):
    """SECRSSSource 連線失敗 → collect 跳過不崩。"""
    from urllib.error import URLError
    from trustforge.ingestion import regulatory, base
    monkeypatch.setattr(
        regulatory, "_fetch_url",
        lambda url: (_ for _ in ()).throw(URLError("timeout")),
    )
    src = regulatory.SECRSSSource()
    docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)  # 不拋例外


def test_build_regulatory_sources():
    """build_regulatory_sources 應回傳 1 個 SECRSSSource，name=sec-gov。"""
    from trustforge.ingestion.regulatory import build_regulatory_sources
    sources = build_regulatory_sources()
    assert len(sources) == 1
    assert sources[0].name == "sec-gov"
    assert sources[0].kind == "regulatory"


# ── collect 整合測試 ──────────────────────────────────────────────────────────

def test_collect_online_includes_social_and_regulatory(monkeypatch):
    """collect offline=False 同時產出 social 與 regulatory 文件。"""
    from trustforge.ingestion import news, onchain, social, regulatory, base
    monkeypatch.setattr(news, "_fetch_url", lambda url: _RSS_MINIMAL)
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: _FNG_MINIMAL)
    monkeypatch.setattr(social, "_fetch_url", lambda url: REDDIT_FIXTURE)
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: SEC_ATOM_FIXTURE)
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)

    docs = base.collect("BTC", coin="BTC", offline=False)
    kinds = {d.kind for d in docs}
    assert "social" in kinds, f"缺 social，got kinds={kinds}"
    assert "regulatory" in kinds, f"缺 regulatory，got kinds={kinds}"


def test_collect_offline_unchanged_by_new_connectors():
    """offline=True 路徑不受新連接器影響，仍用 sample json。"""
    from trustforge.ingestion import base
    docs = base.collect("BTC", coin="BTC", offline=True)
    assert isinstance(docs, list)
    # offline 樣本含 onchain 資料
    onchain_docs = [d for d in docs if d.kind == "onchain"]
    assert len(onchain_docs) >= 1
