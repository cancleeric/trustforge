"""#1162 DefiLlama 連接器整合測試 — CI 不打真網路（monkeypatch defillama._fetch_url）。

涵蓋：
  - price：URL 含 6 key 白名單、parse 6 Document、meta.coin、_finite_num 驗證。
  - price 壞資料（NaN/缺欄）→ 不產 Document（#24 不造假）。
  - tvl：chain 映射只產 ETH/SOL/BNB/ARB；BTC/XRP 回 []（無意義 DeFi TVL）。
  - tvl 方向詞（端點若有 change_24h 欄）；真實端點無此欄 → 中性。
  - no-false-divergence：defi_tvl 客觀類不製造假背離（TVL 無方向詞 → neutral）。
  - price corroboration consensus：coingecko-price + defillama-price 同幣 → 兩個
    獨立來源互相佐證。
  - offline：OfflineSampleSource 讀到 defi_tvl 樣本。
"""
from __future__ import annotations

import json

# ── 固定 fixture（模擬 DefiLlama API 回應）──────────────────────────────────────

# prices/current 回應：6 幣各一筆（key 用 coingecko:<id> 命名空間）。
PRICE_FIXTURE = json.dumps({
    "coins": {
        "coingecko:bitcoin": {"price": 67000.5, "symbol": "BTC", "timestamp": 1_700_000_000, "confidence": 0.99},
        "coingecko:ethereum": {"price": 3521.1, "symbol": "ETH", "timestamp": 1_700_000_000, "confidence": 0.99},
        "coingecko:solana": {"price": 172.5, "symbol": "SOL", "timestamp": 1_700_000_000, "confidence": 0.98},
        "coingecko:binancecoin": {"price": 610.2, "symbol": "BNB", "timestamp": 1_700_000_000, "confidence": 0.98},
        "coingecko:ripple": {"price": 0.62, "symbol": "XRP", "timestamp": 1_700_000_000, "confidence": 0.97},
        "coingecko:arbitrum": {"price": 1.18, "symbol": "ARB", "timestamp": 1_700_000_000, "confidence": 0.97},
    }
}).encode()

# 現價壞資料：BTC price=NaN、ETH 完全缺 price 欄 → 兩幣都不應產 Document。
PRICE_FIXTURE_BAD = json.dumps({
    "coins": {
        "coingecko:bitcoin": {"price": float("nan"), "symbol": "BTC", "timestamp": 1_700_000_000},
        "coingecko:ethereum": {"symbol": "ETH", "timestamp": 1_700_000_000},  # 缺 price
    }
}).encode()

# /v2/chains 回應（真實端點：無 change_24h 欄，僅現值 tvl）。
TVL_FIXTURE = json.dumps([
    {"name": "Ethereum", "tvl": 58_000_000_000, "chainSymbol": "ethereum"},
    {"name": "Solana", "tvl": 12_500_000_000, "chainSymbol": "solana"},
    {"name": "BSC", "tvl": 6_200_000_000, "chainSymbol": "bsc"},
    {"name": "Arbitrum", "tvl": 4_100_000_000, "chainSymbol": "arbitrum"},
    {"name": "Tron", "tvl": 5_000_000_000, "chainSymbol": "tron"},  # 非白名單鏈，應忽略
]).encode()

# /v2/chains 帶 change_24h 欄的回應（模擬未來端點升級／測試注入，驗方向詞路徑）。
TVL_FIXTURE_WITH_CHANGE = json.dumps([
    {"name": "Ethereum", "tvl": 58_000_000_000, "change_24h": 2.5},   # 流入 → 偏多
    {"name": "Solana", "tvl": 12_500_000_000, "change_24h": -1.8},    # 流出 → 偏空
    {"name": "BSC", "tvl": 6_200_000_000, "change_24h": 0.0},         # 持平
    {"name": "Arbitrum", "tvl": 4_100_000_000},                       # 無 change → 中性
]).encode()

# TVL 壞資料：tvl=NaN / 缺欄 → 不產 Document（#24 不造假）。
TVL_FIXTURE_BAD = json.dumps([
    {"name": "Ethereum", "tvl": float("nan")},
    {"name": "Solana", "chainSymbol": "solana"},  # 缺 tvl
]).encode()


# ── A. DefiLlamaPriceSource ────────────────────────────────────────────────────

def test_price_source_url_contains_all_whitelisted_keys(monkeypatch):
    """coin='' 時 URL path 段含全部 6 個白名單 coingecko id（一次呼叫涵蓋多幣）。"""
    from trustforge.ingestion import defillama

    captured: list[str] = []

    def _capture(url):
        captured.append(url)
        return PRICE_FIXTURE

    monkeypatch.setattr(defillama, "_fetch_url", _capture)
    defillama.DefiLlamaPriceSource().fetch("", coin="")
    assert len(captured) == 1
    url = captured[0]
    assert url.startswith("https://coins.llama.fi/prices/current/")
    for gid in ("bitcoin", "ethereum", "solana", "binancecoin", "ripple", "arbitrum"):
        assert f"coingecko:{gid}" in url, f"URL 缺白名單 key coingecko:{gid}：{url}"


def test_price_source_parses_six_documents(monkeypatch):
    """6 幣各產一筆 Document，meta.coin 正確、kind/source/name 正確。"""
    from trustforge.ingestion import defillama

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: PRICE_FIXTURE)
    docs = defillama.DefiLlamaPriceSource().fetch("", coin="")
    assert len(docs) == 6
    coins = {d.meta["coin"] for d in docs}
    assert coins == {"BTC", "ETH", "SOL", "BNB", "XRP", "ARB"}
    for d in docs:
        assert d.kind == "price_live"
        assert d.source == "defillama-price"
        assert "coins.llama.fi" in d.url
        assert d.ts > 0
        assert "content_reference" in d.meta


def test_price_source_single_coin_target(monkeypatch):
    """指定 coin 時只回該幣一筆。"""
    from trustforge.ingestion import defillama

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: PRICE_FIXTURE)
    docs = defillama.DefiLlamaPriceSource().fetch("", coin="eth")
    assert len(docs) == 1
    assert docs[0].meta["coin"] == "ETH"


def test_price_source_text_has_no_direction_word(monkeypatch):
    """現價文字只標價格數字，不附方向詞（DefiLlama 無 24h change；靠數字 token 進
    corroboration，不捏造方向）。"""
    from trustforge.ingestion import defillama

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: PRICE_FIXTURE)
    docs = defillama.DefiLlamaPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    text = docs[0].text
    assert "67000.5" in text
    for word in ("上漲", "下跌", "看漲", "看跌", "流入", "流出"):
        assert word not in text


def test_price_source_bad_data_produces_no_document(monkeypatch):
    """price=NaN / 缺 price 欄：一律不產 Document（現價是 Document 唯一理由，#24）。"""
    from trustforge.ingestion import defillama

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: PRICE_FIXTURE_BAD)
    docs = defillama.DefiLlamaPriceSource().fetch("", coin="")
    assert docs == [], f"壞 price 不應產生 Document，實得 {docs!r}"


def test_price_source_non_whitelisted_coin_skipped(monkeypatch):
    """非白名單幣種（如 DOGE）直接回 []，不串 URL。"""
    from trustforge.ingestion import defillama

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: PRICE_FIXTURE)
    assert defillama.DefiLlamaPriceSource().fetch("", coin="DOGE") == []


def test_price_source_coin_not_concatenated_into_url(monkeypatch):
    """coin 代碼不可直接拼進 URL path（path injection 防）：即使 coin 含特殊字元，
    URL 也只由白名單 coingecko id 組成。"""
    from trustforge.ingestion import defillama

    captured: list[str] = []
    monkeypatch.setattr(defillama, "_fetch_url", lambda url: (captured.append(url), PRICE_FIXTURE)[1])
    defillama.DefiLlamaPriceSource().fetch("", coin="BTC")
    assert captured
    # URL 只含 coingecko:bitcoin，不含原始 coin 代碼片段以外的注入
    assert "../" not in captured[0]
    assert captured[0].count("coingecko:") == 1


def test_price_source_failure_does_not_crash_collect(monkeypatch):
    """連接器逾時/例外 → collect 跳過該來源，不拋例外。"""
    from urllib.error import URLError
    from trustforge.ingestion import defillama, base

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: (_ for _ in ()).throw(URLError("timeout")))
    src = defillama.DefiLlamaPriceSource()
    docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)


# ── B. DefiLlamaTvlSource ─────────────────────────────────────────────────────

def test_tvl_source_only_produces_whitelisted_chains(monkeypatch):
    """chain 映射只產 ETH/SOL/BNB/ARB 4 筆；非白名單鏈（Tron）忽略。"""
    from trustforge.ingestion import defillama

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: TVL_FIXTURE)
    docs = defillama.DefiLlamaTvlSource().fetch("", coin="")
    coins = {d.meta["coin"] for d in docs}
    assert coins == {"ETH", "SOL", "BNB", "ARB"}
    assert len(docs) == 4
    for d in docs:
        assert d.kind == "defi_tvl"
        assert d.source == "defillama-tvl"
        assert "api.llama.fi/v2/chains" in d.url
        assert isinstance(d.meta["tvl"], float)
        assert d.meta["tvl"] > 0


def test_tvl_source_btc_xrp_return_empty(monkeypatch):
    """BTC/XRP 無意義 DeFi TVL → fetch() 回 []，不造假 near-zero（#24）。"""
    from trustforge.ingestion import defillama

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: TVL_FIXTURE)
    assert defillama.DefiLlamaTvlSource().fetch("", coin="BTC") == []
    assert defillama.DefiLlamaTvlSource().fetch("", coin="XRP") == []


def test_tvl_source_no_change_field_is_neutral(monkeypatch):
    """真實 /v2/chains 端點無 change_24h 欄 → 文字不附方向詞，direction 維持 neutral。"""
    from trustforge.ingestion import defillama
    from trustforge.trust.scoring import extract_claims

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: TVL_FIXTURE)
    docs = defillama.DefiLlamaTvlSource().fetch("", coin="ETH")
    assert len(docs) == 1
    text = docs[0].text
    for word in ("流入", "流出", "偏多", "偏空"):
        assert word not in text
    claims = extract_claims(docs)
    assert claims[0].direction == "neutral"


def test_tvl_source_change_field_attaches_direction_word(monkeypatch):
    """若 entry 含 change_24h 欄：正值→流入/偏多、負值→流出/偏空、零→持平、缺欄→中性。"""
    from trustforge.ingestion import defillama

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: TVL_FIXTURE_WITH_CHANGE)
    docs = defillama.DefiLlamaTvlSource().fetch("", coin="")
    by_coin = {d.meta["coin"]: d.text for d in docs}
    assert "流入" in by_coin["ETH"] and "偏多" in by_coin["ETH"]
    assert "流出" in by_coin["SOL"] and "偏空" in by_coin["SOL"]
    assert "持平" in by_coin["BNB"]
    # Arbitrum 無 change_24h → 中性（不出現方向詞）
    for word in ("流入", "流出", "偏多", "偏空"):
        assert word not in by_coin["ARB"]


def test_tvl_source_bad_data_produces_no_document(monkeypatch):
    """tvl=NaN / 缺 tvl 欄：不產 Document（#24 不造假 near-zero）。"""
    from trustforge.ingestion import defillama

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: TVL_FIXTURE_BAD)
    docs = defillama.DefiLlamaTvlSource().fetch("", coin="")
    assert docs == [], f"壞 tvl 不應產生 Document，實得 {docs!r}"


# ── C. no-false-divergence 防護 ────────────────────────────────────────────────

def test_defi_tvl_registered_in_objective_kinds():
    """defi_tvl 歸客觀類（市場事實），使進背離偵測的客觀分組。"""
    from trustforge.agent.orchestrator import OBJECTIVE_KINDS

    assert "defi_tvl" in OBJECTIVE_KINDS


def test_defi_tvl_reputation_is_0_85_via_per_doc_override(monkeypatch):
    """defi_tvl 信譽 0.85 透過 per-doc `meta["reputation"]` 覆寫生效（非登記進
    KIND_REPUTATION）——因為 `trustforge_core.scoring` 是 shadow runtime 受審候選核心，
    其原始檔 hash 被 `reviewed-shadow-candidate.v1.json` 固定釘住（CISO 安全工件），
    改它需重釘 digest + CISO review，超出本連接器範圍。per-doc 覆寫在 legacy 與候選
    核心兩條評分路徑都被採用（優先於 kind 預設），真實 score() 應得到 reputation=0.85。"""
    import time
    from trustforge.ingestion import defillama
    from trustforge.trust.scoring import extract_claims, score

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: TVL_FIXTURE)
    docs = defillama.DefiLlamaTvlSource().fetch("", coin="ETH")
    assert len(docs) == 1
    assert docs[0].meta["reputation"] == 0.85
    now = time.time()
    scored = score(extract_claims(docs), now=now + 1.0, dynamic_reputation=False, offline=True)
    assert len(scored) == 1
    assert scored[0].components.get("reputation") == 0.85, (
        f"defi_tvl per-doc reputation 覆寫應為 0.85，實得 {scored[0].components.get('reputation')}"
    )


def test_tvl_bullish_price_bearish_no_false_cross_source_divergence(monkeypatch):
    """no-false-divergence：defi_tvl（偏多，注入 change 欄）+ 價格偏空，兩者皆客觀類
    → detect_cross_source_signal 不製造假跨源背離（客觀類內部分歧 ≠ 客觀-情緒背離；
    無情緒類時回 None）。鎖死防回歸。"""
    from trustforge.ingestion import defillama
    from trustforge.trust.scoring import extract_claims, score
    from trustforge.agent.orchestrator import detect_cross_source_signal

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: TVL_FIXTURE_WITH_CHANGE)
    tvl_docs = defillama.DefiLlamaTvlSource().fetch("", coin="ETH")
    assert len(tvl_docs) == 1
    # ETH TVL 帶 change>0 → 文字含「流入/偏多」→ direction 應為 bullish
    tvl_claims = extract_claims(tvl_docs)
    assert tvl_claims[0].direction == "bullish", (
        f"注入正 change 的 TVL 應推斷 bullish，實得 {tvl_claims[0].direction}"
    )

    # 價格偏空客觀主張（獨立來源，方向 bearish）。
    from trustforge.ingestion.base import Document

    bearish_price = Document(
        id="price-bearish-eth", kind="price_live", source="coingecko-price",
        text="ETH 現價 3200 USD，24h 變動 -6.50%（下跌），市值 420,000,000,000 USD",
        ts=1_700_000_100.0, meta={"coin": "ETH"},
    )
    price_claims = extract_claims([bearish_price])
    assert price_claims[0].direction == "bearish"

    scored = score(tvl_claims + price_claims, now=1_700_000_200.0)
    result = detect_cross_source_signal(scored)
    # 客觀類內部分歧（tvl 偏多 vs 價格偏空）且無情緒類 → 不構成跨源背離訊號。
    assert result is None, (
        f"客觀類內部分歧不應被捏造成跨源背離訊號，實得 {result}"
    )


def test_tvl_without_direction_neutral_does_not_fabricate_divergence(monkeypatch):
    """真實端點無 change 欄 → TVL neutral；與偏空價格同為客觀類、無情緒類 → 不背離。"""
    from trustforge.ingestion import defillama
    from trustforge.ingestion.base import Document
    from trustforge.trust.scoring import extract_claims, score
    from trustforge.agent.orchestrator import detect_cross_source_signal

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: TVL_FIXTURE)
    tvl_docs = defillama.DefiLlamaTvlSource().fetch("", coin="ETH")
    tvl_claims = extract_claims(tvl_docs)
    assert tvl_claims[0].direction == "neutral"

    bearish_price = Document(
        id="price-bearish-eth-2", kind="price_live", source="coingecko-price",
        text="ETH 現價 3200 USD，24h 變動 -6.50%（下跌）",
        ts=1_700_000_100.0, meta={"coin": "ETH"},
    )
    scored = score(tvl_claims + extract_claims([bearish_price]), now=1_700_000_200.0)
    assert detect_cross_source_signal(scored) is None


# ── D. price corroboration consensus ──────────────────────────────────────────

def test_price_corroboration_coingecko_and_defillama_independent_sources(monkeypatch):
    """coingecko-price + defillama-price 同幣現價 → 兩個不同 source 互相獨立佐證：
    對 defillama-price 主張跑 `_corroboration_detail`，independent_sources 應含
    coingecko-price（反之亦然）。證明兩條獨立現價來源形成 corroboration consensus。"""
    from trustforge.ingestion import defillama, coingecko
    from trustforge.trust.scoring import extract_claims, _corroboration_detail

    coingecko.reset_process_cache()
    monkeypatch.setattr(
        coingecko, "_fetch_url",
        lambda url, extra_headers=None: json.dumps({
            "bitcoin": {"usd": 67823.45, "usd_market_cap": 1_330_000_000_000,
                        "usd_24h_change": 2.34, "last_updated_at": 1_700_000_000},
        }).encode(),
    )
    cg_docs = coingecko.CoinGeckoPriceSource().fetch("", coin="BTC")
    assert len(cg_docs) == 1

    monkeypatch.setattr(
        defillama, "_fetch_url",
        lambda url: json.dumps({
            "coins": {"coingecko:bitcoin": {"price": 67823.45, "symbol": "BTC",
                                            "timestamp": 1_700_000_000, "confidence": 0.99}},
        }).encode(),
    )
    dl_docs = defillama.DefiLlamaPriceSource().fetch("", coin="BTC")
    assert len(dl_docs) == 1

    claims = extract_claims(cg_docs + dl_docs)
    assert len(claims) == 2
    by_source = {c.doc.source: c for c in claims}
    assert set(by_source) == {"coingecko-price", "defillama-price"}

    # defillama 主張的獨立佐證來源應含 coingecko-price（不同 source、方向相容、內容重疊）。
    dl_indep, _ = _corroboration_detail(by_source["defillama-price"], claims)
    assert "coingecko-price" in dl_indep, (
        f"defillama-price 應被 coingecko-price 獨立佐證，實得 independent_sources={dl_indep}"
    )
    # 反向亦然。
    cg_indep, _ = _corroboration_detail(by_source["coingecko-price"], claims)
    assert "defillama-price" in cg_indep


# ── E. offline 樣本 + collect 接線 ─────────────────────────────────────────────

def test_offline_sample_source_reads_defi_tvl():
    """OfflineSampleSource('defi_tvl','defi_tvl') 讀到 demo 樣本（4 鏈）。"""
    from trustforge.ingestion.base import OfflineSampleSource

    src = OfflineSampleSource("defi_tvl", "defi_tvl")
    docs = src.fetch("", coin="ETH")
    assert len(docs) == 1
    assert docs[0].kind == "defi_tvl"
    assert docs[0].meta["coin"] == "ETH"
    # coin='' 回全部 4 鏈
    all_docs = src.fetch("", coin="")
    assert {d.meta["coin"] for d in all_docs} == {"ETH", "SOL", "BNB", "ARB"}


def test_build_defillama_sources_returns_two():
    from trustforge.ingestion.defillama import build_defillama_sources

    sources = build_defillama_sources()
    assert len(sources) == 2
    names = {s.name for s in sources}
    assert names == {"defillama-price", "defillama-tvl"}
    kinds = {s.kind for s in sources}
    assert kinds == {"price_live", "defi_tvl"}


def test_collect_online_includes_defillama_kinds(monkeypatch, tmp_path):
    """collect(offline=False, sources=None) 應把 build_defillama_sources() 併入，
    經 CachedSource 讀出後 price_live 與 defi_tvl kind 出現在結果中。"""
    import time
    from trustforge.ingestion import defillama, base
    from trustforge.ingestion import cache as cache_mod

    monkeypatch.setattr(defillama, "_fetch_url", lambda url: PRICE_FIXTURE)

    backend = cache_mod.JsonCacheBackend(tmp_path / "cache.json")
    monkeypatch.setattr(cache_mod, "get_cache_backend", lambda: backend)

    price_src, tvl_src = defillama.build_defillama_sources()
    backend.set(
        cache_mod.cache_key(price_src.name, "BTC"),
        [cache_mod.doc_to_dict(d) for d in price_src.fetch("BTC", coin="BTC")],
        fetched_at=time.time(),
    )
    monkeypatch.setattr(defillama, "_fetch_url", lambda url: TVL_FIXTURE)
    backend.set(
        cache_mod.cache_key(tvl_src.name, "ETH"),
        [cache_mod.doc_to_dict(d) for d in tvl_src.fetch("ETH", coin="ETH")],
        fetched_at=time.time(),
    )

    # price_live（defillama-price 沿用）與 defi_tvl 都應讀得到。
    btc_docs = base.collect("BTC", coin="BTC", offline=False)
    assert any(d.kind == "price_live" and d.source == "defillama-price" for d in btc_docs)
    eth_docs = base.collect("ETH", coin="ETH", offline=False)
    assert any(d.kind == "defi_tvl" and d.source == "defillama-tvl" for d in eth_docs)


def test_collect_online_cache_miss_defillama_degrades_gracefully(monkeypatch, tmp_path):
    """未預先寫入 cache 時，DefiLlama 來源不應反過來呼叫真 _fetch_url，而是優雅降級。"""
    from trustforge.ingestion import defillama, base

    def _boom(url):  # pragma: no cover - 不應被呼叫到
        raise AssertionError(f"CachedSource 不該打真連接器 API：{url}")

    monkeypatch.setattr(defillama, "_fetch_url", _boom)
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path / "cache"))

    failed: list = []
    docs = base.collect("BTC", coin="BTC", offline=False, _failed=failed)
    kinds = {d.kind for d in docs}
    assert "defi_tvl" not in kinds
    assert "defillama-price" in failed
    assert "defillama-tvl" in failed
