"""CoinMarketCap key-based 資料連接器（#1161，CEO 審核 gray 計劃）。

CoinMarketCap（CMC）Pro API 提供即時加密貨幣報價，用來：
  - **強化 price 交叉佐證**（`CoinMarketCapPriceSource`，kind=`price_live`）：第三條
    獨立的即時現價來源，與 `coingecko-price` / `defillama-price` 同幣現價在
    corroboration 機制裡形成共識（三個不同 source 對同一價格數字的獨立佐證，
    提升該現價的信任分）。

認證方式（security，harper 雙審重點）：
  CMC 是 key-based API。API key 一律透過 HTTP header `X-CMC_PRO_API_KEY`
  傳遞（官方指定的認證方式），**絕不**附加在 URL query param 上——避免
  proxy/tracing/access-log/例外訊息等常見「記錄請求 URL」路徑 side-channel
  側錄外洩（key 走 header 非 query，URL 全程乾淨）。key 由 `cmc_secret`
   模組解析（SSM SecureString 優先 → 本機檔 0o600 → env），本檔不經手
   key 的儲存/管理。**未配置憑證時靜默降級**（回 []，不報錯、不造假）；
   已配置但 SSM/網路暫失敗時 `fetch()` 改 raise（見 `build_cmc_sources`）。

來源白名單（寫死，防 SSRF；比照 `defillama.py`/`coingecko.py` 慣例）：
  - 現價（一次呼叫涵蓋全部 6 幣）：
      GET https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest
          ?symbol=BTC,ETH,SOL,BNB,XRP,ARB
      header: X-CMC_PRO_API_KEY: <key>
      → `{"data": {"BTC": {"quote": {"USD": {price, market_cap,
          market_cap_dominance, percent_change_24h, ...}}, ...}, ...}}`
    URL 的 query 段一律只由寫死白名單的 6 幣 symbol 常數組成，coin 代碼本身
    絕不直接拼進 URL（與 defillama.py 的 path injection 防護同理）。

誠實不造假（#24）：
  - 現價數值一律過 `_finite_num`（擋 NaN/inf/負/零/非數值/bool）；不合格的幣
    **不產 Document**（現價是該 Document 存在的唯一理由，壞值沒有「退化成 N/A」
    的意義——退化後仍是看似合理但實則無效的客觀觀測，會污染背離偵測）。
  - market_cap / market_cap_dominance 當 **文字 context** 寫進去，**不**另立
    dimension（避免假背離——見 defillama.py 的 caution 先例：客觀類內部若因
    不同面向被當成對立方向，會被捏造成跨源背離訊號）。這兩個數字只作為現價
    觀測的背景脈絡，不影響 direction。
  - 24h 漲跌幅附方向詞（正→上漲/負→下跌），使其進 corroboration（與
    coingecko-price / defillama-price 同向共識）；NaN/inf/缺欄退回 N/A、
    不下方向判斷（不捏造方向）。

安全措施（同 defillama.py / coingecko.py 慣例）：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）/ 固定 User-Agent。
  - 不接受外部傳入 URL；URL 只由本檔內建白名單常數組成。
  - SSRF-safe fetch（見 `safe_fetch.py`）：逐跳驗證（含初始 URL）scheme/hostname/
    port/私有 IP，DNS pinning，禁自動跟轉——`_fetch_url` 是本模組唯一外呼出口。
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone

from . import safe_fetch
from ..cmc_secret import resolve_api_key
from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"

# 時間戳合理範圍下限（同 coingecko.py/defillama.py 慣例，只用來擋明顯異常值如 0/負值）。
_MIN_PLAUSIBLE_EPOCH = 1_577_836_800.0  # 2020-01-01T00:00:00Z

# 6 幣白名單（與 base.py / COIN_POOL 一致）。
_SUPPORTED_COINS = frozenset({"BTC", "ETH", "SOL", "BNB", "XRP", "ARB"})

# 現價端點（寫死常數，防 SSRF）：一次呼叫涵蓋全部 6 幣，URL 全程不含任何 secret。
# symbol 段只由白名單 6 幣代碼組成，coin 代碼本身絕不直接由外部輸入拼進 URL。
_PRICE_URL = (
    "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
    "?symbol=BTC,ETH,SOL,BNB,XRP,ARB"
)


def _finite_num(
    v: object,
    lo: float | None = None,
    hi: float | None = None,
    exclusive_lo: bool = False,
) -> float | None:
    """數值欄位共用有限驗證（同 defillama.py::_finite_num，呼應 #24 不造假）：
    有限數字（排除 bool/非數值/NaN/inf），選用值域檢查；不合格一律回 None。

    - `bool` 是 `int` 子類但語意上不是數字，明確排除。
    - `NaN`/`inf`/`-inf` 一律視為不可用（`>`/`<` 比較會悄悄吃掉，落入某個分支
      被誤判成看似合理的觀測，等於把壞資料捏造成訊號）。
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    fv = float(v)
    if not math.isfinite(fv):
        return None
    if lo is not None:
        if exclusive_lo and fv <= lo:
            return None
        if not exclusive_lo and fv < lo:
            return None
    if hi is not None and fv > hi:
        return None
    return fv


def _parse_ts(value: object, fallback_now: float) -> float:
    """把 CMC 回應的 ISO-8601 時間戳（如 "2024-01-01T00:00:00.000Z"）解析為
    epoch 秒；只接受「合理範圍內的過去 epoch」（不早於 `_MIN_PLAUSIBLE_EPOCH`，
    不晚於 `fallback_now + 時鐘偏差容忍`，擋未來戳灌 recency 到滿分，同
    coingecko.py 慣例）。解析失敗/缺欄/壞值一律退回 `fallback_now`（真實、
    有限、非未來的本地呼叫當下時間，不捏造鮮度）。"""
    if not isinstance(value, str) or not value.strip():
        return fallback_now
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return fallback_now
    if parsed.tzinfo is None:
        # CMC 一律回帶時區的 ISO 字串；缺時區視為壞值，不擅自假設 UTC。
        return fallback_now
    ts = parsed.astimezone(timezone.utc).timestamp()
    if not math.isfinite(ts) or ts < _MIN_PLAUSIBLE_EPOCH:
        return fallback_now
    return ts


def _fetch_url(url: str, extra_headers: dict[str, str] | None = None) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 SSRF-safe GET（見 `safe_fetch.py`）。

    本模組所有真 HTTP 請求唯一的出口：CMC 現價端點不論呼叫端是誰，送出請求
    一律經過這裡交給 SSRF-safe 的 `safe_fetch.fetch_url` 實際送出。

    `extra_headers`（含 API key header `X-CMC_PRO_API_KEY`）與固定 `User-Agent`
    一併附加在請求 header 上；URL 本身不受影響、一律保持乾淨（不含任何 secret）。
    """
    return safe_fetch.fetch_url(
        url, user_agent=_UA, extra_headers=extra_headers,
        timeout=_TIMEOUT, max_bytes=_MAX_BYTES,
    )


class CoinMarketCapPriceSource(Source):
    """CoinMarketCap 即時現價（pro-api quotes/latest，key-based，一次呼叫涵蓋 6 幣）。

    作為 `coingecko-price` / `defillama-price` 以外的**第三條獨立現價來源**，與之
    形成 corroboration consensus（同幣現價被三個不同 source 各自報出 → 獨立佐證 →
    該現價信任分進一步提升）。

    **憑證在 fetch 時解析**（`build_cmc_sources` 永遠註冊，不在 build-time 決定）。
    `resolve_api_key()` 回 `(None, "unconfigured")`（完全未設）→ `fetch()` 回 []
    （靜默降級：不報錯、不造假、不打網路）；回 `(None, "unavailable")`（已配置
    但 SSM/網路暫失敗）→ `fetch()` raise RuntimeError（排程器 catch+log 並計入
    failures，可觀測——見下方 `build_cmc_sources` 與 `fetch_scheduler` 說明）。

    `coin` 指定單一目標時只回該幣；`coin` 為空時回白名單 6 幣各一筆，皆帶顯式
    `meta["coin"]`。非白名單幣種一律跳過（回 []）。

    market_cap / market_cap_dominance 當 **文字 context** 寫進 `ref`，**不**另立
    dimension（避免假背離，見模組頂部說明）；這兩個數字不影響 direction。
    """

    kind = "price_live"
    name = "coinmarketcap-price"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        targets = [coin.upper()] if coin else sorted(_SUPPORTED_COINS)
        targets = [t for t in targets if t in _SUPPORTED_COINS]
        if not targets:
            return []
        # 憑證在 fetch 時解析（build_cmc_sources 永遠註冊、不在 build-time 決定，
        # 見該函式 docstring 的可觀測性理由）。兩種「無 key」情況刻意分開（codex P1）：
        #   - "unconfigured"（完全未設憑證）→ 回 []（非故障，靜默降級：排程器/
        #     collect 不把它當故障，也不會嘗試打需 key 的 API）。
        #   - "unavailable"（已配置但 SSM/網路/解密暫失敗）→ raise RuntimeError。
        #     fetch_scheduler 對 source.fetch() 例外一律 catch+log 並計入 failures
        #     （見 fetch_scheduler.py COIN_KEYED_BATCH 分支，本來源屬該集合），
        #     不會 crash——這讓「來源仍在排程、只是憑證暫取不到」對 cron/監控
        #     可見，避免 build-time 消失造成的隱形憑證中斷。
        key, key_source = resolve_api_key()
        if not key:
            if key_source == "unavailable":
                raise RuntimeError(
                    "CMC credential unavailable (configured but SSM/network failed)"
                )
            return []
        # key 走 header（security：非 query），URL 全程乾淨。
        raw = _fetch_url(_PRICE_URL, extra_headers={"X-CMC_PRO_API_KEY": key})
        data = json.loads(raw)
        if not isinstance(data, dict):
            return []
        data_section = data.get("data")
        if not isinstance(data_section, dict):
            return []
        fallback_now = time.time()
        docs: list[Document] = []
        for code in targets:
            entry = data_section.get(code)
            if not isinstance(entry, dict):
                continue
            quote = entry.get("quote")
            if not isinstance(quote, dict):
                continue
            usd = quote.get("USD")
            if not isinstance(usd, dict):
                continue
            # 現價是這個 Document 存在的唯一理由，壞值（NaN/inf/負/零/非數值）
            # 直接跳過該幣——不退化成 N/A 續產（price_live 是 OBJECTIVE_KINDS，
            # 壞現價會污染 detect_cross_source_signal，#24）。
            price = _finite_num(usd.get("price"), lo=0.0, exclusive_lo=True)
            if price is None:
                continue
            # market_cap / dominance 當文字 context（非獨立 dimension，避免假背離）。
            mcap = _finite_num(usd.get("market_cap"), lo=0.0)
            mcap_str = f"{mcap:,.0f}" if mcap is not None else "N/A"
            dominance = _finite_num(usd.get("market_cap_dominance"), lo=0.0, hi=100.0)
            dom_str = f"{dominance:.2f}%" if dominance is not None else "N/A"
            # 24h 漲跌幅附方向詞（正→上漲/負→下跌）使其進 corroboration（與
            # coingecko-price/defillama-price 同向共識）；NaN/inf/缺欄退回 N/A、
            # 不下方向判斷（不捏造方向，#24）。
            change = _finite_num(usd.get("percent_change_24h"))
            if change is not None:
                if change > 0:
                    change_str = f"{change:+.2f}%（上漲）"
                elif change < 0:
                    change_str = f"{change:+.2f}%（下跌）"
                else:
                    change_str = f"{change:+.2f}%（持平）"
            else:
                change_str = "N/A"
            # 時間戳：CMC 把 last_updated 放在 quote.USD 下（非 currency entry），
            # 即 data.<SYMBOL>.quote.USD.last_updated——這才是該現價的真實鮮度來源。
            # 只接受合理過去 epoch 並 clamp <= fallback_now（擋未來戳灌 recency，同
            # coingecko.py 慣例）；usd 無 last_updated 才退回 fallback_now（真實、有限、
            # 非未來）——避免 entry.last_updated 缺欄導致每筆都 fallback 成「剛剛」，
            # 把過時價格誤判新鮮、膨脹 recency trust。
            raw_ts = _parse_ts(usd.get("last_updated"), fallback_now)
            ts = min(raw_ts, fallback_now)
            ref = (
                f"{code} 現價 {price} USD，24h 變動 {change_str}，"
                f"市值 {mcap_str} USD，市佔 {dom_str}"
            )
            doc_id = "coinmarketcap-price-" + hashlib.md5(f"{code}-{ref}".encode()).hexdigest()[:12]
            docs.append(Document(
                id=doc_id,
                kind=self.kind,
                source=self.name,
                text=ref,
                url=_PRICE_URL,
                ts=ts,
                meta={"content_reference": ref, "coin": code},
            ))
        return docs


def build_cmc_sources() -> list[Source]:
    """永遠註冊 CoinMarketCap 現價連接器（同 `build_whale_sources()` 慣例）。

    **不在 build-time resolve 憑證**——憑證解析延後到 `fetch()`。理由（codex P1，
    可觀測性）：若在 build-time 呼叫 `resolve_api_key()`，SSM 暫時不可用（權限/
    網路/解密失敗）時回 `(None, "unavailable")` → build 回 [] → source 從 registry
    消失 → 排程器根本不會跑它 → 憑證中斷變成隱形失敗（cron/監控看不到、cache
    無聲過期、產品端斷料才被發現）。永遠註冊後，憑證狀態在 `fetch()` 時才決定：
      - "unconfigured"（完全未設）→ `fetch()` 回 []（非故障，靜默降級）。
      - "unavailable"（已配置但 SSM/網路暫失敗）→ `fetch()` raise RuntimeError，
        排程器 catch+log 並計入 failures（可觀測失敗）。
    """
    return [CoinMarketCapPriceSource()]
