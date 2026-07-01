"""W-coingecko：CoinGecko 真實加密資料源整合測試 — CI 不打真網路（monkeypatch _fetch_url）。"""
from __future__ import annotations

import json

import pytest

# ── 本地固定 fixture ──────────────────────────────────────────────────────────

PRICE_FIXTURE = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000, "usd_24h_change": 2.34},
    "ethereum": {"usd": 3521.10, "usd_market_cap": 420_000_000_000, "usd_24h_change": -1.12},
    "solana": {"usd": 172.5, "usd_market_cap": 78_000_000_000, "usd_24h_change": 5.67},
    "binancecoin": {"usd": 610.2, "usd_market_cap": 88_000_000_000, "usd_24h_change": 0.05},
    "ripple": {"usd": 0.62, "usd_market_cap": 34_000_000_000, "usd_24h_change": -0.98},
}).encode()

PRICE_FIXTURE_MISSING_FIELDS = json.dumps({
    "bitcoin": {"usd": 67823.45},  # 缺 usd_24h_change / usd_market_cap
}).encode()

PRICE_FIXTURE_NULL_FIELDS = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": None, "usd_24h_change": None},
}).encode()

DETAIL_FIXTURE_NORMAL = json.dumps({
    "sentiment_votes_up_percentage": 72.5,
    "sentiment_votes_down_percentage": 27.5,
    "developer_data": {"stars": 12345, "forks": 6789, "commit_count_4_weeks": 42},
    "community_data": None,
}).encode()

DETAIL_FIXTURE_MISSING_FIELDS = json.dumps({
    # 完全沒有 sentiment_votes_* / developer_data 欄位
}).encode()

DETAIL_FIXTURE_NULL_FIELDS = json.dumps({
    "sentiment_votes_up_percentage": None,
    "sentiment_votes_down_percentage": None,
    "developer_data": {"stars": None, "forks": None, "commit_count_4_weeks": None},
}).encode()


# ── CoinGeckoPriceSource ──────────────────────────────────────────────────────

def test_price_source_document_fields(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: PRICE_FIXTURE)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    d = docs[0]
    assert d.kind == "price_live"
    assert d.source == "coingecko-price"
    assert "coingecko.com" in d.url
    assert d.ts > 0
    assert d.meta["coin"] == "BTC"
    ref = d.meta["content_reference"]
    assert "67823.45" in ref
    assert "+2.34%" in ref


def test_price_source_non_target_coin_skipped(monkeypatch):
    """非 5 幣白名單的幣種一律跳過。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: PRICE_FIXTURE)
    assert coingecko.CoinGeckoPriceSource().fetch("", coin="DOGE") == []


def test_price_source_empty_coin_returns_all_five(monkeypatch):
    """coin='' 時（全市場通用查詢）回傳 5 幣各一筆，皆帶顯式 meta['coin']。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: PRICE_FIXTURE)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="")
    assert len(docs) == 5
    coins = {d.meta["coin"] for d in docs}
    assert coins == {"BTC", "ETH", "SOL", "BNB", "XRP"}


def test_price_source_missing_fields_degrades_to_na(monkeypatch):
    """缺 24h 變動 / 市值欄位時不炸，改顯示 N/A。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: PRICE_FIXTURE_MISSING_FIELDS)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    ref = docs[0].meta["content_reference"]
    assert "N/A" in ref


def test_price_source_null_fields_degrades_to_na(monkeypatch):
    """欄位為 null（非缺欄）時同樣不炸，改顯示 N/A。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: PRICE_FIXTURE_NULL_FIELDS)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    ref = docs[0].meta["content_reference"]
    assert "N/A" in ref


def test_price_source_missing_price_entirely_skips_coin(monkeypatch):
    """整個幣別在回應中缺席（如 API 部分降級）時該幣被跳過，不炸。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: b'{"bitcoin": {"usd": 100.0}}')
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="ETH")
    assert docs == []


def test_price_source_failure_does_not_crash_collect(monkeypatch):
    """連接器逾時/例外 → collect 跳過該來源，不拋例外。"""
    from urllib.error import URLError
    from trustforge.ingestion import coingecko, base
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: (_ for _ in ()).throw(URLError("timeout")))
    src = coingecko.CoinGeckoPriceSource()
    docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)


# ── CoinGeckoSentimentSource ──────────────────────────────────────────────────

def test_sentiment_source_document_fields(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: DETAIL_FIXTURE_NORMAL)
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="ETH")
    assert len(docs) == 1
    d = docs[0]
    assert d.kind == "sentiment"
    assert d.source == "coingecko-sentiment"
    assert "coingecko.com" in d.url
    assert d.meta["coin"] == "ETH"
    ref = d.meta["content_reference"]
    assert "72.5%" in ref
    assert "27.5%" in ref


def test_sentiment_source_non_target_coin_skipped(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: DETAIL_FIXTURE_NORMAL)
    assert coingecko.CoinGeckoSentimentSource().fetch("", coin="DOGE") == []


def test_sentiment_source_empty_coin_skipped(monkeypatch):
    """空 coin 無法決定要打哪個 id 的端點，直接跳過。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: DETAIL_FIXTURE_NORMAL)
    assert coingecko.CoinGeckoSentimentSource().fetch("", coin="") == []


def test_sentiment_source_missing_fields_returns_empty(monkeypatch):
    """欄位整個缺席（community_data-only 回應等）時安靜回傳 []，不炸。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: DETAIL_FIXTURE_MISSING_FIELDS)
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    assert docs == []


def test_sentiment_source_null_fields_returns_empty(monkeypatch):
    """欄位存在但值為 null（免費 tier 常見）時安靜回傳 []，不炸。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: DETAIL_FIXTURE_NULL_FIELDS)
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    assert docs == []


def test_sentiment_source_failure_does_not_crash_collect(monkeypatch):
    from urllib.error import URLError
    from trustforge.ingestion import coingecko, base
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: (_ for _ in ()).throw(URLError("timeout")))
    src = coingecko.CoinGeckoSentimentSource()
    docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)


# ── CoinGeckoDevSource ────────────────────────────────────────────────────────

def test_dev_source_document_fields(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: DETAIL_FIXTURE_NORMAL)
    docs = coingecko.CoinGeckoDevSource().fetch("", coin="SOL")
    assert len(docs) == 1
    d = docs[0]
    assert d.kind == "dev_activity"
    assert d.source == "coingecko-dev"
    assert d.meta["coin"] == "SOL"
    ref = d.meta["content_reference"]
    assert "12345" in ref
    assert "6789" in ref
    assert "42" in ref


def test_dev_source_non_target_coin_skipped(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: DETAIL_FIXTURE_NORMAL)
    assert coingecko.CoinGeckoDevSource().fetch("", coin="DOGE") == []


def test_dev_source_empty_coin_skipped(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: DETAIL_FIXTURE_NORMAL)
    assert coingecko.CoinGeckoDevSource().fetch("", coin="") == []


def test_dev_source_missing_fields_returns_empty(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: DETAIL_FIXTURE_MISSING_FIELDS)
    docs = coingecko.CoinGeckoDevSource().fetch("", coin="BTC")
    assert docs == []


def test_dev_source_null_fields_returns_empty(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: DETAIL_FIXTURE_NULL_FIELDS)
    docs = coingecko.CoinGeckoDevSource().fetch("", coin="BTC")
    assert docs == []


def test_dev_source_failure_does_not_crash_collect(monkeypatch):
    from urllib.error import URLError
    from trustforge.ingestion import coingecko, base
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: (_ for _ in ()).throw(URLError("timeout")))
    src = coingecko.CoinGeckoDevSource()
    docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)


# ── build_coingecko_sources ───────────────────────────────────────────────────

def test_build_coingecko_sources_returns_three():
    from trustforge.ingestion.coingecko import build_coingecko_sources
    sources = build_coingecko_sources()
    assert len(sources) == 3
    names = {s.name for s in sources}
    assert names == {"coingecko-price", "coingecko-sentiment", "coingecko-dev"}
    kinds = {s.kind for s in sources}
    assert kinds == {"price_live", "sentiment", "dev_activity"}


# ── collect() 接線測試（沿用 test_news_onchain.py 的 CachedSource 寫入慣例）──

def test_collect_online_includes_coingecko_kinds(monkeypatch, tmp_path):
    """collect(offline=False, sources=None) 應把 build_coingecko_sources() 併入
    raw_sources，經 CachedSource 讀出後 price_live/sentiment/dev_activity 三種
    kind 皆能出現在 collect() 結果中。"""
    import time
    from trustforge.ingestion import coingecko, base
    from trustforge.ingestion import cache as cache_mod
    from trustforge.ingestion.coingecko import build_coingecko_sources

    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: PRICE_FIXTURE)

    backend = cache_mod.JsonCacheBackend(tmp_path / "cache.json")
    monkeypatch.setattr(cache_mod, "get_cache_backend", lambda: backend)

    # 逐一寫入三個來源的快取（sentiment/dev 用各自 fixture，避免共用
    # PRICE_FIXTURE 造成 sentiment/dev 因欄位缺席回傳空文件）。
    price_src, sentiment_src, dev_src = build_coingecko_sources()
    backend.set(
        cache_mod.cache_key(price_src.name, "BTC"),
        [cache_mod.doc_to_dict(d) for d in price_src.fetch("BTC", coin="BTC")],
        fetched_at=time.time(),
    )
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url: DETAIL_FIXTURE_NORMAL)
    backend.set(
        cache_mod.cache_key(sentiment_src.name, "BTC"),
        [cache_mod.doc_to_dict(d) for d in sentiment_src.fetch("BTC", coin="BTC")],
        fetched_at=time.time(),
    )
    backend.set(
        cache_mod.cache_key(dev_src.name, "BTC"),
        [cache_mod.doc_to_dict(d) for d in dev_src.fetch("BTC", coin="BTC")],
        fetched_at=time.time(),
    )

    docs = base.collect("BTC", coin="BTC", offline=False)
    kinds = {d.kind for d in docs}
    assert "price_live" in kinds, f"缺 price_live，got kinds={kinds}"
    assert "sentiment" in kinds, f"缺 sentiment，got kinds={kinds}"
    assert "dev_activity" in kinds, f"缺 dev_activity，got kinds={kinds}"


def test_collect_online_cache_miss_coingecko_degrades_gracefully(monkeypatch, tmp_path):
    """未預先寫入 cache 時，CoinGecko 來源不應反過來呼叫真 _fetch_url，而是
    優雅降級（來源名進 _failed），不崩潰、不觸發真連線。"""
    from trustforge.ingestion import coingecko, base

    def _boom(url):  # pragma: no cover - 不應被呼叫到
        raise AssertionError(f"CachedSource 不該打真連接器 API：{url}")

    monkeypatch.setattr(coingecko, "_fetch_url", _boom)
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path / "cache"))

    failed: list = []
    docs = base.collect("BTC", coin="BTC", offline=False, _failed=failed)
    kinds = {d.kind for d in docs}
    assert not ({"price_live", "sentiment", "dev_activity"} & kinds), f"不應含 coingecko kinds：{kinds}"
    assert "coingecko-price" in failed
    assert "coingecko-sentiment" in failed
    assert "coingecko-dev" in failed


def test_collect_offline_unaffected_by_coingecko():
    """offline=True 路徑不受新連接器影響，仍用 sample json（無 price_live/sentiment/dev_activity）。"""
    from trustforge.ingestion import base
    docs = base.collect("BTC", coin="BTC", offline=True)
    kinds = {d.kind for d in docs}
    assert "price_live" not in kinds
    assert "dev_activity" not in kinds


# ── cache.py 排程間隔設定 ──────────────────────────────────────────────────────

def test_coingecko_refresh_intervals_registered():
    from trustforge.ingestion.cache import DEFAULT_REFRESH_INTERVAL_SECONDS, DEFAULT_STALE_AFTER_SECONDS
    assert DEFAULT_REFRESH_INTERVAL_SECONDS["coingecko-price"] == 10 * 60
    assert DEFAULT_REFRESH_INTERVAL_SECONDS["coingecko-sentiment"] == 30 * 60
    assert DEFAULT_REFRESH_INTERVAL_SECONDS["coingecko-dev"] == 60 * 60
    # 硬過期時限應為 refresh 間隔的 STALE_AFTER_MULTIPLIER 倍（衍生值，非獨立手填）
    for name in ("coingecko-price", "coingecko-sentiment", "coingecko-dev"):
        assert DEFAULT_STALE_AFTER_SECONDS[name] == DEFAULT_REFRESH_INTERVAL_SECONDS[name] * 3


# ── fetch_scheduler.py 接線測試 ────────────────────────────────────────────────

def test_fetch_scheduler_registry_includes_coingecko():
    from scripts.fetch_scheduler import build_registry
    registry = build_registry()
    assert "coingecko-price" in registry
    assert "coingecko-sentiment" in registry
    assert "coingecko-dev" in registry


# ── trust/scoring.py KIND_REPUTATION 三項 + 既有 6 項不動 ─────────────────────

def test_kind_reputation_existing_six_untouched():
    from trustforge.trust.scoring import KIND_REPUTATION
    assert KIND_REPUTATION["price"] == 0.95
    assert KIND_REPUTATION["onchain"] == 0.95
    assert KIND_REPUTATION["regulatory"] == 0.90
    assert KIND_REPUTATION["hoyabit"] == 0.85
    assert KIND_REPUTATION["news"] == 0.65
    assert KIND_REPUTATION["social"] == 0.35


def test_kind_reputation_coingecko_three_added():
    from trustforge.trust.scoring import KIND_REPUTATION
    assert KIND_REPUTATION["price_live"] == 0.90
    assert KIND_REPUTATION["sentiment"] == 0.50
    assert KIND_REPUTATION["dev_activity"] == 0.50


def test_score_and_aggregate_handle_coingecko_kinds_without_crash():
    """score()/aggregate() 對含 price_live/sentiment/dev_activity 的 claims
    正常運作，不因新 kind 出現而崩潰或 reputation 分項異常。"""
    import time
    from trustforge.ingestion.base import Document
    from trustforge.trust.scoring import extract_claims, score, aggregate

    now = time.time()
    docs = [
        Document(id="cg-price-1", kind="price_live", source="coingecko-price",
                 text="BTC 現價 67823.45 USD，24h 變動 +2.34%，市值 1,330,000,000,000 USD。",
                 url="https://api.coingecko.com/api/v3/simple/price", ts=now,
                 meta={"coin": "BTC"}),
        Document(id="cg-sent-1", kind="sentiment", source="coingecko-sentiment",
                 text="BTC 社群情緒投票：看漲 72.5%，看跌 27.5%。",
                 url="https://api.coingecko.com/api/v3/coins/bitcoin", ts=now,
                 meta={"coin": "BTC"}),
        Document(id="cg-dev-1", kind="dev_activity", source="coingecko-dev",
                 text="BTC 開發活動：GitHub stars 12345，forks 6789，近 4 週 commits 42。",
                 url="https://api.coingecko.com/api/v3/coins/bitcoin", ts=now,
                 meta={"coin": "BTC"}),
    ]
    claims = extract_claims(docs)
    scored = score(claims, now=now)
    assert len(scored) == len(claims)
    reps = {sc.claim.doc.kind: sc.components["reputation"] for sc in scored}
    assert reps["price_live"] == pytest.approx(0.90)
    assert reps["sentiment"] == pytest.approx(0.50)
    assert reps["dev_activity"] == pytest.approx(0.50)

    brief = aggregate(scored, query="BTC", coin="BTC")
    assert brief.confidence >= 0.0  # 不崩、可正常聚合


# ── 鮮度並存：HOYA price(歷史) 與 CoinGecko price_live(即時) 各自 ts 共存 ────

def test_hoya_price_and_coingecko_price_live_coexist_with_distinct_ts():
    """HOYA `price`（歷史 K 棒，ts=最後 K 棒收盤日）與 CoinGecko `price_live`
    （ts=API 呼叫當下）同時出現在 claim pool 時，各自保留獨立 ts，時效衰減
    分別計算，互不覆蓋、互不取代（不改資料結構，純粹是兩筆不同 kind/source
    的 Document 並存）。"""
    import time
    from trustforge.ingestion.base import Document
    from trustforge.trust.scoring import extract_claims, score, _recency_decay

    now = time.time()
    hoya_ts = now - 3600 * 30  # 30 小時前的 K 棒收盤（歷史事實）
    live_ts = now - 60         # 1 分鐘前呼叫的即時現價

    docs = [
        Document(id="price-BTC-ret", kind="price", source="ohlcv-csv",
                 text="BTC 近 14 日收盤從 60000 變動至 67823，報酬 +13.0%，呈上漲。",
                 url="", ts=hoya_ts, meta={}),
        Document(id="cg-price-1", kind="price_live", source="coingecko-price",
                 text="BTC 現價 67823.45 USD，24h 變動 +2.34%，市值 N/A USD。",
                 url="https://api.coingecko.com/api/v3/simple/price", ts=live_ts,
                 meta={"coin": "BTC"}),
    ]
    claims = extract_claims(docs)
    assert len(claims) == 2

    # 兩者 ts 完全獨立，不互相覆蓋
    ts_by_kind = {c.doc.kind: c.doc.ts for c in claims}
    assert ts_by_kind["price"] == hoya_ts
    assert ts_by_kind["price_live"] == live_ts
    assert ts_by_kind["price"] != ts_by_kind["price_live"]

    # 時效衰減各自獨立計算：live（1 分鐘前）應遠新鮮於 hoya（30 小時前）
    decay = {c.doc.kind: _recency_decay(c, now) for c in claims}
    assert decay["price_live"] > decay["price"]

    scored = score(claims, now=now)
    assert len(scored) == 2
    # 兩者皆能正常評分、共存於同一個 claim pool，不互相取代
    kinds_scored = {sc.claim.doc.kind for sc in scored}
    assert kinds_scored == {"price", "price_live"}
