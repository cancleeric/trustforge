"""#1161 CoinMarketCap 連接器整合測試 — CI 不打真網路（monkeypatch cmc._fetch_url
與 cmc.resolve_api_key）。

涵蓋：
  - URL 白名單含 6 symbol、parse 6 Document、meta.coin、kind/source 正確。
  - key 走 header（非 URL query）——security 鐵律。
  - 壞資料（NaN/缺 price 欄）→ 不產 Document（#24 不造假）。
  - 無 key → 靜默降級回 []（不報錯、不打網路）。
  - 24h 漲跌幅方向詞（上漲/下跌/持平/N/A）。
  - price corroboration consensus：coingecko-price + defillama-price + coinmarketcap-price
    同幣同向 → 三源互相獨立佐證。
  - build_cmc_sources 有 key→回來源；無 key→回 []。
"""
from __future__ import annotations

import json

import pytest

# ── 固定 fixture（模擬 CoinMarketCap quotes/latest 回應）──────────────────────────

# 6 幣各一筆（data.<SYMBOL>.quote.USD.{price, market_cap, market_cap_dominance,
# percent_change_24h}）。
PRICE_FIXTURE = json.dumps({
    "data": {
        "BTC": {"symbol": "BTC", "quote": {"USD": {
            "price": 67000.5, "market_cap": 1.33e12,
            "market_cap_dominance": 52.30, "percent_change_24h": 2.34,
            "last_updated": "2024-01-01T00:00:00.000Z",
        }}},
        "ETH": {"symbol": "ETH", "quote": {"USD": {
            "price": 3521.1, "market_cap": 4.2e11,
            "market_cap_dominance": 17.10, "percent_change_24h": 1.50,
            "last_updated": "2024-01-01T00:00:00.000Z",
        }}},
        "SOL": {"symbol": "SOL", "quote": {"USD": {
            "price": 172.5, "market_cap": 8.0e10,
            "market_cap_dominance": 3.20, "percent_change_24h": -3.10,
            "last_updated": "2024-01-01T00:00:00.000Z",
        }}},
        "BNB": {"symbol": "BNB", "quote": {"USD": {
            "price": 610.2, "market_cap": 9.0e10,
            "market_cap_dominance": 4.00, "percent_change_24h": 0.0,
            "last_updated": "2024-01-01T00:00:00.000Z",
        }}},
        "XRP": {"symbol": "XRP", "quote": {"USD": {
            "price": 0.62, "market_cap": 3.4e10,
            "market_cap_dominance": 1.40, "percent_change_24h": -0.50,
            "last_updated": "2024-01-01T00:00:00.000Z",
        }}},
        "ARB": {"symbol": "ARB", "quote": {"USD": {
            "price": 1.18, "market_cap": 4.0e9,
            "market_cap_dominance": 0.20, "percent_change_24h": 5.00,
            "last_updated": "2024-01-01T00:00:00.000Z",
        }}},
    }
}).encode()

# 壞資料：BTC price=NaN、ETH 完全缺 price 欄 → 兩幣都不應產 Document。
PRICE_FIXTURE_BAD = json.dumps({
    "data": {
        "BTC": {"symbol": "BTC", "quote": {"USD": {
            "price": float("nan"), "percent_change_24h": 2.0,
            "last_updated": "2024-01-01T00:00:00.000Z",
        }}},
        "ETH": {"symbol": "ETH", "quote": {"USD": {  # 缺 price
            "market_cap": 4.2e11, "percent_change_24h": 1.5,
            "last_updated": "2024-01-01T00:00:00.000Z",
        }}},
        "SOL": {"symbol": "SOL", "quote": {"USD": {
            "price": 172.5, "percent_change_24h": -3.1,
            "last_updated": "2024-01-01T00:00:00.000Z",
        }}},
    }
}).encode()


def _patch_key(monkeypatch, key="controlled-cmc-key-1234567890"):
    """讓 connector 認為有 key（不打真 SSM/env）。"""
    monkeypatch.setattr(
        "trustforge.ingestion.cmc.resolve_api_key",
        lambda: (key, "ssm"),
    )


# ── A. CoinMarketCapPriceSource 基本解析 ────────────────────────────────────────

def test_price_source_url_contains_all_whitelisted_symbols(monkeypatch):
    """coin='' 時 URL 含全部 6 個白名單 symbol（一次呼叫涵蓋多幣）。"""
    from trustforge.ingestion import cmc

    _patch_key(monkeypatch)
    captured: list[str] = []

    def _capture(url, extra_headers=None):
        captured.append(url)
        return PRICE_FIXTURE

    monkeypatch.setattr(cmc, "_fetch_url", _capture)
    cmc.CoinMarketCapPriceSource().fetch("", coin="")
    assert len(captured) == 1
    url = captured[0]
    assert url.startswith("https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest")
    for sym in ("BTC", "ETH", "SOL", "BNB", "XRP", "ARB"):
        assert sym in url, f"URL 缺白名單 symbol {sym}：{url}"


def test_price_source_parses_six_documents(monkeypatch):
    """6 幣各產一筆 Document，meta.coin 正確、kind/source/name 正確。"""
    from trustforge.ingestion import cmc

    _patch_key(monkeypatch)
    monkeypatch.setattr(cmc, "_fetch_url", lambda url, extra_headers=None: PRICE_FIXTURE)
    docs = cmc.CoinMarketCapPriceSource().fetch("", coin="")
    assert len(docs) == 6
    coins = {d.meta["coin"] for d in docs}
    assert coins == {"BTC", "ETH", "SOL", "BNB", "XRP", "ARB"}
    for d in docs:
        assert d.kind == "price_live"
        assert d.source == "coinmarketcap-price"
        assert "pro-api.coinmarketcap.com" in d.url
        assert d.ts > 0
        assert "content_reference" in d.meta


def test_price_source_single_coin_target(monkeypatch):
    """指定 coin 時只回該幣一筆。"""
    from trustforge.ingestion import cmc

    _patch_key(monkeypatch)
    monkeypatch.setattr(cmc, "_fetch_url", lambda url, extra_headers=None: PRICE_FIXTURE)
    docs = cmc.CoinMarketCapPriceSource().fetch("", coin="eth")
    assert len(docs) == 1
    assert docs[0].meta["coin"] == "ETH"


def test_price_source_key_goes_in_header_not_url(monkeypatch):
    """security：key 透過 header X-CMC_PRO_API_KEY 傳遞，URL 全程乾淨、不含 key。"""
    from trustforge.ingestion import cmc

    _patch_key(monkeypatch, key="secret-cmc-key-abcdef123456")
    observed = {}

    def fake_fetch(url, extra_headers=None):
        observed["url"] = url
        observed["extra_headers"] = extra_headers
        return PRICE_FIXTURE

    monkeypatch.setattr(cmc, "_fetch_url", fake_fetch)
    docs = cmc.CoinMarketCapPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    assert observed["extra_headers"] == {"X-CMC_PRO_API_KEY": "secret-cmc-key-abcdef123456"}
    assert "secret-cmc-key-abcdef123456" not in observed["url"]
    assert "X-CMC_PRO_API_KEY" not in observed["url"]


def test_price_source_change_24h_direction_words(monkeypatch):
    """24h 漲跌幅附方向詞：正值→上漲、負值→下跌、零→持平、NaN→N/A。"""
    from trustforge.ingestion import cmc

    _patch_key(monkeypatch)
    monkeypatch.setattr(cmc, "_fetch_url", lambda url, extra_headers=None: PRICE_FIXTURE)
    docs = cmc.CoinMarketCapPriceSource().fetch("", coin="")
    by_coin = {d.meta["coin"]: d.text for d in docs}
    assert "上漲" in by_coin["BTC"]   # +2.34
    assert "上漲" in by_coin["ETH"]   # +1.50
    assert "下跌" in by_coin["SOL"]   # -3.10
    assert "持平" in by_coin["BNB"]   # 0.0
    assert "下跌" in by_coin["XRP"]   # -0.50
    assert "上漲" in by_coin["ARB"]   # +5.00


def test_price_source_uses_quote_usd_last_updated_not_fallback_now(monkeypatch):
    """timestamp 來自 quote.USD.last_updated（CMC 真實位置，非 currency entry）：
    fixture 的 entry 沒有 last_updated、只有 quote.USD.last_updated=2024-01-01T00:00:00Z
    （epoch 1704067200）。若誤讀 entry.last_updated → 缺欄 → fallback 成 time.time()
    （≈現在，遠大於 1704067200）→ 過時價格被誤判新鮮、recency 膨脹。本測鎖住
    「採用 quote.USD.last_updated」：doc.ts 必須精確等於該 epoch，而非 fallback_now。"""
    from trustforge.ingestion import cmc

    _patch_key(monkeypatch)
    monkeypatch.setattr(cmc, "_fetch_url", lambda url, extra_headers=None: PRICE_FIXTURE)
    docs = cmc.CoinMarketCapPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    # quote.USD.last_updated="2024-01-01T00:00:00.000Z" → epoch 1704067200.0；
    # entry 無 last_updated，舊邏輯會 fallback 成 time.time()（本測現在會 fail）。
    assert docs[0].ts == 1704067200.0


def test_price_source_market_cap_is_text_context_only(monkeypatch):
    """market_cap/dominance 當文字 context，不另立 dimension（避免假背離）。"""
    from trustforge.ingestion import cmc

    _patch_key(monkeypatch)
    monkeypatch.setattr(cmc, "_fetch_url", lambda url, extra_headers=None: PRICE_FIXTURE)
    docs = cmc.CoinMarketCapPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    text = docs[0].text
    assert "市值" in text   # market_cap 寫進文字
    assert "市佔" in text   # dominance 寫進文字
    # 只有一個 dimension（price_live），不另立 market_cap/dominance kind。
    assert docs[0].kind == "price_live"


def test_price_source_bad_data_produces_no_document(monkeypatch):
    """price=NaN / 缺 price 欄：一律不產 Document（現價是 Document 唯一理由，#24）。"""
    from trustforge.ingestion import cmc

    _patch_key(monkeypatch)
    monkeypatch.setattr(cmc, "_fetch_url", lambda url, extra_headers=None: PRICE_FIXTURE_BAD)
    docs = cmc.CoinMarketCapPriceSource().fetch("", coin="")
    # BTC(NaN) + ETH(缺 price) 跳過；SOL(price=172.5) 仍產。
    coins = {d.meta["coin"] for d in docs}
    assert coins == {"SOL"}, f"壞 price 不應產生 Document，實得 {coins!r}"


def test_price_source_non_whitelisted_coin_skipped(monkeypatch):
    """非白名單幣種（如 DOGE）直接回 []，不串 URL。"""
    from trustforge.ingestion import cmc

    _patch_key(monkeypatch)
    monkeypatch.setattr(cmc, "_fetch_url", lambda url, extra_headers=None: PRICE_FIXTURE)
    assert cmc.CoinMarketCapPriceSource().fetch("", coin="DOGE") == []


def test_price_source_coin_not_concatenated_into_url(monkeypatch):
    """URL 只由寫死白名單 symbol 常數組成，coin 代碼不直接拼進 URL
    （path/query injection 防）。"""
    from trustforge.ingestion import cmc

    _patch_key(monkeypatch)
    captured: list[str] = []
    monkeypatch.setattr(
        cmc, "_fetch_url",
        lambda url, extra_headers=None: (captured.append(url), PRICE_FIXTURE)[1],
    )
    cmc.CoinMarketCapPriceSource().fetch("", coin="BTC")
    assert captured
    url = captured[0]
    # URL 永遠是完整 6 幣白名單常數，不受 coin 參數影響。
    assert url.count("BTC,ETH,SOL,BNB,XRP,ARB") == 1


def test_price_source_no_key_silent_degrade(monkeypatch):
    """無 key → resolve_api_key 回 (None, ...) → fetch() 直接回 []（靜默降級），
    不報錯、不打網路。"""
    from trustforge.ingestion import cmc

    monkeypatch.setattr("trustforge.ingestion.cmc.resolve_api_key", lambda: (None, "unconfigured"))

    def _boom(url, extra_headers=None):  # pragma: no cover - 不應被呼叫
        raise AssertionError(f"無 key 不應打網路：{url}")

    monkeypatch.setattr(cmc, "_fetch_url", _boom)
    assert cmc.CoinMarketCapPriceSource().fetch("", coin="BTC") == []


def test_price_source_unavailable_raises_for_observability(monkeypatch):
    """codex P1（可觀測性）：已配置憑證但 SSM/網路暫失敗
    （resolve_api_key 回 (None,"unavailable")）→ fetch() raise RuntimeError，讓
    排程器 catch+log 並計入 failures（fetch_scheduler 對 source.fetch() 例外是
    catch+log，不會 crash）——避免隱形憑證中斷。對比未配置 (None,"unconfigured")
    仍靜默回 []（非故障，見 test_price_source_no_key_silent_degrade）。"""
    from trustforge.ingestion import cmc

    monkeypatch.setattr(
        "trustforge.ingestion.cmc.resolve_api_key", lambda: (None, "unavailable")
    )

    def _boom(url, extra_headers=None):  # pragma: no cover - 不應被呼叫
        raise AssertionError(f"憑證取不到不應打網路：{url}")

    monkeypatch.setattr(cmc, "_fetch_url", _boom)
    with pytest.raises(RuntimeError, match="unavailable"):
        cmc.CoinMarketCapPriceSource().fetch("", coin="BTC")


def test_price_source_failure_does_not_crash_collect(monkeypatch):
    """連接器逾時/例外 → collect 跳過該來源，不拋例外。"""
    from urllib.error import URLError
    from trustforge.ingestion import base, cmc

    _patch_key(monkeypatch)
    monkeypatch.setattr(
        cmc, "_fetch_url", lambda url, extra_headers=None: (_ for _ in ()).throw(URLError("timeout"))
    )
    src = cmc.CoinMarketCapPriceSource()
    docs = base.collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)


# ── B. build_cmc_sources ──────────────────────────────────────────────────────

def test_build_cmc_sources_returns_source_when_key_present(monkeypatch):
    """有 key → build_cmc_sources() 回 [CoinMarketCapPriceSource()]。"""
    from trustforge.ingestion import cmc

    monkeypatch.setattr(
        "trustforge.ingestion.cmc.resolve_api_key",
        lambda: ("controlled-cmc-key-1234567890", "ssm"),
    )
    sources = cmc.build_cmc_sources()
    assert len(sources) == 1
    assert sources[0].name == "coinmarketcap-price"
    assert sources[0].kind == "price_live"


def test_build_cmc_sources_always_registers_even_without_key(monkeypatch):
    """codex P1（可觀測性）：build_cmc_sources() 永遠註冊來源（同
    build_whale_sources 慣例），不在 build-time resolve 憑證。即使完全未配置
    憑證也回 [CoinMarketCapPriceSource()]——避免 SSM 暫時不可用時 source 從
    registry 消失、憑證中斷變隱形（排程器不跑、cache 無聲過期）。憑證狀態改在
    fetch() 才決定。"""
    from trustforge.ingestion import cmc

    monkeypatch.setattr("trustforge.ingestion.cmc.resolve_api_key", lambda: (None, "unconfigured"))
    sources = cmc.build_cmc_sources()
    assert len(sources) == 1
    assert sources[0].name == "coinmarketcap-price"

    # 已配置但暫時取不到（SSM/網路失敗）→ 同樣必須註冊，失敗留到 fetch() 才可觀測。
    monkeypatch.setattr("trustforge.ingestion.cmc.resolve_api_key", lambda: (None, "unavailable"))
    sources = cmc.build_cmc_sources()
    assert len(sources) == 1
    assert sources[0].name == "coinmarketcap-price"


# ── C. price corroboration consensus（三源同向共識）────────────────────────────

def test_price_corroboration_three_sources_independent(monkeypatch):
    """coingecko-price + defillama-price + coinmarketcap-price 同幣現價 → 三個不同
    source 互相獨立佐證：對 coinmarketcap-price 主張跑 `_corroboration_detail`，
    independent_sources 應含 coingecko-price 與 defillama-price。"""
    from trustforge.ingestion import cmc, coingecko, defillama
    from trustforge.trust.scoring import extract_claims, _corroboration_detail

    # CMC（本 connector）
    monkeypatch.setattr(
        "trustforge.ingestion.cmc.resolve_api_key",
        lambda: ("controlled-cmc-key-1234567890", "ssm"),
    )
    monkeypatch.setattr(
        cmc, "_fetch_url",
        lambda url, extra_headers=None: json.dumps({
            "data": {"BTC": {"quote": {"USD": {
                "price": 67823.45, "market_cap": 1.33e12,
                "market_cap_dominance": 52.3, "percent_change_24h": 2.34,
                "last_updated": "2024-01-01T00:00:00.000Z",
            }}}},
        }).encode(),
    )
    cmc_docs = cmc.CoinMarketCapPriceSource().fetch("", coin="BTC")
    assert len(cmc_docs) == 1

    # CoinGecko（同向：+2.34% 上漲）
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

    # DefiLlama（中性方向，靠價格數字 token 共識）
    monkeypatch.setattr(
        defillama, "_fetch_url",
        lambda url: json.dumps({
            "coins": {"coingecko:bitcoin": {"price": 67823.45, "symbol": "BTC",
                                            "timestamp": 1_700_000_000, "confidence": 0.99}},
        }).encode(),
    )
    dl_docs = defillama.DefiLlamaPriceSource().fetch("", coin="BTC")
    assert len(dl_docs) == 1

    claims = extract_claims(cmc_docs + cg_docs + dl_docs)
    assert len(claims) == 3
    by_source = {c.doc.source: c for c in claims}
    assert set(by_source) == {"coinmarketcap-price", "coingecko-price", "defillama-price"}

    # CMC（豐富文字：現價+市值+市佔+漲跌）與 coingecko（同樣豐富）token 重疊高
    # → coingecko 佐證 CMC。corroboration 的 overlap 閘是 target-relative
    # （intersection / len(target_tokens) >= 0.4）：CMC 文字 token 多，coingecko
    # 文字 token 結構相近 → 重疊比例高，通過。
    cmc_indep, _ = _corroboration_detail(by_source["coinmarketcap-price"], claims)
    assert "coingecko-price" in cmc_indep, (
        f"coinmarketcap-price 應被 coingecko-price 獨立佐證，實得 {cmc_indep}"
    )

    # defillama（稀疏文字「現價 X USD」）作 target 時，CMC 的豐富文字涵蓋了
    # defillama 的全部 token → ratio≈1.0 通過閘 → CMC 佐證 defillama。證明三條
    # 獨立現價來源在同一 corroboration cluster 內互相佐證（defillama 在此扮演
    # 「稀疏目標被豐富候選涵蓋」的角色，與 test_defillama.py 既有範式一致）。
    dl_indep, _ = _corroboration_detail(by_source["defillama-price"], claims)
    assert "coinmarketcap-price" in dl_indep, (
        f"defillama-price 應被 coinmarketcap-price 獨立佐證，實得 {dl_indep}"
    )
    assert "coingecko-price" in dl_indep


# ── D. collect 接線（COIN_KEYED_BATCH）─────────────────────────────────────────

def test_collect_online_cache_miss_cmc_degrades_gracefully(monkeypatch, tmp_path):
    """未預先寫入 cache 時，CMC 來源不應反過來呼叫真 _fetch_url，而是優雅降級。"""
    from trustforge.ingestion import cmc, base

    _patch_key(monkeypatch)

    def _boom(url, extra_headers=None):  # pragma: no cover - 不應被呼叫
        raise AssertionError(f"CachedSource 不該打真連接器 API：{url}")

    monkeypatch.setattr(cmc, "_fetch_url", _boom)
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path / "cache"))

    failed: list = []
    docs = base.collect("BTC", coin="BTC", offline=False, _failed=failed)
    kinds = {d.kind for d in docs}
    assert "price_live" not in kinds or not any(
        d.source == "coinmarketcap-price" for d in docs
    )
    assert "coinmarketcap-price" in failed
