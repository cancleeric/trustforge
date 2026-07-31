"""DefiLlama keyless 資料連接器（#1162，CEO 審核 gray 計劃）。

DefiLlama 提供完全免 key 的公開 API，用來：
  1. **強化 price 交叉佐證**（`DefiLlamaPriceSource`，kind=`price_live`）：第二條
     獨立的即時現價來源，與 `coingecko-price` 同幣現價在 corroboration 機制裡
     形成共識（兩個不同 source 對同一價格數字的獨立佐證，提升該現價的信任分）。
  2. **新增 TVL 維度**（`DefiLlamaTvlSource`，kind=`defi_tvl`）：DeFi 鎖倉量是
     客觀市場事實（OBJECTIVE_KINDS），提供價格以外的另一個客觀面向。

來源白名單（寫死，防 SSRF；比照 `coingecko.py`/`onchain.py` 慣例）：
  - 現價（免 key，一次呼叫涵蓋多幣）：
      GET https://coins.llama.fi/prices/current/coingecko:bitcoin,coingecko:ethereum,...
      → `{"coins": {"coingecko:bitcoin": {"price", "symbol", "timestamp", "confidence"}, ...}}`
    DefiLlama prices API 採用「命名空間:id」的 key 慣例；這裡沿用 CoinGecko-id
    命名空間（`coingecko:<id>`），與 `coingecko.py::_COINGECKO_IDS` 完全相同的
    幣別→id 白名單映射。**URL 的 path 段一律只由白名單 dict 的 value 組成**
    （`coingecko:bitcoin` 等），coin 代碼本身絕不直接拼進 URL——杜絕 path
    injection（攻擊者無法透過 `coin` 參數把任意字串塞進 URL path）。
  - TVL（免 key，回各鏈現值 TVL）：
      GET https://api.llama.fi/v2/chains
      → `[{"name": "Ethereum", "tvl": 5.8e10, ...}, ...]`
    coin→chain 白名單：ETH→Ethereum、SOL→Solana、BNB→BSC、ARB→Arbitrum。
    **BTC/XRP 無意義 DeFi TVL**（無對應「BTC 鏈 DeFi」概念），`fetch()` 對這兩
    幣回 []，不造假 near-zero 數值（#24 鐵律）。

誠實不造假（#24）：
  - 現價 / TVL 數值一律過 `_finite_num`（擋 NaN/inf/負/非數值/bool）；不合格的
    幣**不產 Document**（現價/TVL 是該 Document 存在的唯一理由，壞值沒有「退化
    成 N/A」的意義——退化後仍是看似合理但實則無效的客觀觀測，會污染背離偵測）。
  - TVL 24h 變化：真實 `/v2/chains` 端點**不提供** 24h 變化欄位，故 TVL 文字
    **只標現值 TVL、不強附方向詞**（維持中性 direction）。程式仍保留：若 entry
    恰含有限 `change_24h` 欄（未來端點升級或測試注入），才依正負附「流入/偏多」
    「流出/偏空」方向詞；缺欄一律中性，不捏造方向。
  - 現價文字**只標現價數字、不附方向詞**（DefiLlama prices 端點無 24h change）；
    靠價格數字 token 與 coingecko-price 進 corroboration consensus（兩者皆中性
    direction，方向相容，可互相佐證）。

安全措施（同 coingecko.py / whale_trades.py 慣例）：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）/ 固定 User-Agent。
  - 不接受外部傳入 URL；URL 只由本檔內建白名單常數 + 白名單映射組成。
  - SSRF-safe fetch（見 `safe_fetch.py`）：逐跳驗證（含初始 URL）scheme/hostname/
    port/私有 IP，DNS pinning，禁自動跟轉——`_fetch_url` 是本模組唯一外呼出口，
    price 與 tvl 兩個 Source 的所有真 HTTP 請求都只走這一條。
"""
from __future__ import annotations

import hashlib
import json
import math
import time

from . import safe_fetch
from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"

# 時間戳合理範圍下限（同 coingecko.py 慣例，只用來擋明顯異常值如 0/負值）。
_MIN_PLAUSIBLE_EPOCH = 1_577_836_800.0  # 2020-01-01T00:00:00Z

# 幣別代碼 -> CoinGecko coin id（與 coingecko.py::_COINGECKO_IDS 一致）。
# DefiLlama prices 端點用 `coingecko:<id>` 命名空間查價，沿用同一份白名單映射。
_PRICE_COINGECKO_IDS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ARB": "arbitrum",
}

# 幣別代碼 -> DefiLlama `/v2/chains` 端點的鏈名稱。BTC/XRP 刻意不在這裡：
# DeFi TVL 是「該鏈上 DeFi 協議鎖倉總量」，BTC/XRP 沒有對應的 DeFi 生態 TVL
# 概念，回 near-zero 是造假（#24），故這兩幣 `fetch()` 直接回 []。
_TVL_CHAINS: dict[str, str] = {
    "ETH": "Ethereum",
    "SOL": "Solana",
    "BNB": "BSC",
    "ARB": "Arbitrum",
}

# 兩個端點寫死常數（不同 host，各自 SSRF-safe 驗證；URL 全程不含任何 secret）。
_PRICE_URL_PREFIX = "https://coins.llama.fi/prices/current/"
_TVL_URL = "https://api.llama.fi/v2/chains"


def _finite_num(
    v: object,
    lo: float | None = None,
    hi: float | None = None,
    exclusive_lo: bool = False,
) -> float | None:
    """數值欄位共用有限驗證（同 coingecko.py::_finite_num，呼應 #24 不造假）：
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


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 SSRF-safe GET（見 `safe_fetch.py`）。

    本模組所有真 HTTP 請求（現價 + TVL）唯一的出口：price 與 tvl 兩個 Source
    不論呼叫端是誰，送出請求一律經過這裡交給 SSRF-safe 的 `safe_fetch.fetch_url`
    實際送出。
    """
    return safe_fetch.fetch_url(
        url, user_agent=_UA, timeout=_TIMEOUT, max_bytes=_MAX_BYTES,
    )


class DefiLlamaPriceSource(Source):
    """DefiLlama 即時現價（coins.llama.fi/prices/current，免 key，一次呼叫涵蓋多幣）。

    作為 `coingecko-price` 以外的**第二條獨立現價來源**，與之形成 corroboration
    consensus（同幣現價被兩個不同 source 各自報出 → 獨立佐證 → 該現價信任分提升）。

    `coin` 指定單一目標時只回該幣；`coin` 為空時回白名單 6 幣各一筆，皆帶顯式
    `meta["coin"]`。非白名單幣種一律跳過（回 []）。
    """

    kind = "price_live"
    name = "defillama-price"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        targets = [coin.upper()] if coin else list(_PRICE_COINGECKO_IDS)
        targets = [t for t in targets if t in _PRICE_COINGECKO_IDS]
        if not targets:
            return []
        # URL path 段只由白名單 dict 的 value（coingecko id）組成，coin 代碼本身
        # 絕不直接拼進 URL——即使 coin 含特殊字元也無法影響最終 URL（path injection 防）。
        gids = [_PRICE_COINGECKO_IDS[t] for t in targets]
        keys = ",".join(f"coingecko:{g}" for g in gids)
        url = _PRICE_URL_PREFIX + keys
        data = json.loads(_fetch_url(url))
        coins = data.get("coins") if isinstance(data, dict) else None
        if not isinstance(coins, dict):
            return []
        fallback_now = time.time()
        docs: list[Document] = []
        for code in targets:
            gid = _PRICE_COINGECKO_IDS[code]
            entry = coins.get(f"coingecko:{gid}")
            if not isinstance(entry, dict):
                continue
            # 現價是這個 Document 存在的唯一理由，壞值（NaN/inf/負/零/非數值）
            # 直接跳過該幣——不退化成 N/A 續產（N/A 現價對 price_live 沒意義，
            # 且 price_live 是 OBJECTIVE_KINDS，壞現價會污染 detect_cross_source_signal）。
            price = _finite_num(entry.get("price"), lo=0.0, exclusive_lo=True)
            if price is None:
                continue
            # 時間戳只接受「合理範圍內的過去 epoch」，並 clamp 到 <= 呼叫當下
            # （擋未來戳灌 recency 到滿分，同 coingecko.py 慣例）；缺欄/壞值退回
            # 本地呼叫當下時間（真實、有限、非未來）。
            raw_ts = _finite_num(entry.get("timestamp"), lo=_MIN_PLAUSIBLE_EPOCH)
            ts = min(raw_ts, fallback_now) if raw_ts is not None else fallback_now
            # 只標現價數字，不附方向詞：DefiLlama prices 端點無 24h change 欄位，
            # 附方向詞會捏造方向（#24）；靠價格數字 token 與 coingecko-price 進
            # corroboration consensus（兩者皆中性 direction，方向相容）。
            ref = f"{code} 現價 {price} USD"
            doc_id = "defillama-price-" + hashlib.md5(f"{code}-{ref}".encode()).hexdigest()[:12]
            docs.append(Document(
                id=doc_id,
                kind=self.kind,
                source=self.name,
                text=ref,
                url=url,
                ts=ts,
                meta={"content_reference": ref, "coin": code},
            ))
        return docs


class DefiLlamaTvlSource(Source):
    """DefiLlama 各鏈 DeFi TVL（api.llama.fi/v2/chains，免 key，一次呼叫回全鏈現值）。

    新增價格以外的客觀市場面向（kind=`defi_tvl`，OBJECTIVE_KINDS）。`coin` 指定
    單一目標時只回該鏈；`coin` 為空時回白名單 4 鏈各一筆。**BTC/XRP 不在
    `_TVL_CHAINS`**（無意義 DeFi TVL），`fetch()` 對這兩幣回 []，不造假 near-zero。

    真實 `/v2/chains` 端點**不提供 24h 變化欄位**，故文字只標現值 TVL、不附方向詞
    （中性 direction）。程式保留：若 entry 恰含有限 `change_24h` 欄（未來端點升級
    或測試注入），才依正負附「流入/偏多」「流出/偏空」方向詞，進背離偵測；缺欄一律中性。
    """

    kind = "defi_tvl"
    name = "defillama-tvl"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        targets = [coin.upper()] if coin else list(_TVL_CHAINS)
        targets = [t for t in targets if t in _TVL_CHAINS]
        if not targets:
            return []
        data = json.loads(_fetch_url(_TVL_URL))
        if not isinstance(data, list):
            return []
        # 建 name -> entry 對照；只收 name 為非空字串、entry 為 dict 者。
        by_name: dict[str, dict] = {}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if isinstance(name, str) and name.strip():
                by_name[name] = entry
        fallback_now = time.time()
        docs: list[Document] = []
        for code in targets:
            chain = _TVL_CHAINS[code]
            entry = by_name.get(chain)
            if not isinstance(entry, dict):
                continue
            # TVL 是這個 Document 存在的唯一理由，壞值（NaN/inf/負/非數值）直接
            # 跳過該鏈——不造假 near-zero、不退化成 N/A（defi_tvl 是 OBJECTIVE_KINDS）。
            tvl = _finite_num(entry.get("tvl"), lo=0.0)
            if tvl is None:
                continue
            # 真實 /v2/chains 端點無 change_24h 欄（文件化於模組頂部）：若 entry 恰含
            # 有限 change_24h 才用，否則不附方向詞（中性，不捏造方向）。
            change = _finite_num(entry.get("change_24h"))
            if change is not None:
                if change > 0:
                    change_desc = "24h 流入（TVL 上升，偏多）"
                elif change < 0:
                    change_desc = "24h 流出（TVL 下降，偏空）"
                else:
                    change_desc = "24h 變動持平"
                ref = f"{code}（{chain} 鏈）DeFi TVL 約 {tvl:,.0f} USD，{change_desc}"
            else:
                ref = f"{code}（{chain} 鏈）DeFi TVL 約 {tvl:,.0f} USD（現值，端點未提供 24h 變化）"
            doc_id = "defillama-tvl-" + hashlib.md5(f"{code}-{ref}".encode()).hexdigest()[:12]
            docs.append(Document(
                id=doc_id,
                kind=self.kind,
                source=self.name,
                text=ref,
                url=_TVL_URL,
                ts=fallback_now,
                # #1162：`defi_tvl` 信譽走 per-doc `meta["reputation"]` 覆寫（=0.85），
                # 而非登記進 `KIND_REPUTATION`/`trustforge_core.scoring`。原因：後者為
                # shadow runtime「受審候選核心」原始檔，其內容 hash 被
                # `data/contracts/reviewed-shadow-candidate.v1.json` 固定釘住（CISO 審核
                # 安全工件），任何變更都需同步重釘 digest 並走 CISO review——超出本連接器
                # 範圍。per-doc 覆寫在 legacy 與候選核心兩條路徑都被 `_source_reputation`
                # 採用（優先於 kind 預設），行為等價且不碰受守門的檔案。
                meta={
                    "content_reference": ref,
                    "coin": code,
                    "tvl": tvl,
                    "reputation": 0.85,
                },
            ))
        return docs


def build_defillama_sources() -> list[Source]:
    """回傳所有已啟用的 DefiLlama 連接器。"""
    return [DefiLlamaPriceSource(), DefiLlamaTvlSource()]
