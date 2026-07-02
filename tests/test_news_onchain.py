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


def test_coindesk_url_has_no_trailing_slash(monkeypatch):
    """生產事故修復：`.../rss/`（帶斜線）已被 CoinDesk 永久重導（308）到
    `.../rss`（無斜線），寫死的白名單 URL 須直接用無斜線版本，不依賴跟轉址
    才能拿到內容。"""
    from trustforge.ingestion import news
    assert news.CoinDeskRSSSource._URL == "https://www.coindesk.com/arc/outboundfeeds/rss"


# ── `_fetch_url` 轉址安全測試（安全事故修復：見 news.py 模組頂部說明）───────
#
# `urlopen()` 預設 `HTTPRedirectHandler` 對 3xx 一律自動跟轉、完全不檢查
# 目的地 host/scheme/是否私有 IP——第一版「跟 308」防禦寫在 `except
# HTTPError` 裡，在自動跟轉的 Python 版本上根本不會被執行，等於形同虛設。
# 修法：自訂 `_NoRedirectHandler` 完全禁用自動跟轉，`_fetch_url` 改成手動
# 逐跳驗證。以下測試一律 mock `news._opener.open`（而非已被移除的
# `news.urlopen`）＋ `news.socket.getaddrinfo`（避免測試真的打 DNS 查詢，
# credit-safe/no real network）。

def _fake_getaddrinfo_returning(*ips: str):
    """回傳一個 `socket.getaddrinfo` 替身：不管查哪個 hostname，都回傳給定
    的 IP 清單（用最小可用的 tuple 結構，只有 `_is_safe_redirect_target`
    實際會讀的 `info[4][0]` 這個位置需要正確）。"""
    def _fake(host, port, *args, **kwargs):
        return [(None, None, None, None, (ip, 0)) for ip in ips]
    return _fake


def test_fetch_url_follows_308_same_host_https(monkeypatch):
    """`_fetch_url` 對 308 Permanent Redirect（urllib < 3.11 不會自動跟）
    手動跟一次：跳轉目標同 host + https + 解析出的 IP 非私有時，改打新
    URL 並回傳其內容。"""
    from urllib.error import HTTPError
    from trustforge.ingestion import news

    calls: list[str] = []

    def _fake_open(req, timeout=None):
        url = req.full_url
        calls.append(url)
        if url == "https://www.coindesk.com/arc/outboundfeeds/rss":
            raise HTTPError(
                url, 308, "Permanent Redirect",
                {"Location": "/arc/outboundfeeds/rss-new"}, None,
            )
        assert url == "https://www.coindesk.com/arc/outboundfeeds/rss-new"
        return _FakeResp(RSS_FIXTURE)

    monkeypatch.setattr(news._opener, "open", _fake_open)
    monkeypatch.setattr(news.socket, "getaddrinfo", _fake_getaddrinfo_returning("104.16.1.1"))
    raw = news._fetch_url("https://www.coindesk.com/arc/outboundfeeds/rss")
    assert raw == RSS_FIXTURE
    assert calls == [
        "https://www.coindesk.com/arc/outboundfeeds/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss-new",
    ]


def test_fetch_url_308_cross_host_redirect_not_followed(monkeypatch):
    """308 跳轉目標若不是同 host（如被劫持/CDN 誤配置跳到其他網域），不得
    跟轉址（避免任意網域跳轉造成 SSRF），原樣拋出 HTTPError。"""
    from urllib.error import HTTPError
    from trustforge.ingestion import news

    def _fake_open(req, timeout=None):
        raise HTTPError(
            req.full_url, 308, "Permanent Redirect",
            {"Location": "https://evil.example.com/steal"}, None,
        )

    monkeypatch.setattr(news._opener, "open", _fake_open)
    with pytest.raises(HTTPError):
        news._fetch_url("https://www.coindesk.com/arc/outboundfeeds/rss")


def test_fetch_url_second_hop_cross_host_redirect_not_followed(monkeypatch):
    """SSRF 回歸鎖：第一跳同 host（通過驗證、正常跟轉），但第一跳的目的地
    緊接著又把請求轉到「不同 host」——整條轉址鏈只要有任一跳的目的地不是
    最初白名單 URL 的 host，就必須拒絕，不能只比對「上一跳」而放行第二跳
    才變更 host 的繞過手法。"""
    from urllib.error import HTTPError
    from trustforge.ingestion import news

    calls: list[str] = []

    def _fake_open(req, timeout=None):
        url = req.full_url
        calls.append(url)
        if url == "https://www.coindesk.com/arc/outboundfeeds/rss":
            raise HTTPError(
                url, 308, "Permanent Redirect",
                {"Location": "https://www.coindesk.com/arc/outboundfeeds/rss-hop2"}, None,
            )
        if url == "https://www.coindesk.com/arc/outboundfeeds/rss-hop2":
            raise HTTPError(
                url, 302, "Found",
                {"Location": "https://evil.example.com/steal"}, None,
            )
        raise AssertionError(f"不該打到 {url}")

    monkeypatch.setattr(news._opener, "open", _fake_open)
    monkeypatch.setattr(news.socket, "getaddrinfo", _fake_getaddrinfo_returning("104.16.1.1"))
    with pytest.raises(HTTPError):
        news._fetch_url("https://www.coindesk.com/arc/outboundfeeds/rss")
    # 第一跳確實嘗試過（同 host，通過驗證），第二跳的跨 host 目標沒有被打
    assert calls == [
        "https://www.coindesk.com/arc/outboundfeeds/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss-hop2",
    ]


@pytest.mark.parametrize("private_ip", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1"])
def test_fetch_url_redirect_to_private_ip_not_followed(monkeypatch, private_ip):
    """轉址目標即使 host 字串與白名單同名，但解析出的 IP 是私有/迴環/
    link-local（含雲端 metadata endpoint 169.254.169.254）時，一律拒絕
    跟轉（防禦 DNS rebinding 把同一個網域名稱解析到內網位址的攻擊手法）。"""
    from urllib.error import HTTPError
    from trustforge.ingestion import news

    def _fake_open(req, timeout=None):
        raise HTTPError(
            req.full_url, 308, "Permanent Redirect",
            {"Location": "/arc/outboundfeeds/rss-new"}, None,
        )

    monkeypatch.setattr(news._opener, "open", _fake_open)
    monkeypatch.setattr(news.socket, "getaddrinfo", _fake_getaddrinfo_returning(private_ip))
    with pytest.raises(HTTPError):
        news._fetch_url("https://www.coindesk.com/arc/outboundfeeds/rss")


def test_fetch_url_redirect_non_standard_port_not_followed(monkeypatch):
    """轉址目標 port 不是 https 預設 443 時拒絕跟轉（避免被導去內網服務常
    用的非標準 port）。"""
    from urllib.error import HTTPError
    from trustforge.ingestion import news

    def _fake_open(req, timeout=None):
        raise HTTPError(
            req.full_url, 308, "Permanent Redirect",
            {"Location": "https://www.coindesk.com:8443/arc/outboundfeeds/rss-new"}, None,
        )

    monkeypatch.setattr(news._opener, "open", _fake_open)
    monkeypatch.setattr(news.socket, "getaddrinfo", _fake_getaddrinfo_returning("104.16.1.1"))
    with pytest.raises(HTTPError):
        news._fetch_url("https://www.coindesk.com/arc/outboundfeeds/rss")


def test_fetch_url_exceeds_max_redirects_rejected(monkeypatch):
    """轉址鏈超過 `_MAX_REDIRECTS` 跳（每一跳本身驗證都合法、同 host 安全
    IP）仍要被拒絕，避免無限轉址鏈耗盡資源。"""
    from urllib.error import HTTPError
    from trustforge.ingestion import news

    calls: list[str] = []

    def _fake_open(req, timeout=None):
        url = req.full_url
        calls.append(url)
        n = len(calls)
        raise HTTPError(
            url, 308, "Permanent Redirect",
            {"Location": f"/arc/outboundfeeds/rss-hop{n}"}, None,
        )

    monkeypatch.setattr(news._opener, "open", _fake_open)
    monkeypatch.setattr(news.socket, "getaddrinfo", _fake_getaddrinfo_returning("104.16.1.1"))
    with pytest.raises(HTTPError):
        news._fetch_url("https://www.coindesk.com/arc/outboundfeeds/rss")
    # 最初一次 + 最多 _MAX_REDIRECTS 跳 = 上限，不會無止盡跟下去
    assert len(calls) == news._MAX_REDIRECTS + 1


def test_fetch_url_non_redirect_http_error_propagates(monkeypatch):
    """非轉址類 HTTPError（如 429/500）不受這層跟轉址邏輯影響，原樣拋出，
    也不會嘗試呼叫 DNS 解析（不是轉址，不需要驗證目的地）。"""
    from urllib.error import HTTPError
    from trustforge.ingestion import news

    def _fake_open(req, timeout=None):
        raise HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    def _boom_getaddrinfo(*args, **kwargs):
        raise AssertionError("非轉址錯誤不該觸發 DNS 解析")

    monkeypatch.setattr(news._opener, "open", _fake_open)
    monkeypatch.setattr(news.socket, "getaddrinfo", _boom_getaddrinfo)
    with pytest.raises(HTTPError):
        news._fetch_url("https://www.coindesk.com/arc/outboundfeeds/rss")


@pytest.mark.parametrize("code", [301, 302, 303, 307])
def test_fetch_url_legacy_redirect_codes_also_require_manual_validation(monkeypatch, code):
    """安全回歸鎖：301/302/303/307（這些狀態碼在所有 Python 版本都會被
    `urlopen()` 預設 handler 自動跟轉、不分 Python 版本、從一開始就存在的
    洞——不是只有 308 才有問題）現在也必須走同一套手動逐跳驗證，跨 host
    目標一律拒絕，不能被預設 handler 偷偷跟走。"""
    from urllib.error import HTTPError
    from trustforge.ingestion import news

    def _fake_open(req, timeout=None):
        raise HTTPError(
            req.full_url, code, "Redirect",
            {"Location": "https://evil.example.com/steal"}, None,
        )

    monkeypatch.setattr(news._opener, "open", _fake_open)
    with pytest.raises(HTTPError):
        news._fetch_url("https://www.coindesk.com/arc/outboundfeeds/rss")


def test_no_redirect_handler_disables_automatic_redirect():
    """`_NoRedirectHandler.redirect_request` 恆回傳 `None`：直接單元測試
    這個 handler 本身確實會讓 urllib 視為「不處理」轉址（進而對呼叫端拋出
    HTTPError，而不是偷偷自動跟走），是本檔唯一安裝的 opener 行為的核心
    保證。"""
    from trustforge.ingestion import news
    handler = news._NoRedirectHandler()
    result = handler.redirect_request(
        req=None, fp=None, code=308, msg="Permanent Redirect",
        headers=None, newurl="https://evil.example.com/steal",
    )
    assert result is None


class _FakeResp:
    """`urlopen()` 回傳值的最小可用替身（context manager + read）。"""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self, _n: int = -1) -> bytes:
        return self._body


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
        raw_docs = src.fetch("BTC", coin="BTC")
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
