"""W-coingecko：CoinGecko 真實加密資料源整合測試 — CI 不打真網路（monkeypatch _fetch_url）。"""
from __future__ import annotations

import json
from pathlib import Path

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

# codex MEDIUM（呼應 #24 不造假）：partial/malformed 情境 fixture，驗證壞資料
# 不會被捏造成強方向（見 test_sentiment_source_* 系列）。json.dumps 對
# float("nan")/float("inf") 預設會輸出非標準的 NaN/Infinity token，
# Python 的 json.loads 預設 allow_nan=True 也能原樣讀回，故可直接用於
# 模擬「API 回傳壞值」情境，不需手刻原始 bytes。
DETAIL_FIXTURE_ONE_SIDED_UP_ONLY = json.dumps({
    "sentiment_votes_up_percentage": 58.6,
    # down 完全缺席（非 null，是欄位不存在）
}).encode()

DETAIL_FIXTURE_OUT_OF_RANGE = json.dumps({
    "sentiment_votes_up_percentage": 150.0,  # 超出 0–100
    "sentiment_votes_down_percentage": 20.0,
}).encode()

DETAIL_FIXTURE_NAN = json.dumps({
    "sentiment_votes_up_percentage": 60.0,
    "sentiment_votes_down_percentage": float("nan"),
}).encode()

DETAIL_FIXTURE_INF = json.dumps({
    "sentiment_votes_up_percentage": float("inf"),
    "sentiment_votes_down_percentage": 20.0,
}).encode()

DETAIL_FIXTURE_NON_NUMERIC = json.dumps({
    "sentiment_votes_up_percentage": "up",
    "sentiment_votes_down_percentage": 40.0,
}).encode()

DETAIL_FIXTURE_EXACT_POSITIVE_BOUNDARY = json.dumps({
    "sentiment_votes_up_percentage": 52.5,
    "sentiment_votes_down_percentage": 47.5,  # diff == +5.0（剛好等於門檻）
}).encode()

DETAIL_FIXTURE_EXACT_NEGATIVE_BOUNDARY = json.dumps({
    "sentiment_votes_up_percentage": 45.0,
    "sentiment_votes_down_percentage": 50.0,  # diff == -5.0（剛好等於門檻）
}).encode()

PRICE_FIXTURE_NAN_CHANGE = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": float("nan")},
}).encode()

PRICE_FIXTURE_INF_CHANGE = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": float("inf")},
}).encode()

PRICE_FIXTURE_NON_NUMERIC_CHANGE = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": "flat"},
}).encode()

# codex MEDIUM #3（收斂整個數值欄位驗證類別）：`usd`/`usd_market_cap`/
# `last_updated_at` 同類壞值 fixture。前兩輪只擋了 `usd_24h_change`，
# `usd` 本身（現價，price_live 存在的唯一理由）只擋了 None，NaN/inf/字串/
# bool/負值/零都會被當成「有效現價」直接寫進 ref、進 OBJECTIVE_KINDS。
PRICE_FIXTURE_NAN_USD = json.dumps({
    "bitcoin": {"usd": float("nan"), "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": 2.34},
}).encode()

PRICE_FIXTURE_INF_USD = json.dumps({
    "bitcoin": {"usd": float("inf"), "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": 2.34},
}).encode()

PRICE_FIXTURE_STRING_USD = json.dumps({
    "bitcoin": {"usd": "sixty-seven-thousand", "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": 2.34},
}).encode()

PRICE_FIXTURE_BOOL_USD = json.dumps({
    "bitcoin": {"usd": True, "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": 2.34},
}).encode()

PRICE_FIXTURE_ZERO_USD = json.dumps({
    "bitcoin": {"usd": 0, "usd_market_cap": 1_330_000_000_000, "usd_24h_change": 2.34},
}).encode()

PRICE_FIXTURE_NEGATIVE_USD = json.dumps({
    "bitcoin": {"usd": -100.0, "usd_market_cap": 1_330_000_000_000, "usd_24h_change": 2.34},
}).encode()

PRICE_FIXTURE_NAN_MCAP = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": float("nan"), "usd_24h_change": 2.34},
}).encode()

PRICE_FIXTURE_NEGATIVE_MCAP = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": -5.0, "usd_24h_change": 2.34},
}).encode()

PRICE_FIXTURE_NAN_LAST_UPDATED = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": 2.34, "last_updated_at": float("nan")},
}).encode()

PRICE_FIXTURE_STRING_LAST_UPDATED = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": 2.34, "last_updated_at": "just now"},
}).encode()

# codex HIGH（未來/超大時間戳 → 最高 recency 信任）：以下四種皆為「有限」但
# 不合理的 epoch，只驗有限性擋不住，須靠合理範圍檢查。
PRICE_FIXTURE_NEGATIVE_LAST_UPDATED = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": 2.34, "last_updated_at": -1_700_000_000},
}).encode()

PRICE_FIXTURE_ZERO_LAST_UPDATED = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": 2.34, "last_updated_at": 0},
}).encode()

# 西元 3000 年附近的 epoch：對任何現實中會執行本測試的時間點都是「遠未來」。
PRICE_FIXTURE_FAR_FUTURE_LAST_UPDATED = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": 2.34, "last_updated_at": 32_503_680_000},
}).encode()

PRICE_FIXTURE_OVERSIZED_LAST_UPDATED = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": 2.34, "last_updated_at": 1e18},
}).encode()

# codex HIGH（一次 sweep）：24h 漲跌幅超出合理範圍 ±100%，理應視為壞資料。
PRICE_FIXTURE_EXTREME_POSITIVE_CHANGE = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": 5000.0},
}).encode()

PRICE_FIXTURE_EXTREME_NEGATIVE_CHANGE = json.dumps({
    "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                "usd_24h_change": -300.0},
}).encode()


@pytest.fixture(autouse=True)
def _reset_coingecko_process_cache():
    """每個測試案例前後都清空 coingecko.py 的記憶體快取（`_get_price_data()`/
    `_get_coin_detail()`），避免案例之間互相污染（見 coingecko.py 模組頂部
    「高效抓取」說明——這兩個快取設計上只該活在單一 process 生命週期內）。"""
    from trustforge.ingestion import coingecko
    coingecko.reset_process_cache()
    yield
    coingecko.reset_process_cache()


# ── CoinGeckoPriceSource ──────────────────────────────────────────────────────

def test_price_source_document_fields(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE)
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
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE)
    assert coingecko.CoinGeckoPriceSource().fetch("", coin="DOGE") == []


def test_price_source_empty_coin_returns_all_five(monkeypatch):
    """coin='' 時（全市場通用查詢）回傳 5 幣各一筆，皆帶顯式 meta['coin']。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="")
    assert len(docs) == 5
    coins = {d.meta["coin"] for d in docs}
    assert coins == {"BTC", "ETH", "SOL", "BNB", "XRP"}


def test_price_source_missing_fields_degrades_to_na(monkeypatch):
    """缺 24h 變動 / 市值欄位時不炸，改顯示 N/A。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE_MISSING_FIELDS)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    ref = docs[0].meta["content_reference"]
    assert "N/A" in ref


def test_price_source_null_fields_degrades_to_na(monkeypatch):
    """欄位為 null（非缺欄）時同樣不炸，改顯示 N/A。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE_NULL_FIELDS)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    ref = docs[0].meta["content_reference"]
    assert "N/A" in ref


def test_price_source_missing_price_entirely_skips_coin(monkeypatch):
    """整個幣別在回應中缺席（如 API 部分降級）時該幣被跳過，不炸。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: b'{"bitcoin": {"usd": 100.0}}')
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="ETH")
    assert docs == []


def test_price_source_failure_does_not_crash_collect(monkeypatch):
    """連接器逾時/例外 → collect 跳過該來源，不拋例外。"""
    from urllib.error import URLError
    from trustforge.ingestion import coingecko, base
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: (_ for _ in ()).throw(URLError("timeout")))
    src = coingecko.CoinGeckoPriceSource()
    docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)


# codex MEDIUM #3（呼應 #24 不造假，Tier2 最後一根，收斂整個數值欄位驗證
# 類別）：`usd`（現價）壞值不得被當成有效觀測放行——`price_live` 是
# OBJECTIVE_KINDS、會直接進 `detect_cross_source_signal`，壞現價因此可能
# 變成內部看似合理、實則無效的客觀觀測，製造假背離/假共識。下面全部走
# 真實 `CoinGeckoPriceSource.fetch()`，不手造 Document。

@pytest.mark.parametrize("fixture_name", [
    "PRICE_FIXTURE_NAN_USD",
    "PRICE_FIXTURE_INF_USD",
    "PRICE_FIXTURE_STRING_USD",
    "PRICE_FIXTURE_BOOL_USD",
    "PRICE_FIXTURE_ZERO_USD",
    "PRICE_FIXTURE_NEGATIVE_USD",
])
def test_price_source_bad_usd_produces_no_document(monkeypatch, fixture_name):
    """usd 是 NaN/inf/字串/bool/零/負值：一律不產該幣 price_live Document
    （現價是這個 Document 存在的唯一理由，壞現價沒有「退化成 N/A」的意義，
    直接跳過該幣，不進 OBJECTIVE_KINDS、不參與背離偵測）。"""
    from trustforge.ingestion import coingecko
    fixture = globals()[fixture_name]
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: fixture)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert docs == [], f"{fixture_name} 壞 usd 不應產生 Document，實得 {docs!r}"


def test_price_source_bad_usd_does_not_enter_divergence_detection(monkeypatch):
    """端到端：usd=NaN 時 `CoinGeckoPriceSource` 不產 Document → 真實
    `extract_claims`/`score`/`detect_cross_source_signal` 全程看不到這個
    壞現價觀測（不是產生了一個 neutral 的假觀測，而是它根本不存在），
    不炸、不造假、也不會意外冒出背離/共識結果。"""
    from trustforge.ingestion import coingecko
    from trustforge.ingestion.base import Document
    from trustforge.trust.scoring import extract_claims, score
    from trustforge.agent.orchestrator import detect_cross_source_signal

    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE_NAN_USD)
    price_docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert price_docs == []

    social_doc = Document(
        id="social-btc-1", kind="social", source="reddit-btc",
        text="BTC 社群看漲氣氛濃厚，買盤湧入", ts=1_700_000_000.0,
    )
    claims = extract_claims(price_docs + [social_doc])
    scored = score(claims, now=1_700_000_100.0)
    result = detect_cross_source_signal(scored)
    assert result is None, f"壞 usd 被排除後客觀類應為空、不應產生訊號，實得 {result}"


def test_price_source_nan_market_cap_degrades_to_na_not_fabricated(monkeypatch):
    """market_cap 是 NaN：現價本身仍有效就照常產 Document，market_cap 欄位
    單獨退化成 N/A，不崩潰、不捏造市值數字。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE_NAN_MCAP)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    ref = docs[0].meta["content_reference"]
    assert "67823.45" in ref
    assert "市值 N/A" in ref


def test_price_source_negative_market_cap_degrades_to_na(monkeypatch):
    """market_cap 是負值（不合理的市值）：退化成 N/A，不當成合法數字顯示。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE_NEGATIVE_MCAP)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    ref = docs[0].meta["content_reference"]
    assert "市值 N/A" in ref


def test_price_source_nan_last_updated_falls_back_to_fetch_time_not_nan(monkeypatch):
    """last_updated_at 是 NaN：不得把 NaN 塞進 `Document.ts`（會污染 recency
    衰減計算），須退回本地呼叫當下時間（有限、可用的時間戳）。"""
    import math
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE_NAN_LAST_UPDATED)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    assert math.isfinite(docs[0].ts)


def test_price_source_string_last_updated_falls_back_to_fetch_time(monkeypatch):
    """last_updated_at 是非數值字串：同樣不得崩潰，退回本地呼叫當下時間。"""
    import math
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE_STRING_LAST_UPDATED)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    assert math.isfinite(docs[0].ts)


# codex HIGH（未來/超大時間戳 → 最高 recency 信任，coingecko.py:286-294）：
# `last_updated_at` 只驗「有限」擋不住未來/超大 epoch——這些值會直接變成
# `Document.ts`，讓 `_recency_decay` 算出負齡、clamp 成 0 齡 → recency=1.0
# （最高信任），等於把壞資料捏造成「最新鮮」的觀測。以下四種皆須退回
# `fallback_now`（本地真實呼叫時間），且經過真實 `score()` 得到的 recency
# 分數不得被灌成 1.0（斷言實際分數落在合理衰減曲線內，而非只驗
# `math.isfinite`）。
@pytest.mark.parametrize(
    "fixture_json",
    [
        PRICE_FIXTURE_NEGATIVE_LAST_UPDATED,
        PRICE_FIXTURE_ZERO_LAST_UPDATED,
        PRICE_FIXTURE_FAR_FUTURE_LAST_UPDATED,
        PRICE_FIXTURE_OVERSIZED_LAST_UPDATED,
    ],
    ids=["negative", "zero", "far_future", "oversized_finite"],
)
def test_price_source_implausible_last_updated_falls_back_to_fetch_time(monkeypatch, fixture_json):
    """負/零/遠未來/超大有限 epoch：Document.ts 須落在合理範圍（退回
    fallback_now），不得直接採用壞值。"""
    import math
    import time
    from trustforge.ingestion import coingecko
    before = time.time()
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: fixture_json)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    after = time.time()
    assert len(docs) == 1
    assert math.isfinite(docs[0].ts)
    # 退回的是本地呼叫當下時間（fallback_now），不是原始壞值。
    assert before - 1.0 <= docs[0].ts <= after + 1.0


@pytest.mark.parametrize(
    "fixture_json",
    [
        PRICE_FIXTURE_NEGATIVE_LAST_UPDATED,
        PRICE_FIXTURE_ZERO_LAST_UPDATED,
        PRICE_FIXTURE_FAR_FUTURE_LAST_UPDATED,
        PRICE_FIXTURE_OVERSIZED_LAST_UPDATED,
    ],
    ids=["negative", "zero", "far_future", "oversized_finite"],
)
def test_price_source_implausible_last_updated_does_not_inflate_recency_to_max(monkeypatch, fixture_json):
    """真實 score() 路徑：壞時間戳退回 fallback_now 後，recency 分數應反映
    「剛剛才 fetch」的正常新鮮度（略小於 1.0，因為 fetch 到 score 之間必有
    非零耗時），而不是被壞值人為灌成剛好 1.0 的最高信任——用以證明壞資料
    確實被判定為過去時間、走正常衰減公式，不是繞過驗證的巧合。"""
    import time
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims, score

    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: fixture_json)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1

    claims = extract_claims(docs)
    scored = score(claims, now=time.time() + 1.0)
    assert len(scored) == 1
    recency = scored[0].components.get("recency")
    assert recency is not None
    assert 0.0 <= recency < 1.0, (
        f"壞時間戳退回 fallback_now 後，recency 不應被灌成 1.0 最高信任，實得 {recency}"
    )


@pytest.mark.parametrize(
    "fixture_json",
    [PRICE_FIXTURE_EXTREME_POSITIVE_CHANGE, PRICE_FIXTURE_EXTREME_NEGATIVE_CHANGE],
    ids=["extreme_positive", "extreme_negative"],
)
def test_price_source_extreme_change_pct_degrades_to_na_not_fabricated(monkeypatch, fixture_json):
    """codex HIGH sweep：24h 漲跌幅超出合理範圍 ±100%（如單位換算錯亂造成
    的 5000%/-300%）視為壞資料，只退回 N/A、不下方向措辭，不得寫進 ref。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: fixture_json)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    assert "N/A" in docs[0].text
    assert "上漲" not in docs[0].text
    assert "下跌" not in docs[0].text
    assert "持平" not in docs[0].text


@pytest.mark.parametrize(
    "fixture_json",
    [PRICE_FIXTURE_EXTREME_POSITIVE_CHANGE, PRICE_FIXTURE_EXTREME_NEGATIVE_CHANGE],
    ids=["extreme_positive", "extreme_negative"],
)
def test_price_source_extreme_change_pct_infers_neutral_not_fabricated_direction(monkeypatch, fixture_json):
    """真實 extract_claims 路徑：24h 漲跌幅超出合理範圍時，文字不含方向詞，
    推出的 direction 須是 neutral，不得因壞資料被捏造成 bullish/bearish。"""
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: fixture_json)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    claims = extract_claims(docs)
    assert len(claims) == 1
    assert claims[0].direction == "neutral"


# codex HIGH（subtle 邊界：容忍範圍內近未來時間戳仍拿滿分 recency，
# coingecko.py:311-316）：時鐘偏差容忍讓 `fallback_now+1s ~ +300s` 的戳通過
# 合理範圍檢查（合法接受，不誤拒），但若原封不動存為 Document.ts，
# `_recency_decay` 對「未來」時間戳一樣會把負齡 clamp 成 0 → recency=1.0。
# 上面遠未來/超大有限的測試都遠超容忍窗口，測不到這個邊界，須專測
# 容忍窗口內的近未來值。
@pytest.mark.parametrize("skew_sec", [1.0, 300.0], ids=["skew_plus_1s", "skew_plus_300s"])
def test_price_source_near_future_within_tolerance_ts_clamped_to_now(monkeypatch, skew_sec):
    """容忍範圍內的近未來 last_updated_at（now+1s / now+300s）：驗證通過
    （視為真實資料、不誤拒），但儲存的 ts 須 clamp 到 min(ts, now)，不得
    直接沿用未來值。"""
    import json
    import time
    from trustforge.ingestion import coingecko

    before = time.time()
    fixture = json.dumps({
        "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                    "usd_24h_change": 2.34, "last_updated_at": before + skew_sec},
    }).encode()
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: fixture)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    after = time.time()
    assert len(docs) == 1
    assert docs[0].ts <= after, f"近未來戳（+{skew_sec}s）須 clamp 到 now，實得 ts={docs[0].ts}，now~{after}"


@pytest.mark.parametrize("skew_sec", [1.0, 300.0], ids=["skew_plus_1s", "skew_plus_300s"])
def test_price_source_near_future_within_tolerance_does_not_max_out_recency(monkeypatch, skew_sec):
    """真實 score() 路徑：容忍範圍內的近未來時間戳 clamp 後，recency 分數
    須反映「非負齡」的正常衰減（< 1.0），不得因未來戳被灌成最高信任。"""
    import json
    import time
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims, score

    fetch_time = time.time()
    fixture = json.dumps({
        "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                    "usd_24h_change": 2.34, "last_updated_at": fetch_time + skew_sec},
    }).encode()
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: fixture)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1

    claims = extract_claims(docs)
    scored = score(claims, now=time.time() + 1.0)
    assert len(scored) == 1
    recency = scored[0].components.get("recency")
    assert recency is not None
    assert 0.0 <= recency < 1.0, (
        f"容忍窗口內近未來戳（+{skew_sec}s）clamp 後 recency 不應被灌成 1.0，實得 {recency}"
    )


# ── CoinGeckoSentimentSource ──────────────────────────────────────────────────

def test_sentiment_source_document_fields(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_NORMAL)
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
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_NORMAL)
    assert coingecko.CoinGeckoSentimentSource().fetch("", coin="DOGE") == []


def test_sentiment_source_empty_coin_skipped(monkeypatch):
    """空 coin 無法決定要打哪個 id 的端點，直接跳過。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_NORMAL)
    assert coingecko.CoinGeckoSentimentSource().fetch("", coin="") == []


def test_sentiment_source_missing_fields_returns_empty(monkeypatch):
    """欄位整個缺席（community_data-only 回應等）時安靜回傳 []，不炸。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_MISSING_FIELDS)
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    assert docs == []


def test_sentiment_source_null_fields_returns_empty(monkeypatch):
    """欄位存在但值為 null（免費 tier 常見）時安靜回傳 []，不炸。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_NULL_FIELDS)
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    assert docs == []


# codex MEDIUM（呼應 #24 不造假，Tier2 最後一根）：partial/malformed 資料不得
# 被捏造成強方向觀測。下面全部走真實 CoinGeckoSentimentSource/
# CoinGeckoPriceSource → trust.scoring.extract_claims 驗證 direction，
# 不手造 direction、不繞過真代碼。

def test_sentiment_source_one_sided_data_does_not_fabricate_direction(monkeypatch):
    """只有多方票數、空方完全缺席：不得捏造成「明確偏多」，須回中性語意、
    direction 維持 neutral（原 bug：曾把單邊當成 ±100pp 差距硬發強方向詞）。"""
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims
    monkeypatch.setattr(coingecko, "_fetch_url",
                         lambda url, headers=None: DETAIL_FIXTURE_ONE_SIDED_UP_ONLY)
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    assert len(docs) == 1
    ref = docs[0].meta["content_reference"]
    assert "偏多" not in ref and "偏空" not in ref
    assert "N/A" in ref
    assert extract_claims(docs)[0].direction == "neutral"


def test_sentiment_source_out_of_range_pct_does_not_fabricate_direction(monkeypatch):
    """百分比超出 0–100（明顯壞資料，如 150%）不得代入方向判斷。"""
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims
    monkeypatch.setattr(coingecko, "_fetch_url",
                         lambda url, headers=None: DETAIL_FIXTURE_OUT_OF_RANGE)
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    ref = docs[0].meta["content_reference"]
    assert "偏多" not in ref and "偏空" not in ref
    assert extract_claims(docs)[0].direction == "neutral"


def test_sentiment_source_nan_pct_does_not_fabricate_direction(monkeypatch):
    """一邊是 NaN（json 允許的非標準 token）不得代入方向判斷、不得崩潰。"""
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_NAN)
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    ref = docs[0].meta["content_reference"]
    assert "偏多" not in ref and "偏空" not in ref
    assert extract_claims(docs)[0].direction == "neutral"


def test_sentiment_source_inf_pct_does_not_fabricate_direction(monkeypatch):
    """一邊是 Infinity 不得代入方向判斷、不得崩潰。"""
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_INF)
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    ref = docs[0].meta["content_reference"]
    assert "偏多" not in ref and "偏空" not in ref
    assert extract_claims(docs)[0].direction == "neutral"


def test_sentiment_source_non_numeric_pct_does_not_fabricate_direction(monkeypatch):
    """一邊是非數值字串不得代入方向判斷、不得崩潰。"""
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_NON_NUMERIC)
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    ref = docs[0].meta["content_reference"]
    assert "偏多" not in ref and "偏空" not in ref
    assert extract_claims(docs)[0].direction == "neutral"


def test_sentiment_source_exact_positive_boundary_stays_neutral(monkeypatch):
    """diff 剛好 +5.0pp（等於門檻，非「大於」）：規格是嚴格 `> 5.0` 才算偏多，
    剛好等於門檻不算方向，維持 neutral。"""
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims
    monkeypatch.setattr(coingecko, "_fetch_url",
                         lambda url, headers=None: DETAIL_FIXTURE_EXACT_POSITIVE_BOUNDARY)
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    ref = docs[0].meta["content_reference"]
    assert "偏多" not in ref and "偏空" not in ref
    assert extract_claims(docs)[0].direction == "neutral"


def test_sentiment_source_exact_negative_boundary_stays_neutral(monkeypatch):
    """diff 剛好 -5.0pp（等於門檻，非「小於」）：規格是嚴格 `< -5.0` 才算偏空，
    剛好等於門檻不算方向，維持 neutral。"""
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims
    monkeypatch.setattr(coingecko, "_fetch_url",
                         lambda url, headers=None: DETAIL_FIXTURE_EXACT_NEGATIVE_BOUNDARY)
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    ref = docs[0].meta["content_reference"]
    assert "偏多" not in ref and "偏空" not in ref
    assert extract_claims(docs)[0].direction == "neutral"


def test_price_source_nan_change_degrades_to_na_not_fake_flat(monkeypatch):
    """24h 變動是 NaN：NaN 跟 0 比較永遠是 False，若不擋會落入 else 分支被
    誤判成「持平」——等於把壞資料捏造成一個看似合理的觀測。須退回 N/A，
    不得寫「持平」，direction 維持 neutral。"""
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE_NAN_CHANGE)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    ref = docs[0].meta["content_reference"]
    assert "N/A" in ref
    assert "持平" not in ref and "上漲" not in ref and "下跌" not in ref
    assert extract_claims(docs)[0].direction == "neutral"


def test_price_source_inf_change_degrades_to_na_not_fake_direction(monkeypatch):
    """24h 變動是 Infinity：`inf > 0` 為 True，若不擋會被誤判成真實大漲。
    須退回 N/A，不得捏造方向。"""
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE_INF_CHANGE)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    ref = docs[0].meta["content_reference"]
    assert "N/A" in ref
    assert "上漲" not in ref and "下跌" not in ref
    assert extract_claims(docs)[0].direction == "neutral"


def test_price_source_non_numeric_change_degrades_to_na(monkeypatch):
    """24h 變動是非數值字串：須退回 N/A，不得崩潰、不得捏造方向。"""
    from trustforge.ingestion import coingecko
    from trustforge.trust.scoring import extract_claims
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE_NON_NUMERIC_CHANGE)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    ref = docs[0].meta["content_reference"]
    assert "N/A" in ref
    assert extract_claims(docs)[0].direction == "neutral"


def test_sentiment_source_failure_does_not_crash_collect(monkeypatch):
    from urllib.error import URLError
    from trustforge.ingestion import coingecko, base
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: (_ for _ in ()).throw(URLError("timeout")))
    src = coingecko.CoinGeckoSentimentSource()
    docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)


# ── CoinGeckoDevSource ────────────────────────────────────────────────────────

def test_dev_source_document_fields(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_NORMAL)
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
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_NORMAL)
    assert coingecko.CoinGeckoDevSource().fetch("", coin="DOGE") == []


def test_dev_source_empty_coin_skipped(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_NORMAL)
    assert coingecko.CoinGeckoDevSource().fetch("", coin="") == []


def test_dev_source_missing_fields_returns_empty(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_MISSING_FIELDS)
    docs = coingecko.CoinGeckoDevSource().fetch("", coin="BTC")
    assert docs == []


def test_dev_source_null_fields_returns_empty(monkeypatch):
    from trustforge.ingestion import coingecko
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_NULL_FIELDS)
    docs = coingecko.CoinGeckoDevSource().fetch("", coin="BTC")
    assert docs == []


def test_dev_source_failure_does_not_crash_collect(monkeypatch):
    from urllib.error import URLError
    from trustforge.ingestion import coingecko, base
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: (_ for _ in ()).throw(URLError("timeout")))
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

    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: PRICE_FIXTURE)

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
    monkeypatch.setattr(coingecko, "_fetch_url", lambda url, headers=None: DETAIL_FIXTURE_NORMAL)
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

    def _boom(url, headers=None):  # pragma: no cover - 不應被呼叫到
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
    """三者統一 5 分鐘（300s）一輪（老闆修正：keyless 已足夠，不必分快慢）。"""
    from trustforge.ingestion.cache import DEFAULT_REFRESH_INTERVAL_SECONDS, DEFAULT_STALE_AFTER_SECONDS
    assert DEFAULT_REFRESH_INTERVAL_SECONDS["coingecko-price"] == 5 * 60
    assert DEFAULT_REFRESH_INTERVAL_SECONDS["coingecko-sentiment"] == 5 * 60
    assert DEFAULT_REFRESH_INTERVAL_SECONDS["coingecko-dev"] == 5 * 60
    # 硬過期時限應為 refresh 間隔的 STALE_AFTER_MULTIPLIER 倍（衍生值，非獨立手填）= 900s
    for name in ("coingecko-price", "coingecko-sentiment", "coingecko-dev"):
        assert DEFAULT_STALE_AFTER_SECONDS[name] == 900
        assert DEFAULT_STALE_AFTER_SECONDS[name] == DEFAULT_REFRESH_INTERVAL_SECONDS[name] * 3


# ── 高效抓取：現價 1 次涵蓋 5 幣 / coins-detail 每幣 1 次由 sentiment+dev 共用 ──

def test_price_source_single_real_call_shared_across_all_coins(monkeypatch):
    """CoinGeckoPriceSource 在同一輪（process 生命週期）內不論被呼叫幾次
    （每幣各呼叫一次，模擬 fetch_scheduler 逐幣迴圈），底層 `_fetch_url`
    只會真的被呼叫 1 次——現價回應本身已涵蓋全部 5 幣。"""
    from trustforge.ingestion import coingecko

    calls: list[str] = []

    def _counting_fetch(url, headers=None):
        calls.append(url)
        return PRICE_FIXTURE

    monkeypatch.setattr(coingecko, "_fetch_url", _counting_fetch)
    src = coingecko.CoinGeckoPriceSource()
    for code in ("BTC", "ETH", "SOL", "BNB", "XRP"):
        docs = src.fetch("", coin=code)
        assert len(docs) == 1
    assert len(calls) == 1, f"應只有 1 次真呼叫，實際 {len(calls)} 次：{calls}"


def test_sentiment_and_dev_share_single_coin_detail_call(monkeypatch):
    """CoinGeckoSentimentSource 與 CoinGeckoDevSource 打同一個 coins/{id}
    端點；同一輪內先呼叫其中一個後，另一個直接複用快取，底層 `_fetch_url`
    對同一幣只會真的被呼叫 1 次（不是各自獨立呼叫變 2 次）。"""
    from trustforge.ingestion import coingecko

    calls: list[str] = []

    def _counting_fetch(url, headers=None):
        calls.append(url)
        return DETAIL_FIXTURE_NORMAL

    monkeypatch.setattr(coingecko, "_fetch_url", _counting_fetch)
    sentiment_docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    dev_docs = coingecko.CoinGeckoDevSource().fetch("", coin="BTC")
    assert len(sentiment_docs) == 1
    assert len(dev_docs) == 1
    assert len(calls) == 1, f"同一幣 coins/detail 應只打 1 次，實際 {len(calls)} 次：{calls}"


def test_full_round_five_coins_totals_about_six_real_calls(monkeypatch):
    """模擬 fetch_scheduler 對 5 幣跑一輪三個 Source：現價 1 次 + 5 幣
    coins/detail 各 1 次（sentiment/dev 共用）= 6 次，不是 15 次。"""
    from trustforge.ingestion import coingecko

    calls: list[str] = []

    def _counting_fetch(url, headers=None):
        calls.append(url)
        if "simple/price" in url:
            return PRICE_FIXTURE
        return DETAIL_FIXTURE_NORMAL

    monkeypatch.setattr(coingecko, "_fetch_url", _counting_fetch)
    price_src = coingecko.CoinGeckoPriceSource()
    sentiment_src = coingecko.CoinGeckoSentimentSource()
    dev_src = coingecko.CoinGeckoDevSource()
    coins = ["BTC", "ETH", "SOL", "BNB", "XRP"]
    for code in coins:
        price_src.fetch("", coin=code)
    for code in coins:
        dev_src.fetch("", coin=code)
    for code in coins:
        sentiment_src.fetch("", coin=code)

    assert len(calls) == 6, f"預期 6 次真呼叫（1 現價 + 5 幣 detail），實際 {len(calls)} 次：{calls}"


# ── Demo API key（選用，keyless 已足夠；透過 header 傳遞，URL 全程乾淨）────────
#    codex 對抗審 HIGH 修正：舊版把 key 附成 URL query param，即使 Document.url
#    乾淨，實際發送的請求仍含 key，會被 proxy/tracing/access-log/例外訊息
#    外洩。改用 `x-cg-demo-api-key` 請求 header 傳遞後，URL 全程不含 key。
#    `_fetch_url` 現在透過共用的 `safe_fetch.fetch_url()` 送出真請求（見
#    codex 對抗審第 3 輪 SSRF 修復），以下測試直接 mock
#    `coingecko.safe_fetch.fetch_url`，檢視傳入的 `url`/`extra_headers`
#    參數，證明 key 只走 header、不落在 URL 上。

def test_api_key_sent_via_header_not_url_when_env_set(monkeypatch):
    """設定 COINGECKO_API_KEY 時，key 應透過 `extra_headers`（最終送進
    `x-cg-demo-api-key` 請求 header）傳給 `safe_fetch.fetch_url`；傳入的
    `url` 本身完全不含 key（不只 Document.url 乾淨——交給 SSRF-safe fetch
    層的 URL 也必須乾淨）。"""
    from trustforge.ingestion import coingecko

    fake_key = "test-fake-demo-key-not-real"
    monkeypatch.setenv("COINGECKO_API_KEY", fake_key)
    captured: dict = {}

    def _fake_fetch_url(url, *, user_agent, extra_headers=None, timeout=None, max_bytes=None):
        captured["url"] = url
        captured["extra_headers"] = extra_headers
        return PRICE_FIXTURE

    monkeypatch.setattr(coingecko.safe_fetch, "fetch_url", _fake_fetch_url)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")

    assert fake_key not in captured["url"]
    assert captured["extra_headers"] == {"x-cg-demo-api-key": fake_key}
    # Document 對外欄位一律乾淨，不留 key 痕跡
    assert fake_key not in docs[0].url
    assert fake_key not in json.dumps(docs[0].meta)
    assert fake_key not in docs[0].text


def test_no_api_key_header_when_env_not_set(monkeypatch):
    """未設定 COINGECKO_API_KEY 時，實際請求 URL 與 header 皆不含任何 key
    （keyless 降級，不報錯、正常運作，也不會附加空的 header）。"""
    from trustforge.ingestion import coingecko

    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    captured: dict = {}

    def _fake_fetch_url(url, *, user_agent, extra_headers=None, timeout=None, max_bytes=None):
        captured["url"] = url
        captured["extra_headers"] = extra_headers
        return PRICE_FIXTURE

    monkeypatch.setattr(coingecko.safe_fetch, "fetch_url", _fake_fetch_url)
    docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")

    assert "x_cg_demo_api_key" not in captured["url"]
    assert not captured["extra_headers"]
    assert len(docs) == 1


def test_api_key_headers_helper_blank_env_treated_as_unset(monkeypatch):
    """空字串/純空白 env 值視為未設定，回傳空 header 字典（不附加空 key）。"""
    from trustforge.ingestion import coingecko

    monkeypatch.setenv("COINGECKO_API_KEY", "   ")
    assert coingecko._api_key_headers() == {}


def test_no_hardcoded_api_key_in_source_module():
    """原始碼本身不得含任何看起來像真實 key 的寫死字串——本檔只允許透過
    env 讀取（`os.environ.get(_API_KEY_ENV, ...)`），確保 secret 只從外部
    注入，不落地進 repo。"""
    from trustforge.ingestion import coingecko
    src = Path(coingecko.__file__).read_text(encoding="utf-8")
    assert "os.environ.get(_API_KEY_ENV" in src
    # 唯一允許出現的 "= " 後面接字面字串賦值給 key 相關變數的地方應該只有
    # env 變數名稱常數本身，不應該有第二個看起來像 API key 值的字面常數。
    assert 'CG-' not in src  # CoinGecko 官方 key 前綴慣例，不應出現在原始碼


# ── 整個 host 共享節流器（生產事故修復：keyless 仍 429，codex HIGH）───────────
#
# 舊版只有各 Source 獨立的排程間隔（fetch_scheduler.py 的 --stagger），互不
# 共享——若排程順序先跑完 coingecko-dev（5 幣 detail，間隔 6 秒）再跑
# coingecko-price（1 次），30 秒內仍發生 6 次真請求（12 次/分鐘），keyless
# 5-15 req/min 的保守下限（5/min）依然可能撞到。以下驗證 `_fetch_url` 內部
# 的共享節流器（`_throttle_before_request`）能不分 Source/端點，強制任兩次
# 真請求之間至少間隔 `_min_interval_seconds()` 秒。
#
# 全部用假時鐘（`monkeypatch.setattr(coingecko.time, "monotonic"/"sleep", ...)`）
# 取代真的 `time.sleep`，測試不會真的等待，維持測試套件秒級執行。

def _install_fake_clock(monkeypatch):
    """安裝假的 `time.monotonic`/`time.sleep`：`time.sleep(s)` 不會真的等
    待，只把假時鐘往前推進 `s` 秒，讓節流邏輯的等待時間可被斷言而不拖慢
    測試。回傳 `clock`（list[0] 為目前假時間）與 `sleeps`（記錄的每次
    sleep 秒數）。"""
    from trustforge.ingestion import coingecko
    clock = [0.0]
    sleeps: list[float] = []

    def _fake_monotonic():
        return clock[0]

    def _fake_sleep(s):
        sleeps.append(s)
        clock[0] += s

    monkeypatch.setattr(coingecko.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(coingecko.time, "sleep", _fake_sleep)
    return clock, sleeps


def test_throttle_shares_state_across_price_and_sentiment_sources(monkeypatch):
    """核心驗收：price 與 sentiment（不同 Source、不同端點）共用同一個節流
    狀態——第 2、3 次真請求前都補足到 keyless 下限 12 秒，不因為換了
    Source/端點就重新起算。"""
    from trustforge.ingestion import coingecko
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    clock, sleeps = _install_fake_clock(monkeypatch)

    def _fake_fetch_url(url, *, user_agent, extra_headers=None, timeout=None, max_bytes=None):
        return PRICE_FIXTURE if "simple/price" in url else DETAIL_FIXTURE_NORMAL

    monkeypatch.setattr(coingecko.safe_fetch, "fetch_url", _fake_fetch_url)

    coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")       # 真請求 #1（price）
    coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")   # 真請求 #2（coin-detail，不同端點）
    coingecko.CoinGeckoSentimentSource().fetch("", coin="ETH")   # 真請求 #3（不同幣，新的 coin-detail）

    assert sleeps == [12.0, 12.0], f"跨 Source 的真請求間隔應共享節流狀態，實得 sleeps={sleeps}"


def test_throttle_skips_sleep_when_elapsed_already_sufficient(monkeypatch):
    """呼叫端之間本來就間隔夠久（模擬慢速的上游處理時間）時，節流器不該
    多此一舉再補一次 sleep。"""
    from trustforge.ingestion import coingecko
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    clock, sleeps = _install_fake_clock(monkeypatch)

    def _fake_fetch_url(url, *, user_agent, extra_headers=None, timeout=None, max_bytes=None):
        return PRICE_FIXTURE

    monkeypatch.setattr(coingecko.safe_fetch, "fetch_url", _fake_fetch_url)

    coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    clock[0] = 20.0  # 模擬呼叫端之間本來就間隔了 20 秒（超過 12 秒下限）
    coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")

    assert sleeps == []


def test_throttle_uses_shorter_interval_when_api_key_set(monkeypatch):
    """有 `COINGECKO_API_KEY` 時（30 req/min）節流間隔放寬到 2 秒，不再套
    用 keyless 的 12 秒下限。"""
    from trustforge.ingestion import coingecko
    monkeypatch.setenv("COINGECKO_API_KEY", "test-fake-demo-key-not-real")
    clock, sleeps = _install_fake_clock(monkeypatch)

    def _fake_fetch_url(url, *, user_agent, extra_headers=None, timeout=None, max_bytes=None):
        return PRICE_FIXTURE if "simple/price" in url else DETAIL_FIXTURE_NORMAL

    monkeypatch.setattr(coingecko.safe_fetch, "fetch_url", _fake_fetch_url)

    coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")

    assert sleeps == [2.0]


def test_throttle_full_round_six_calls_all_gaps_at_least_keyless_floor(monkeypatch):
    """端到端：完整一輪（price 1 次 + 5 幣 coin-detail 各 1 次，共 6 次真
    請求，對應生產排程一輪的真實請求數）不管排程呼叫序為何，任兩次真請求
    的時間戳差距都 ≥12 秒（keyless）——換算最多 5 次/分鐘，不會再撞
    keyless 5-15 req/min 的保守下限。"""
    from trustforge.ingestion import coingecko
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    clock, _sleeps = _install_fake_clock(monkeypatch)
    timestamps: list[float] = []

    def _fake_fetch_url(url, *, user_agent, extra_headers=None, timeout=None, max_bytes=None):
        timestamps.append(clock[0])
        return PRICE_FIXTURE if "simple/price" in url else DETAIL_FIXTURE_NORMAL

    monkeypatch.setattr(coingecko.safe_fetch, "fetch_url", _fake_fetch_url)

    coingecko.CoinGeckoPriceSource().fetch("", coin="")
    for code in ("BTC", "ETH", "SOL", "BNB", "XRP"):
        coingecko.CoinGeckoSentimentSource().fetch("", coin=code)

    assert len(timestamps) == 6
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    assert all(g >= 12.0 for g in gaps), f"任兩次真請求間隔須 ≥12 秒，實得 gaps={gaps}"


def test_coin_detail_failure_is_not_retried_by_second_source(monkeypatch):
    """核心驗收（去重）：coins/{id} 第一次呼叫失敗（如 429）後，第二個共用
    同端點的 Source（sentiment 先失敗，dev 接著對同一幣呼叫）不會再對同一
    幣重打一次真請求——直接重新拋出同一個已知失敗，`_fetch_url` 只被呼叫
    1 次而非 2 次。"""
    from urllib.error import URLError
    from trustforge.ingestion import coingecko

    calls: list[str] = []

    def _fake_fetch_url(url, headers=None):
        calls.append(url)
        raise URLError("429 too many requests")

    monkeypatch.setattr(coingecko, "_fetch_url", _fake_fetch_url)

    with pytest.raises(URLError):
        coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    with pytest.raises(URLError):
        coingecko.CoinGeckoDevSource().fetch("", coin="BTC")

    assert len(calls) == 1, (
        f"第二個 Source 不該對同一幣已知失敗的請求再打一次真請求，實得 {len(calls)} 次：{calls}"
    )


def test_coin_detail_failure_cache_is_scoped_per_coin(monkeypatch):
    """去重快取以 coingecko id 為粒度：BTC 失敗不影響 ETH 仍正常打真請求。"""
    from urllib.error import URLError
    from trustforge.ingestion import coingecko

    calls: list[str] = []

    def _fake_fetch_url(url, headers=None):
        calls.append(url)
        if "bitcoin" in url:
            raise URLError("429 too many requests")
        return DETAIL_FIXTURE_NORMAL

    monkeypatch.setattr(coingecko, "_fetch_url", _fake_fetch_url)

    with pytest.raises(URLError):
        coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="ETH")

    assert len(docs) == 1
    assert len(calls) == 2  # BTC 一次（失敗）+ ETH 一次（成功），互不干擾


def test_reset_process_cache_clears_failure_cache_and_throttle_state():
    """`reset_process_cache()` 須連同去重失敗快取、共享節流時間戳一併清空
    （不只是原本的成功結果快取），避免跨測試案例/跨輪殘留污染。"""
    from trustforge.ingestion import coingecko

    coingecko._coin_detail_failed["bitcoin"] = RuntimeError("boom")
    coingecko._last_request_monotonic = 123.0

    coingecko.reset_process_cache()

    assert coingecko._coin_detail_failed == {}
    assert coingecko._last_request_monotonic is None


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
