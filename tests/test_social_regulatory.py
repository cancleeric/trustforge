"""P2-2 / P2-3 社群+監管連接器測試 — CI 不打真網路（monkeypatch _fetch_url）。"""
from __future__ import annotations

import pytest

# ── 本地固定 fixture ──────────────────────────────────────────────────────────

REDDIT_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>r/CryptoCurrency: search results</title>
    <item>
      <title>BTC Bitcoin surges past $70k</title>
      <link>https://www.reddit.com/r/CryptoCurrency/comments/abc123/btc_bitcoin_surges/</link>
      <description>Bitcoin is showing strong institutional demand and bullish structure.</description>
      <pubDate>Sat, 01 Aug 2026 00:00:00 +0000</pubDate>
    </item>
    <item>
      <title>ETH Ethereum price prediction</title>
      <link>https://www.reddit.com/r/CryptoCurrency/comments/def456/eth_ethereum_prediction/</link>
      <description>ETH might reach new highs as network activity increases.</description>
      <pubDate>Fri, 31 Jul 2026 00:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

REDDIT_EMPTY_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel></channel></rss>"""

# ── SEC EDGAR 全文檢索 API fixture（JSON，取代舊版 Atom XML）───────────────────

REGULATORY_FTS_FIXTURE = b"""{
  "hits": {
    "total": {"value": 2, "relation": "eq"},
    "hits": [
      {
        "_id": "0001234567-26-000090:btc-risk-factors.htm",
        "_source": {
          "ciks": ["0001234567"],
          "display_names": ["Acme Holdings Inc. (ACME)"],
          "form": "8-K",
          "file_date": "2026-08-01",
          "items": ["1.01", "2.01"]
        }
      },
      {
        "_id": "0007654321-26-000055:eth-treasury.htm",
        "_source": {
          "ciks": ["0007654321"],
          "display_names": ["Globex Corp (GX)"],
          "form": "10-K",
          "file_date": "2026-07-28",
          "items": []
        }
      }
    ]
  }
}"""

REGULATORY_FTS_NO_HITS_FIXTURE = b"""{
  "hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}
}"""

REGULATORY_FTS_MISSING_CIK_FIXTURE = b"""{
  "hits": {
    "total": {"value": 2, "relation": "eq"},
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
      },
      {
        "_id": "0007654321-26-000055:eth-treasury.htm",
        "_source": {
          "display_names": ["No-CIK Filer"],
          "form": "10-K",
          "file_date": "2026-07-28",
          "items": []
        }
      }
    ]
  }
}"""

REGULATORY_FTS_MALFORMED_ID_FIXTURE = b"""{
  "hits": {
    "total": {"value": 2, "relation": "eq"},
    "hits": [
      {
        "_id": "0001234567-26-000090:btc-risk-factors.htm",
        "_source": {
          "ciks": ["0001234567"],
          "display_names": ["Acme Holdings Inc. (ACME)"],
          "form": "8-K",
          "file_date": "2026-08-01",
          "items": []
        }
      },
      {
        "_id": "no-colon-here",
        "_source": {
          "ciks": ["0007654321"],
          "display_names": ["Globex Corp (GX)"],
          "form": "10-K",
          "file_date": "2026-07-28",
          "items": []
        }
      }
    ]
  }
}"""

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


# ── regulatory.py 測試（SEC EDGAR 全文檢索 API / JSON）────────────────────────

def test_sec_document_fields(monkeypatch):
    """SEC 文件必須有 kind=regulatory / source=sec-gov / url 含 sec.gov / ts > 0。"""
    from trustforge.ingestion import regulatory
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: REGULATORY_FTS_FIXTURE)
    docs = regulatory.SECFullTextSearchSource().fetch("", coin="")
    assert len(docs) >= 1
    d = docs[0]
    assert d.kind == "regulatory"
    assert d.source == "sec-gov"
    assert "sec.gov" in d.url
    assert d.ts > 0
    assert d.meta.get("content_reference")
    assert len(d.meta["content_reference"]) <= 120


def test_sec_url_points_to_sec_gov(monkeypatch):
    """所有 SEC 文件的 url 必須是 https://www.sec.gov/Archives/edgar/data/... 開頭。"""
    from trustforge.ingestion import regulatory
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: REGULATORY_FTS_FIXTURE)
    docs = regulatory.SECFullTextSearchSource().fetch("", coin="")
    for d in docs:
        assert d.url.startswith("https://www.sec.gov/Archives/edgar/data/")


def test_sec_timestamp_parsed(monkeypatch):
    """SEC 文件的 ts 應從 file_date (YYYY-MM-DD) 解析成 UTC epoch。"""
    from datetime import datetime, timezone
    from trustforge.ingestion import regulatory
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: REGULATORY_FTS_FIXTURE)
    docs = regulatory.SECFullTextSearchSource().fetch("", coin="")
    assert len(docs) >= 1
    expected = datetime.strptime("2026-08-01", "%Y-%m-%d").replace(
        tzinfo=timezone.utc
    ).timestamp()
    assert docs[0].ts == expected


def test_sec_source_failure_does_not_crash(monkeypatch):
    """SECFullTextSearchSource 連線失敗 → collect 跳過不崩。"""
    from urllib.error import URLError
    from trustforge.ingestion import regulatory, base
    monkeypatch.setattr(
        regulatory, "_fetch_url",
        lambda url: (_ for _ in ()).throw(URLError("timeout")),
    )
    src = regulatory.SECFullTextSearchSource()
    docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)  # 不拋例外


def test_sec_dedup_across_query_terms(monkeypatch):
    """fetch 對 3 個查詢詞發請求後用 doc.id 去重；同 fixture 三次回傳同一組 hits，
    回傳 Document 數量應等於 fixture 內不重複 _id 數量，而非 hits * 3。"""
    from trustforge.ingestion import regulatory
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: REGULATORY_FTS_FIXTURE)
    docs = regulatory.SECFullTextSearchSource().fetch("", coin="")
    import json as _json
    fixture = _json.loads(REGULATORY_FTS_FIXTURE)
    unique_ids = {h["_id"] for h in fixture["hits"]["hits"]}
    assert len(docs) == len(unique_ids)
    assert len(unique_ids) == 2


def test_sec_hit_missing_cik_skipped(monkeypatch):
    """hit 缺 _source.ciks（或空陣列）時應被跳過，不崩、不產生空 url Document。"""
    from trustforge.ingestion import regulatory
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: REGULATORY_FTS_MISSING_CIK_FIXTURE)
    docs = regulatory.SECFullTextSearchSource().fetch("", coin="")
    assert len(docs) == 1
    for d in docs:
        assert "sec.gov" in d.url
        assert d.url != ""


def test_sec_hit_malformed_id_skipped(monkeypatch):
    """hit 的 _id 沒有冒號（格式不符）時應被跳過，不崩。"""
    from trustforge.ingestion import regulatory
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: REGULATORY_FTS_MALFORMED_ID_FIXTURE)
    docs = regulatory.SECFullTextSearchSource().fetch("", coin="")
    assert len(docs) == 1
    for d in docs:
        assert "sec.gov" in d.url


def test_build_regulatory_sources():
    """build_regulatory_sources 應回傳 1 個來源，name=sec-gov / kind=regulatory。"""
    from trustforge.ingestion.regulatory import build_regulatory_sources
    sources = build_regulatory_sources()
    assert len(sources) == 1
    assert sources[0].name == "sec-gov"
    assert sources[0].kind == "regulatory"


# ── collect 整合測試 ──────────────────────────────────────────────────────────

def test_collect_online_includes_social_and_regulatory(monkeypatch, tmp_path):
    """collect offline=False 同時產出 social 與 regulatory 文件。

    階段2（cache + 排程 fetcher）後，collect() 的線上預設路徑改成一律經
    `CachedSource` 讀快取（見 `ingestion/cache.py`）。這裡比照
    `scripts/fetch_scheduler.py` 的寫入方式，先用（monkeypatch 過
    `_fetch_url` 的）真 source 各自 fetch 一次寫入測試用 cache backend，
    驗證 collect() 端到端仍能正確讀出 social/regulatory 文件——CachedSource
    本身的命中/降級邏輯已在 test_connector_cache.py 完整覆蓋。
    """
    import time
    from trustforge.ingestion import news, onchain, social, regulatory, base
    from trustforge.ingestion import cache as cache_mod
    from trustforge.ingestion.news import build_news_sources
    from trustforge.ingestion.onchain import build_onchain_sources
    from trustforge.ingestion.social import build_social_sources
    from trustforge.ingestion.regulatory import build_regulatory_sources

    monkeypatch.setattr(news, "_fetch_url", lambda url: _RSS_MINIMAL)
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: _FNG_MINIMAL)
    monkeypatch.setattr(social, "_fetch_url", lambda url: REDDIT_FIXTURE)
    # 新版 regulatory._fetch_url 對每個查詢詞會被呼叫 3 次，回同一份 JSON fixture
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: REGULATORY_FTS_FIXTURE)
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)

    backend = cache_mod.JsonCacheBackend(tmp_path / "cache.json")
    monkeypatch.setattr(cache_mod, "get_cache_backend", lambda: backend)
    all_sources = (
        build_news_sources() + build_onchain_sources()
        + build_social_sources() + build_regulatory_sources()
    )
    for src in all_sources:
        # 同 test_news_onchain.py::test_collect_online_produces_news_and_onchain
        # 的說明：`_FNG_MINIMAL` 對 mempool-space-*/blockchair 三個新源是
        # 缺欄位的無效 payload，嚴格驗證會 raise（見 onchain.py），比照真
        # 排程器行為單獨跳過、不中斷其他來源；這裡只驗 collect() 有沒有
        # 正確接上各 kind 的線上快取路徑。
        try:
            raw_docs = src.fetch("BTC", coin="BTC")
        except Exception:
            continue
        backend.set(
            cache_mod.cache_key(src.name, "BTC"),
            [cache_mod.doc_to_dict(d) for d in raw_docs],
            fetched_at=time.time(),
        )

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


# ── 新測試（C / D / E / F）────────────────────────────────────────────────────

def test_reddit_urlencode_query_in_url(monkeypatch):
    """C: query 含特殊字元時 URL 必須正確 percent-encode，不能裸露 & 改寫請求。"""
    from trustforge.ingestion import social

    captured = {}

    def _mock_fetch(url: str) -> bytes:
        captured["url"] = url
        return REDDIT_EMPTY_FIXTURE

    monkeypatch.setattr(social, "_fetch_url", _mock_fetch)
    social.RedditCryptoSource("CryptoCurrency").fetch("btc&limit=100", coin="")
    url = captured["url"]
    # 特殊字元需被編碼
    assert "btc%26limit%3D100" in url or "btc&amp;limit" in url or "btc%26" in url, (
        f"URL 未正確 encode：{url}"
    )
    # 不應出現裸露的注入字串
    assert "q=btc&limit=100" not in url


def test_reddit_rss_missing_fields_no_crash(monkeypatch):
    """D: RSS item 缺 title/description/link/pubDate 時不崩潰，回傳空白預設值。"""
    from trustforge.ingestion import social

    bad_rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
  </item>
</channel></rss>"""

    monkeypatch.setattr(social, "_fetch_url", lambda url: bad_rss)
    # 不應拋 AttributeError / TypeError
    docs = social.RedditCryptoSource("CryptoCurrency").fetch("", coin="")
    assert isinstance(docs, list)
    if docs:
        assert docs[0].text == ""
        assert docs[0].ts == 0.0
        assert docs[0].url == ""


def test_reddit_atom_feed_parsed(monkeypatch):
    """B/D: Reddit 實際回傳 Atom feed（<entry>），解析器須正確處理。"""
    from trustforge.ingestion import social

    atom_fixture = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>r/CryptoCurrency: bitcoin</title>
  <entry>
    <title>Bitcoin hits new ATH</title>
    <link href="https://www.reddit.com/r/CryptoCurrency/comments/xyz/bitcoin_ath/" rel="alternate"/>
    <updated>2026-08-01T00:00:00+00:00</updated>
    <summary>BTC breaks all-time high on institutional inflows.</summary>
  </entry>
</feed>"""

    monkeypatch.setattr(social, "_fetch_url", lambda url: atom_fixture)
    docs = social.RedditCryptoSource("CryptoCurrency").fetch("bitcoin", coin="")
    assert len(docs) == 1
    d = docs[0]
    assert "bitcoin" in d.text.lower() or "bitcoin" in d.meta["content_reference"].lower()
    assert d.url == "https://www.reddit.com/r/CryptoCurrency/comments/xyz/bitcoin_ath/"
    assert d.ts == 1785542400.0
    assert d.source == "reddit-cryptocurrency"


def test_reddit_rss_author_captured_in_meta(monkeypatch):
    """W3 前置：RSS 2.0 <author> 存進 meta["author"]，原文不剝除前綴。"""
    from trustforge.ingestion import social

    rss_with_author = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>BTC breaks resistance</title>
    <link>https://www.reddit.com/r/CryptoCurrency/comments/abc/btc/</link>
    <description>Bitcoin bulls in control.</description>
    <pubDate>Sat, 01 Aug 2026 00:00:00 +0000</pubDate>
    <author>/u/crypto_trader_99</author>
  </item>
</channel></rss>"""

    monkeypatch.setattr(social, "_fetch_url", lambda url: rss_with_author)
    docs = social.RedditCryptoSource("CryptoCurrency").fetch("", coin="")
    assert len(docs) == 1
    assert docs[0].meta.get("author") == "/u/crypto_trader_99"


def test_reddit_atom_author_captured_in_meta(monkeypatch):
    """W3 前置：Atom <author><name> 存進 meta["author"]。"""
    from trustforge.ingestion import social

    atom_with_author = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>r/CryptoCurrency: bitcoin</title>
  <entry>
    <title>Bitcoin hits new ATH</title>
    <link href="https://www.reddit.com/r/CryptoCurrency/comments/xyz/bitcoin_ath/" rel="alternate"/>
    <updated>2026-08-01T00:00:00+00:00</updated>
    <summary>BTC breaks all-time high on institutional inflows.</summary>
    <author><name>/u/hodler_jane</name></author>
  </entry>
</feed>"""

    monkeypatch.setattr(social, "_fetch_url", lambda url: atom_with_author)
    docs = social.RedditCryptoSource("CryptoCurrency").fetch("bitcoin", coin="")
    assert len(docs) == 1
    assert docs[0].meta.get("author") == "/u/hodler_jane"


def test_reddit_missing_author_no_key_no_crash(monkeypatch):
    """optional 欄位：無 <author> 時 meta 缺鍵，不補假值、不崩潰（沿用既有
    REDDIT_FIXTURE，本來就沒有 author 欄位）。"""
    from trustforge.ingestion import social

    monkeypatch.setattr(social, "_fetch_url", lambda url: REDDIT_FIXTURE)
    docs = social.RedditCryptoSource("CryptoCurrency").fetch("", coin="")
    assert len(docs) == 2
    for d in docs:
        assert "author" not in d.meta


def test_reddit_permalink_no_double_domain(monkeypatch):
    """F: permalink 已含完整 https://www.reddit.com 時，url 不能雙重前綴。"""
    from trustforge.ingestion import social

    full_url_rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Full URL test</title>
    <link>https://www.reddit.com/r/CryptoCurrency/comments/xyz/full/</link>
    <pubDate>Sat, 01 Aug 2026 00:00:00 +0000</pubDate>
  </item>
</channel></rss>"""

    monkeypatch.setattr(social, "_fetch_url", lambda url: full_url_rss)
    docs = social.RedditCryptoSource("CryptoCurrency").fetch("", coin="")
    assert len(docs) == 1
    d = docs[0]
    # 不應出現雙 domain
    assert d.url.count("https://www.reddit.com") == 1
    assert d.url == "https://www.reddit.com/r/CryptoCurrency/comments/xyz/full/"


def test_sec_industry_level_marked(monkeypatch):
    """E: SEC 文件必須在 meta 標示 regulatory_scope=industry-level（業界級監管背景）。"""
    from trustforge.ingestion import regulatory
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: REGULATORY_FTS_FIXTURE)
    docs = regulatory.SECFullTextSearchSource().fetch("", coin="")
    assert len(docs) >= 1
    for d in docs:
        assert d.meta.get("regulatory_scope") == "industry-level", (
            f"缺少 regulatory_scope=industry-level，doc={d.id}"
        )
