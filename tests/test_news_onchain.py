"""P0-1 / P0-2 真實連接器測試 — CI 不打真網路（monkeypatch _fetch_url）。"""
from __future__ import annotations

import json

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


def test_coindesk_url_has_no_trailing_slash(monkeypatch):
    """生產事故修復：`.../rss/`（帶斜線）已被 CoinDesk 永久重導（308）到
    `.../rss`（無斜線），寫死的白名單 URL 須直接用無斜線版本，不依賴跟轉址
    才能拿到內容。"""
    from trustforge.ingestion import news
    assert news.CoinDeskRSSSource._URL == "https://www.coindesk.com/arc/outboundfeeds/rss"


# ── `_fetch_url` → 共用 SSRF-safe fetch 整合測試 ─────────────────────────────
#
# codex 對抗審第 3 輪 HIGH：`_fetch_url` 原本自帶的「禁自動跟轉 + 逐跳驗證」
# 邏輯已抽成共用模組 `safe_fetch.py`（套用到 news/coingecko/onchain/
# regulatory/social 全部連接器），核心 SSRF 防護（初始 URL 驗證、DNS
# pinning、rebinding 抵抗力、轉址跨 host/私有 IP/跳數上限/legacy 狀態碼）
# 已在 `tests/test_safe_fetch.py` 針對共用模組本身完整覆蓋，不需要在每個
# 連接器各自重測一次底層邏輯。以下只驗證 news.py 的 `_fetch_url` 確實把
# 對的參數（UA/timeout/max_bytes）轉交給 `safe_fetch.fetch_url`，以及
# `SSRFBlockedError` 會原樣往外傳（不被吞掉）。

def test_fetch_url_delegates_to_safe_fetch_with_correct_params(monkeypatch):
    from trustforge.ingestion import news, safe_fetch

    captured: dict = {}

    def _fake_safe_fetch(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return RSS_FIXTURE

    monkeypatch.setattr(news.safe_fetch, "fetch_url", _fake_safe_fetch)
    raw = news._fetch_url("https://www.coindesk.com/arc/outboundfeeds/rss")

    assert raw == RSS_FIXTURE
    assert captured["url"] == "https://www.coindesk.com/arc/outboundfeeds/rss"
    assert captured["user_agent"] == news._UA
    assert captured["timeout"] == news._TIMEOUT
    assert captured["max_bytes"] == news._MAX_BYTES


def test_fetch_url_propagates_ssrf_blocked_error(monkeypatch):
    """`safe_fetch.fetch_url` 判定不安全時拋出的 `SSRFBlockedError`，原樣
    往外傳，不會在 news.py 這層被吞掉或降級成別的錯誤。"""
    from trustforge.ingestion import news, safe_fetch

    def _boom(url, **kwargs):
        raise safe_fetch.SSRFBlockedError(url, "測試用：私有 IP")

    monkeypatch.setattr(news.safe_fetch, "fetch_url", _boom)
    with pytest.raises(safe_fetch.SSRFBlockedError):
        news._fetch_url("https://www.coindesk.com/arc/outboundfeeds/rss")


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


# ── codex MEDIUM（PR #55，資料密度第二批最終閉合）：共用 _parse_rss 要
#    區分「schema drift（無 item/entry）」vs「有 entries、關鍵字篩後合法為
#    空」，前者 raise、後者回 [] ────────────────────────────────────────────

_SCHEMA_DRIFT_NO_ITEM_OR_ENTRY = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://example.com/some-other-namespace">
  <weird-entry>
    <title>vendor switched schema, this is neither RSS item nor Atom entry</title>
  </weird-entry>
</feed>"""

_SCHEMA_DRIFT_ERROR_HTML_AS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<html><body><h1>503 Service Unavailable</h1></body></html>"""

# codex HIGH 第 6 輪：<item> 容器都在，但供應商把 title/link/description/
# pubDate 全部改名成沒人認得的 tag（headline/permalink/summary-text/
# published-at），_first() 找不到對應欄位，全部 entry 解析成空白 title、
# 空 URL、ts=0 的垃圾——這是子欄位層的 schema drift，不是容器層的。
_SUBFIELD_DRIFT_RENAMED_TAGS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk</title>
    <item>
      <headline>Bitcoin BTC surges past $70,000</headline>
      <permalink>https://www.coindesk.com/markets/2026/08/01/btc-surge</permalink>
      <summary-text>Bitcoin BTC has surged amid strong institutional demand.</summary-text>
      <published-at>Wed, 01 Aug 2026 10:00:00 +0000</published-at>
    </item>
    <item>
      <headline>Ethereum ETH also rallies</headline>
      <permalink>https://www.coindesk.com/markets/2026/08/01/eth-surge</permalink>
    </item>
  </channel>
</rss>"""

# 供應商保留 <title>/<link>/<pubDate> tag 名稱，但值全變空字串——同樣是子
# 欄位 drift（別跟「有值但被 coin 關鍵字篩掉」搞混）。
_SUBFIELD_DRIFT_EMPTY_VALUES = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk</title>
    <item>
      <title></title>
      <link></link>
      <description></description>
      <pubDate></pubDate>
    </item>
  </channel>
</rss>"""

# 混合：一筆子欄位 drift（改名）+ 一筆正常 entry——drift 的那筆該被單獨
# 跳過，不該讓整批 raise（整批只在「結構有效 entry 數 == 0」才 raise）。
_SUBFIELD_DRIFT_PARTIAL = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk</title>
    <item>
      <headline>Drifted entry with renamed tags</headline>
      <permalink>https://www.coindesk.com/drifted</permalink>
    </item>
    <item>
      <title>Bitcoin BTC surges past $70,000</title>
      <link>https://www.coindesk.com/markets/2026/08/01/btc-surge</link>
      <description>Bitcoin BTC has surged amid strong institutional demand.</description>
      <pubDate>Wed, 01 Aug 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

# codex MEDIUM 第 7 輪：所有 entry 的 link 都是「有字首但無 host」的殘缺
# URL（`https://` 空、`https:///path` 空 host）——舊版 startswith 檢查會
# 誤判合法，urlsplit 嚴格驗必須擋下，視為子欄位 drift。
_INVALID_HOST_LINK_ALL = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk</title>
    <item>
      <title>Bitcoin BTC surges past $70,000</title>
      <link>https://</link>
      <description>Bitcoin BTC has surged amid strong institutional demand.</description>
      <pubDate>Wed, 01 Aug 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Ethereum ETH also rallies</title>
      <link>https:///article</link>
      <description>Ethereum ETH climbs on strong demand.</description>
      <pubDate>Wed, 01 Aug 2026 11:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

# 混合：一筆 link 無 host（無效）+ 一筆正常絕對 URL——無效那筆該被單獨
# 跳過，不該讓整批 raise。
_INVALID_HOST_LINK_PARTIAL = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk</title>
    <item>
      <title>Bad link entry</title>
      <link>https:///article</link>
      <description>This entry has an unusable link.</description>
      <pubDate>Wed, 01 Aug 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Bitcoin BTC surges past $70,000</title>
      <link>https://www.coindesk.com/markets/2026/08/01/btc-surge</link>
      <description>Bitcoin BTC has surged amid strong institutional demand.</description>
      <pubDate>Wed, 01 Aug 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""


def test_parse_rss_schema_drift_no_item_or_entry_raises(monkeypatch):
    """合法可解析 XML，但完全沒有 `<item>`/Atom `<entry>`（換 namespace/
    schema）——這是 schema drift 訊號，必須 raise，不能靜靜回 [] 讓排程
    覆蓋掉還能用的舊快取。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: _SCHEMA_DRIFT_NO_ITEM_OR_ENTRY)
    with pytest.raises(ValueError):
        news.CoinDeskRSSSource().fetch("", coin="BTC")


def test_parse_rss_error_page_as_xml_raises(monkeypatch):
    """供應商回錯誤頁但仍是合法 XML（如 `<html>503...</html>`）——同樣沒有
    任何 item/entry，必須 raise。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: _SCHEMA_DRIFT_ERROR_HTML_AS_XML)
    with pytest.raises(ValueError):
        news.CoinDeskRSSSource().fetch("", coin="BTC")


def test_parse_rss_entries_exist_but_coin_filter_empty_is_legitimate(monkeypatch):
    """有 entries，但依 coin 關鍵字過濾後一則都不符——這是合法的空結果
    （那個幣剛好沒新聞），不是 schema drift，不該 raise。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_FIXTURE)
    docs = news.CoinDeskRSSSource().fetch("XRP price prediction", coin="XRP")
    assert docs == []


def test_parse_rss_subfield_drift_renamed_tags_raises(monkeypatch):
    """codex HIGH 第 6 輪：<item> 容器都在，但 title/link/description/
    pubDate 全被改名（headline/permalink/...），_first() 找不到對應欄位，
    所有 entry 解析成空白 title/空 URL/ts=0——這是子欄位層 schema drift，
    必須 raise，不能靜靜用空/垃圾覆蓋掉還能用的舊快取。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: _SUBFIELD_DRIFT_RENAMED_TAGS)
    with pytest.raises(ValueError):
        news.CoinDeskRSSSource().fetch("", coin="")


def test_parse_rss_subfield_drift_empty_values_raises(monkeypatch):
    """<title>/<link>/<pubDate> tag 名稱都在，但值全是空字串——同樣是子
    欄位 drift，必須 raise。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: _SUBFIELD_DRIFT_EMPTY_VALUES)
    with pytest.raises(ValueError):
        news.CoinDeskRSSSource().fetch("", coin="")


def test_parse_rss_subfield_drift_never_publishes_blank_document(monkeypatch):
    """即使無關鍵字過濾（收全部），drift 的 entry 也絕不能被發布成空白
    title/空 URL/ts=0 的文件——parse 應在建 Document 前就把它擋掉並
    最終 raise（因為這個 fixture 全部 entry 都 drift）。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: _SUBFIELD_DRIFT_RENAMED_TAGS)
    with pytest.raises(ValueError):
        news.CoinDeskRSSSource().fetch("", coin="")


def test_parse_rss_subfield_drift_partial_skips_bad_entry_keeps_good_one(monkeypatch):
    """一批裡有一筆子欄位 drift（改名）、一筆正常——drift 的那筆該被單獨
    跳過（不發布垃圾文件），但不該讓整批 raise，因為結構有效的 entry
    數不是 0（正常那筆還在）。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: _SUBFIELD_DRIFT_PARTIAL)
    docs = news.CoinDeskRSSSource().fetch("", coin="")
    assert len(docs) == 1
    assert docs[0].text
    assert docs[0].url == "https://www.coindesk.com/markets/2026/08/01/btc-surge"
    assert docs[0].ts != 0.0


def test_is_valid_http_link_rejects_missing_host():
    """codex MEDIUM 第 7 輪：`startswith(("http://","https://"))` 會把
    `https://`（無 host）、`https:///article`（空 host）當合法——urlsplit
    嚴格驗必須擋下這兩種殘缺 URL。"""
    from trustforge.ingestion.news import _is_valid_http_link
    assert _is_valid_http_link("https://") is False
    assert _is_valid_http_link("https:///article") is False
    assert _is_valid_http_link("http://") is False


def test_is_valid_http_link_rejects_credentials_and_bad_scheme():
    from trustforge.ingestion.news import _is_valid_http_link
    assert _is_valid_http_link("https://user:pass@example.com/x") is False
    assert _is_valid_http_link("javascript:alert(1)") is False
    assert _is_valid_http_link("ftp://example.com/x") is False
    assert _is_valid_http_link("") is False


def test_is_valid_http_link_accepts_normal_absolute_url():
    from trustforge.ingestion.news import _is_valid_http_link
    assert _is_valid_http_link("https://www.coindesk.com/markets/2026/08/01/btc-surge") is True


def test_parse_rss_invalid_host_link_all_entries_raises(monkeypatch):
    """所有 entry 的 link 都是無 host 的殘缺 URL（`https://`、
    `https:///article`）→ 結構有效 entry 數為 0，視為子欄位 drift，
    必須 raise（保留舊快取），不能建含不可用 URL 的 Document。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: _INVALID_HOST_LINK_ALL)
    with pytest.raises(ValueError):
        news.CoinDeskRSSSource().fetch("", coin="")


def test_parse_rss_invalid_host_link_partial_skips_bad_entry_keeps_good_one(monkeypatch):
    """一筆 link 無 host（無效）+ 一筆正常絕對 URL——無效那筆該被單獨
    跳過，不該讓整批 raise，正常那筆照常出 Document。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: _INVALID_HOST_LINK_PARTIAL)
    docs = news.CoinDeskRSSSource().fetch("", coin="")
    assert len(docs) == 1
    assert docs[0].url == "https://www.coindesk.com/markets/2026/08/01/btc-surge"


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
    """未設 token 時，build_news_sources 不包含 CryptoPanicSource（資料密度
    第一批 #24 加 6 家新聞 RSS 後基礎來源數從 2 變 8，第二批再加 3 家變 11）。"""
    from trustforge.ingestion import news
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)
    sources = news.build_news_sources()
    assert len(sources) == 11
    types = [type(s).__name__ for s in sources]
    assert "CryptoPanicSource" not in types


# ── 資料密度第一批（#24，docs/archive/plans/PLAN-data-density.md）：6 家新聞 RSS ───────────

@pytest.mark.parametrize(
    "source_cls_name,expected_name,expected_url",
    [
        ("CoinTelegraphRSSSource", "cointelegraph", "https://cointelegraph.com/rss"),
        ("BitcoinMagazineRSSSource", "bitcoinmagazine", "https://bitcoinmagazine.com/feed"),
        ("CryptoSlateRSSSource", "cryptoslate", "https://cryptoslate.com/feed/"),
        ("BitcoinistRSSSource", "bitcoinist", "https://bitcoinist.com/feed/"),
        ("NewsBTCRSSSource", "newsbtc", "https://www.newsbtc.com/feed/"),
        ("DailyHodlRSSSource", "dailyhodl", "https://dailyhodl.com/feed/"),
    ],
)
def test_new_rss_sources_document_fields_and_url(monkeypatch, source_cls_name, expected_name, expected_url):
    """資料密度第一批 6 家新聞 RSS：各自 name/kind/URL 正確，且複用
    `_parse_rss` 能正確解析出 Document（url/ts/content_reference）。"""
    from trustforge.ingestion import news
    source_cls = getattr(news, source_cls_name)
    assert source_cls._URL == expected_url
    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_FIXTURE)
    docs = source_cls().fetch("BTC", coin="BTC")
    assert len(docs) >= 1
    d = docs[0]
    assert d.kind == "news"
    assert d.source == expected_name
    assert d.ts > 0
    assert d.meta.get("content_reference")
    assert len(d.meta["content_reference"]) <= 120


def test_build_news_sources_includes_all_6_new_rss_sources(monkeypatch):
    """build_news_sources() 含新舊共 11 個新聞來源（不含條件式 cryptopanic）。"""
    from trustforge.ingestion import news
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)
    sources = news.build_news_sources()
    names = {s.name for s in sources}
    assert names == {
        "coindesk", "decrypt", "cointelegraph", "bitcoinmagazine",
        "cryptoslate", "bitcoinist", "newsbtc", "dailyhodl",
        "theblock", "utoday", "blockworks",
    }


# ── 資料密度第二批（#24，docs/archive/plans/PLAN-data-density.md）：The Block/U.Today/
# Blockworks 3 家新聞 RSS ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "source_cls_name,expected_name,expected_url",
    [
        ("TheBlockRSSSource", "theblock", "https://www.theblock.co/rss.xml"),
        ("UTodayRSSSource", "utoday", "https://u.today/rss.php"),
        ("BlockworksRSSSource", "blockworks", "https://blockworks.com/feed"),
    ],
)
def test_batch2_rss_sources_document_fields_and_url(monkeypatch, source_cls_name, expected_name, expected_url):
    """資料密度第二批 3 家新聞 RSS：各自 name/kind/URL 正確，且複用
    `_parse_rss` 能正確解析出 Document（url/ts/content_reference）。"""
    from trustforge.ingestion import news
    source_cls = getattr(news, source_cls_name)
    assert source_cls._URL == expected_url
    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_FIXTURE)
    docs = source_cls().fetch("BTC", coin="BTC")
    assert len(docs) >= 1
    d = docs[0]
    assert d.kind == "news"
    assert d.source == expected_name
    assert d.ts > 0
    assert d.meta.get("content_reference")
    assert len(d.meta["content_reference"]) <= 120


ATOM_FIXTURE = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Blockworks</title>
  <entry>
    <title type="html">ETF inflows accelerate amid rate-cut bets</title>
    <id>https://blockworks.com/news/etf-inflows</id>
    <link href="https://blockworks.com/news/etf-inflows"/>
    <summary type="html">Bitcoin BTC ETF inflows accelerate amid rate-cut bets and strong demand.</summary>
    <published>2026-08-01T10:00:00.000Z</published>
  </entry>
</feed>"""


def test_blockworks_atom_feed_parses_via_shared_parse_rss(monkeypatch):
    """Blockworks 回傳 Atom（非 RSS 2.0），驗證 `_parse_rss` 對 atom:entry/
    atom:link[href]/atom:summary/atom:published 的既有相容路徑能正確解析。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: ATOM_FIXTURE)
    docs = news.BlockworksRSSSource().fetch("BTC", coin="BTC")
    assert len(docs) == 1
    d = docs[0]
    assert d.source == "blockworks"
    assert d.url == "https://blockworks.com/news/etf-inflows"
    assert d.ts > 0


DC_CREATOR_RSS_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Bitcoinist</title>
    <item>
      <title>Bitcoin BTC eyes breakout above resistance</title>
      <link>https://bitcoinist.com/btc-breakout</link>
      <description>Bitcoin BTC shows bullish structure amid rising volume.</description>
      <pubDate>Wed, 01 Aug 2026 10:00:00 +0000</pubDate>
      <dc:creator>Jane Analyst</dc:creator>
    </item>
  </channel>
</rss>"""

RSS_AUTHOR_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk</title>
    <item>
      <title>Bitcoin BTC surges past $70,000</title>
      <link>https://www.coindesk.com/markets/2026/08/01/btc-surge</link>
      <description>Bitcoin BTC has surged amid strong institutional demand.</description>
      <pubDate>Wed, 01 Aug 2026 10:00:00 +0000</pubDate>
      <author>john@coindesk.com (John Reporter)</author>
    </item>
  </channel>
</rss>"""

ATOM_AUTHOR_FIXTURE = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Blockworks</title>
  <entry>
    <title type="html">ETF inflows accelerate amid rate-cut bets</title>
    <id>https://blockworks.com/news/etf-inflows</id>
    <link href="https://blockworks.com/news/etf-inflows"/>
    <summary type="html">Bitcoin BTC ETF inflows accelerate amid rate-cut bets.</summary>
    <published>2026-08-01T10:00:00.000Z</published>
    <author><name>Alex Blockworks</name></author>
  </entry>
</feed>"""


def test_rss_dc_creator_captured_in_meta(monkeypatch):
    """W3 前置：WordPress 常見 <dc:creator> 存進 meta["author"]（原文）。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: DC_CREATOR_RSS_FIXTURE)
    docs = news.BitcoinistRSSSource().fetch("BTC", coin="BTC")
    assert len(docs) == 1
    assert docs[0].meta.get("author") == "Jane Analyst"


def test_rss_author_tag_captured_in_meta(monkeypatch):
    """W3 前置：RSS 2.0 <author> 存進 meta["author"]（原文，不解析 email/姓名）。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_AUTHOR_FIXTURE)
    docs = news.CoinDeskRSSSource().fetch("BTC", coin="BTC")
    assert len(docs) == 1
    assert docs[0].meta.get("author") == "john@coindesk.com (John Reporter)"


def test_atom_author_name_captured_in_meta(monkeypatch):
    """W3 前置：Atom <author><name> 存進 meta["author"]。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: ATOM_AUTHOR_FIXTURE)
    docs = news.BlockworksRSSSource().fetch("BTC", coin="BTC")
    assert len(docs) == 1
    assert docs[0].meta.get("author") == "Alex Blockworks"


def test_rss_missing_author_no_key_no_crash(monkeypatch):
    """optional 欄位：無作者標籤時 meta 缺鍵，不補假值、不崩潰（沿用既有
    RSS_FIXTURE，本來就沒有作者欄位）。"""
    from trustforge.ingestion import news
    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_FIXTURE)
    docs = news.CoinDeskRSSSource().fetch("BTC", coin="BTC")
    assert len(docs) >= 1
    for d in docs:
        assert "author" not in d.meta


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


# ── 資料密度第二批（#24，docs/archive/plans/PLAN-data-density.md）：mempool.space 2 端點 +
# Blockchair ─────────────────────────────────────────────────────────────────

MPFEES_FIXTURE = b"""{
  "fastestFee": 12, "halfHourFee": 8, "hourFee": 6, "economyFee": 3, "minimumFee": 1
}"""

MPDIFF_FIXTURE = b"""{
  "progressPercent": 44.39, "difficultyChange": -1.43, "remainingBlocks": 1121,
  "estimatedRetargetDate": 1783754115859
}"""

BLOCKCHAIR_FIXTURE = b"""{
  "data": {
    "blocks": 956480, "difficulty": 133869853540305.4,
    "mempool_transactions": 82785, "transactions_24h": 573582,
    "best_block_time": "2026-07-03 09:18:09"
  },
  "context": {"code": 200}
}"""

# codex MEDIUM（PR #55，第 5 輪，有界新鮮度窗）：`best_block_time` 現在會
# 拿真牆鐘 `time.time()` 做新鮮度檢查，`BLOCKCHAIR_FIXTURE` 裡寫死的日期
# 不能再假設「反正是未來，測試永遠在窗內」——用固定注入的 now，別依賴真
# 牆鐘飄（coordinator 明確要求）。`_BLOCKCHAIR_FIXED_NOW` 對應
# `BLOCKCHAIR_FIXTURE.best_block_time`（2026-07-03 09:18:09 UTC）之後 12
# 分鐘，落在 6 小時新鮮度窗內、也不是未來時間戳。
import datetime as _dt  # noqa: E402 - 就近放在使用它的 fixture 常數旁

_BLOCKCHAIR_FIXED_NOW = _dt.datetime(2026, 7, 3, 9, 30, 0, tzinfo=_dt.timezone.utc).timestamp()


def test_mempool_space_fees_document_fields(monkeypatch):
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: MPFEES_FIXTURE)
    docs = onchain.MempoolSpaceFeesSource().fetch("", coin="BTC")
    assert len(docs) == 1
    d = docs[0]
    assert d.kind == "onchain"
    assert d.source == "mempool-space-fees"
    assert "mempool.space" in d.url
    assert d.ts > 0
    ref = d.meta["content_reference"]
    assert "最快=12" in ref
    assert "最低=1" in ref


def test_mempool_space_fees_skipped_for_non_btc(monkeypatch):
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: MPFEES_FIXTURE)
    assert onchain.MempoolSpaceFeesSource().fetch("", coin="ETH") == []


def test_mempool_space_difficulty_document_fields(monkeypatch):
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: MPDIFF_FIXTURE)
    docs = onchain.MempoolSpaceDifficultySource().fetch("", coin="BTC")
    assert len(docs) == 1
    d = docs[0]
    assert d.kind == "onchain"
    assert d.source == "mempool-space-difficulty"
    assert "mempool.space" in d.url
    assert d.ts > 0
    ref = d.meta["content_reference"]
    assert "44.4%" in ref
    assert "1121" in ref


def test_mempool_space_difficulty_skipped_for_non_btc(monkeypatch):
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: MPDIFF_FIXTURE)
    assert onchain.MempoolSpaceDifficultySource().fetch("", coin="SOL") == []


def test_blockchair_document_fields(monkeypatch):
    """用固定注入的 now（`_BLOCKCHAIR_FIXED_NOW`）測，別依賴真牆鐘——新鮮度
    窗（codex MEDIUM 第 5 輪）比較的是 `time.time()`，固定注入才能讓測試
    結果不隨真實跑測試的日期改變。"""
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: BLOCKCHAIR_FIXTURE)
    monkeypatch.setattr(onchain.time, "time", lambda: _BLOCKCHAIR_FIXED_NOW)
    docs = onchain.BlockchairStatsSource().fetch("", coin="BTC")
    assert len(docs) == 1
    d = docs[0]
    assert d.kind == "onchain"
    assert d.source == "blockchair"
    assert "blockchair.com" in d.url
    ref = d.meta["content_reference"]
    assert "956480" in ref
    assert "82785" in ref
    # best_block_time "2026-07-03 09:18:09" UTC → 對應 epoch
    expected_ts = _dt.datetime(2026, 7, 3, 9, 18, 9, tzinfo=_dt.timezone.utc).timestamp()
    assert d.ts == expected_ts


def test_blockchair_skipped_for_non_btc(monkeypatch):
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: BLOCKCHAIR_FIXTURE)
    assert onchain.BlockchairStatsSource().fetch("", coin="ETH") == []


def test_blockchair_bad_best_block_time_raises(monkeypatch):
    """codex MEDIUM（PR #55，鏈上驗證最終閉合）：`best_block_time` 格式不符
    也視為必要欄位驗證失敗，必須拋例外——不能退回 `time.time()` 把「上游
    回歷史統計/不完整 envelope」偽裝成剛取得的新證據去覆蓋舊快取（反轉
    上一輪的 `..._falls_back_to_now`，那個設計違反本批的核心不變量）。"""
    from trustforge.ingestion import onchain
    bad_fixture = (
        b'{"data": {"blocks": 1, "difficulty": 2, "mempool_transactions": 3, '
        b'"transactions_24h": 4, "best_block_time": "not-a-date"}, '
        b'"context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad_fixture)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_missing_best_block_time_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad_fixture = (
        b'{"data": {"blocks": 1, "difficulty": 2, "mempool_transactions": 3, '
        b'"transactions_24h": 4}, "context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad_fixture)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_null_best_block_time_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad_fixture = (
        b'{"data": {"blocks": 1, "difficulty": 2, "mempool_transactions": 3, '
        b'"transactions_24h": 4, "best_block_time": null}, "context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad_fixture)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_non_string_best_block_time_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad_fixture = (
        b'{"data": {"blocks": 1, "difficulty": 2, "mempool_transactions": 3, '
        b'"transactions_24h": 4, "best_block_time": 1783754115}, "context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad_fixture)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


# ── codex MEDIUM（PR #55，第 5 輪，CEO 決策：有界新鮮度窗）：
#    best_block_time 語法正確不代表新鮮，供應商可能重放/命中陳舊快取
#    ——全部用固定注入的 now，不依賴真牆鐘（coordinator 明確要求）────────────

def _blockchair_payload_with_best_block_time(bbt: str) -> bytes:
    return (
        b'{"data": {"blocks": 956480, "difficulty": 133869853540305.4, '
        b'"mempool_transactions": 82785, "transactions_24h": 573582, '
        b'"best_block_time": "' + bbt.encode() + b'"}, "context": {"code": 200}}'
    )


def test_blockchair_future_best_block_time_raises(monkeypatch):
    """`best_block_time` 是明顯未來時間戳（超過 10 分鐘容差）→ 拋例外，
    不建 Document——這是 bogus 資料的訊號。"""
    from trustforge.ingestion import onchain
    fixed_now = _dt.datetime(2026, 7, 3, 9, 30, 0, tzinfo=_dt.timezone.utc)
    future_bbt = (fixed_now + _dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: _blockchair_payload_with_best_block_time(future_bbt))
    monkeypatch.setattr(onchain.time, "time", lambda: fixed_now.timestamp())
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_stale_best_block_time_raises(monkeypatch):
    """`best_block_time` 超過 6 小時新鮮度窗（過舊，可能是重放的陳舊
    payload）→ 拋例外，不建 Document、不覆蓋舊快取。"""
    from trustforge.ingestion import onchain
    fixed_now = _dt.datetime(2026, 7, 3, 9, 30, 0, tzinfo=_dt.timezone.utc)
    stale_bbt = (fixed_now - _dt.timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: _blockchair_payload_with_best_block_time(stale_bbt))
    monkeypatch.setattr(onchain.time, "time", lambda: fixed_now.timestamp())
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_recent_best_block_time_within_window_succeeds(monkeypatch):
    """`best_block_time` 落在新鮮度窗內（30 分鐘前，符合 BTC 正常出塊
    節奏）→ 正常出 Document，不受新窗口誤殺。"""
    from trustforge.ingestion import onchain
    fixed_now = _dt.datetime(2026, 7, 3, 9, 30, 0, tzinfo=_dt.timezone.utc)
    recent_bbt = (fixed_now - _dt.timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: _blockchair_payload_with_best_block_time(recent_bbt))
    monkeypatch.setattr(onchain.time, "time", lambda: fixed_now.timestamp())
    docs = onchain.BlockchairStatsSource().fetch("", coin="BTC")
    assert len(docs) == 1
    expected_ts = (fixed_now - _dt.timedelta(minutes=30)).timestamp()
    assert docs[0].ts == expected_ts


# ── codex HIGH 修復（#24+robustness，PR #55）：無效 payload 必須拋例外，
#    絕不能靜靜用 "N/A"/空 dict 補位發布假證據覆蓋舊快取 ──────────────────

def test_mempool_space_fees_missing_field_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = b'{"fastestFee": 12, "halfHourFee": 8, "hourFee": 6, "economyFee": 3}'  # 缺 minimumFee
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.MempoolSpaceFeesSource().fetch("", coin="BTC")


def test_mempool_space_fees_wrong_type_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = b'{"fastestFee": "N/A", "halfHourFee": 8, "hourFee": 6, "economyFee": 3, "minimumFee": 1}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.MempoolSpaceFeesSource().fetch("", coin="BTC")


def test_mempool_space_fees_null_field_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = b'{"fastestFee": null, "halfHourFee": 8, "hourFee": 6, "economyFee": 3, "minimumFee": 1}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.MempoolSpaceFeesSource().fetch("", coin="BTC")


def test_mempool_space_fees_non_object_response_raises(monkeypatch):
    from trustforge.ingestion import onchain
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: b"[1, 2, 3]")
    with pytest.raises(ValueError):
        onchain.MempoolSpaceFeesSource().fetch("", coin="BTC")


def test_mempool_space_difficulty_missing_field_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = b'{"progressPercent": 44.39, "difficultyChange": -1.43}'  # 缺 remainingBlocks
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.MempoolSpaceDifficultySource().fetch("", coin="BTC")


def test_mempool_space_difficulty_wrong_type_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = b'{"progressPercent": "N/A", "difficultyChange": -1.43, "remainingBlocks": 1121}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.MempoolSpaceDifficultySource().fetch("", coin="BTC")


def test_blockchair_error_envelope_raises(monkeypatch):
    """Blockchair 限流/欠費會回非 200 的 `context.code`（如 402/429），
    必須拋例外，不得用當下已無效的 `data` 產生 Document。"""
    from trustforge.ingestion import onchain
    for code in (402, 429):
        bad = json.dumps({"context": {"code": code, "error": "rate limited"}}).encode()
        monkeypatch.setattr(onchain, "_fetch_url", lambda url, bad=bad: bad)
        with pytest.raises(ValueError):
            onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_missing_context_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = b'{"data": {"blocks": 1, "difficulty": 2, "mempool_transactions": 3, "transactions_24h": 4}}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_null_data_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = b'{"data": null, "context": {"code": 200}}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_data_wrong_shape_raises(monkeypatch):
    """codex 原始複現案例：`data` 被回成 list 而非 object。"""
    from trustforge.ingestion import onchain
    bad = b'{"data": [{"value": "38"}], "context": {"code": 200}}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_missing_required_field_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = b'{"data": {"blocks": 956480, "difficulty": 133869853540305.4}, "context": {"code": 200}}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_wrong_type_field_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = (
        b'{"data": {"blocks": "N/A", "difficulty": 2, "mempool_transactions": 3, '
        b'"transactions_24h": 4}, "context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


# ── codex MEDIUM 第 8 輪（PR #55，鏈上驗證最終閉合）：型別合法但語意上
# 不可能的數值（負手續費、負區塊數、進度超 0–100%、sentinel -1）不能被
# 當真資料發布 ─────────────────────────────────────────────────────────

def test_mempool_space_fees_negative_fee_raises(monkeypatch):
    """手續費（sat/vB）語意上不可能為負——sentinel -1 或限流異常回應。"""
    from trustforge.ingestion import onchain
    bad = b'{"fastestFee": -1, "halfHourFee": 8, "hourFee": 6, "economyFee": 3, "minimumFee": 1}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.MempoolSpaceFeesSource().fetch("", coin="BTC")


def test_mempool_space_fees_negative_minimum_fee_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = b'{"fastestFee": 12, "halfHourFee": 8, "hourFee": 6, "economyFee": 3, "minimumFee": -1}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.MempoolSpaceFeesSource().fetch("", coin="BTC")


def test_mempool_space_fees_zero_fee_is_valid(monkeypatch):
    """0 是合法邊界值（極端低擁塞時 economy/minimum 費率可能是 0），不該
    被誤擋。"""
    from trustforge.ingestion import onchain
    ok = b'{"fastestFee": 1, "halfHourFee": 1, "hourFee": 0, "economyFee": 0, "minimumFee": 0}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: ok)
    docs = onchain.MempoolSpaceFeesSource().fetch("", coin="BTC")
    assert len(docs) == 1


def test_mempool_space_difficulty_progress_over_100_raises(monkeypatch):
    """進度百分比語意上只能在 0–100 之間，超過 100 是不可能的鏈上狀態。"""
    from trustforge.ingestion import onchain
    bad = b'{"progressPercent": 150, "difficultyChange": -1.43, "remainingBlocks": 1121}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.MempoolSpaceDifficultySource().fetch("", coin="BTC")


def test_mempool_space_difficulty_negative_progress_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = b'{"progressPercent": -5, "difficultyChange": -1.43, "remainingBlocks": 1121}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.MempoolSpaceDifficultySource().fetch("", coin="BTC")


def test_mempool_space_difficulty_negative_remaining_blocks_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = b'{"progressPercent": 44.39, "difficultyChange": -1.43, "remainingBlocks": -1}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    with pytest.raises(ValueError):
        onchain.MempoolSpaceDifficultySource().fetch("", coin="BTC")


def test_mempool_space_difficulty_negative_change_is_valid(monkeypatch):
    """difficultyChange 可正可負（難度下修是合法鏈上事件），不該被範圍
    檢查誤擋。"""
    from trustforge.ingestion import onchain
    ok = b'{"progressPercent": 10, "difficultyChange": -12.5, "remainingBlocks": 2000}'
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: ok)
    docs = onchain.MempoolSpaceDifficultySource().fetch("", coin="BTC")
    assert len(docs) == 1


def test_mempool_space_difficulty_boundary_0_and_100_are_valid(monkeypatch):
    """0% 與 100% 是合法邊界值（含邊界），不該被誤擋。"""
    from trustforge.ingestion import onchain
    for progress in (0, 100):
        ok = json.dumps({
            "progressPercent": progress, "difficultyChange": 0, "remainingBlocks": 0,
        }).encode()
        monkeypatch.setattr(onchain, "_fetch_url", lambda url, ok=ok: ok)
        docs = onchain.MempoolSpaceDifficultySource().fetch("", coin="BTC")
        assert len(docs) == 1


def test_blockchair_zero_blocks_raises(monkeypatch):
    """區塊高度是 sentinel/不可能狀態的 0，必須擋下（真實 BTC 區塊高度
    永遠 > 0）。"""
    from trustforge.ingestion import onchain
    bad = (
        b'{"data": {"blocks": 0, "difficulty": 133869853540305.4, '
        b'"mempool_transactions": 82785, "transactions_24h": 573582, '
        b'"best_block_time": "2026-07-03 09:18:09"}, "context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    monkeypatch.setattr(onchain.time, "time", lambda: _BLOCKCHAIR_FIXED_NOW)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_negative_blocks_sentinel_raises(monkeypatch):
    """sentinel -1 型別合法但語意上不可能，必須擋下。"""
    from trustforge.ingestion import onchain
    bad = (
        b'{"data": {"blocks": -1, "difficulty": 133869853540305.4, '
        b'"mempool_transactions": 82785, "transactions_24h": 573582, '
        b'"best_block_time": "2026-07-03 09:18:09"}, "context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    monkeypatch.setattr(onchain.time, "time", lambda: _BLOCKCHAIR_FIXED_NOW)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_zero_difficulty_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = (
        b'{"data": {"blocks": 956480, "difficulty": 0, '
        b'"mempool_transactions": 82785, "transactions_24h": 573582, '
        b'"best_block_time": "2026-07-03 09:18:09"}, "context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    monkeypatch.setattr(onchain.time, "time", lambda: _BLOCKCHAIR_FIXED_NOW)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_negative_mempool_transactions_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = (
        b'{"data": {"blocks": 956480, "difficulty": 133869853540305.4, '
        b'"mempool_transactions": -1, "transactions_24h": 573582, '
        b'"best_block_time": "2026-07-03 09:18:09"}, "context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    monkeypatch.setattr(onchain.time, "time", lambda: _BLOCKCHAIR_FIXED_NOW)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_negative_transactions_24h_raises(monkeypatch):
    from trustforge.ingestion import onchain
    bad = (
        b'{"data": {"blocks": 956480, "difficulty": 133869853540305.4, '
        b'"mempool_transactions": 82785, "transactions_24h": -1, '
        b'"best_block_time": "2026-07-03 09:18:09"}, "context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad)
    monkeypatch.setattr(onchain.time, "time", lambda: _BLOCKCHAIR_FIXED_NOW)
    with pytest.raises(ValueError):
        onchain.BlockchairStatsSource().fetch("", coin="BTC")


def test_blockchair_zero_mempool_transactions_is_valid(monkeypatch):
    """mempool_transactions == 0 是合法狀態（mempool 剛好清空），不該被
    誤擋——只有負值才是不可能的。"""
    from trustforge.ingestion import onchain
    ok = (
        b'{"data": {"blocks": 956480, "difficulty": 133869853540305.4, '
        b'"mempool_transactions": 0, "transactions_24h": 0, '
        b'"best_block_time": "2026-07-03 09:18:09"}, "context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: ok)
    monkeypatch.setattr(onchain.time, "time", lambda: _BLOCKCHAIR_FIXED_NOW)
    docs = onchain.BlockchairStatsSource().fetch("", coin="BTC")
    assert len(docs) == 1


def test_build_onchain_sources_includes_batch2_sources(monkeypatch):
    """build_onchain_sources() 含新舊共 5 個鏈上來源（2 變 5）。"""
    from trustforge.ingestion import onchain
    sources = onchain.build_onchain_sources()
    names = {s.name for s in sources}
    assert names == {
        "alternative-me-fng", "blockchain-info",
        "mempool-space-fees", "mempool-space-difficulty", "blockchair",
    }


def test_mempool_space_and_blockchair_source_failure_does_not_crash_collect(monkeypatch):
    from urllib.error import URLError
    from trustforge.ingestion import onchain, base
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: (_ for _ in ()).throw(URLError("timeout")))
    for src in (onchain.MempoolSpaceFeesSource(), onchain.MempoolSpaceDifficultySource(), onchain.BlockchairStatsSource()):
        docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
        assert isinstance(docs, list)


# ── collect 整合測試 ──────────────────────────────────────────────────────────

def test_collect_online_produces_news_and_onchain(monkeypatch, tmp_path):
    """collect offline=False + sources=None 應同時產出 news 與 onchain 文件。

    階段2（cache + 排程 fetcher）後，collect() 的線上預設路徑改成一律經
    `CachedSource` 讀快取，不再直接呼叫真 source 的 `fetch()`（見
    `ingestion/cache.py`）。這裡比照 `scripts/fetch_scheduler.py` 的寫入方式，
    先用（monkeypatch 過 `_fetch_url` 的）真 source 各自 fetch 一次寫入
    測試用 cache backend，驗證 collect() 端到端仍能正確讀出 news/onchain
    文件——CachedSource 本身的命中/降級邏輯已在 test_connector_cache.py
    完整覆蓋，這裡只驗證 build_news_sources()/build_onchain_sources() 有
    正確被接進 collect() 的預設在線流程。
    """
    import time
    from trustforge.ingestion import news, onchain, base
    from trustforge.ingestion import cache as cache_mod
    from trustforge.ingestion.news import build_news_sources
    from trustforge.ingestion.onchain import build_onchain_sources

    monkeypatch.setattr(news, "_fetch_url", lambda url: RSS_FIXTURE)
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: FNG_FIXTURE)
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)

    backend = cache_mod.JsonCacheBackend(tmp_path / "cache.json")
    monkeypatch.setattr(cache_mod, "get_cache_backend", lambda: backend)
    for src in build_news_sources() + build_onchain_sources():
        # 比照 scripts/fetch_scheduler.py 真實行為：單一來源 fetch() 失敗
        # （這裡是刻意的，FNG_FIXTURE 對 mempool-space-*/blockchair 三個
        # 新源來說是缺欄位的無效 payload，嚴格驗證會 raise，見 onchain.py）
        # 只跳過該來源、不中斷其他來源，也不寫入 cache——不是這個測試要
        # 驗的東西（各源自己的解析/驗證邏輯在各自的單元測試已覆蓋，這裡
        # 只驗 collect() 有沒有正確接上 news/onchain 的線上快取路徑）。
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
    assert "news" in kinds, f"缺 news，got kinds={kinds}"
    assert "onchain" in kinds, f"缺 onchain，got kinds={kinds}"


def test_collect_online_cache_miss_degrades_gracefully_not_real_call(monkeypatch, tmp_path):
    """未預先寫入 cache 時，collect() 線上路徑不應反過來呼叫真 source.fetch()
    （這裡故意讓 `_fetch_url` 一被呼叫就炸，藉此證明它完全沒被呼叫），
    而是優雅降級（docs 為空，來源名進 `_failed`），不崩潰。"""
    from trustforge.ingestion import news, onchain, base

    def _boom(url):  # pragma: no cover - 不應被呼叫到
        raise AssertionError(f"CachedSource 不該打真連接器 API：{url}")

    monkeypatch.setattr(news, "_fetch_url", _boom)
    monkeypatch.setattr(onchain, "_fetch_url", _boom)
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)
    # 隔離快取路徑：確保這次一定是全新、空的 cache（不受開發者本機
    # out/connector_cache 既有內容或其他測試殘留影響）。
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path / "cache"))

    failed: list = []
    docs = base.collect("BTC", coin="BTC", offline=False, _failed=failed)
    # 只剩 price（OHLCV 官方基準資料，跟連接器快取無關）；news/onchain 因
    # cache-miss 優雅降級，完全沒有觸發真呼叫。
    kinds = {d.kind for d in docs}
    assert "news" not in kinds and "onchain" not in kinds, f"不應含 news/onchain：{kinds}"
    assert "coindesk" in failed
    assert "alternative-me-fng" in failed


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
