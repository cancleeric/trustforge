"""CoinGecko 真實加密貨幣資料連接器（W-coingecko，CEO 審核 gray 計劃）。

來源白名單（寫死，防 SSRF；比照 `onchain.py` 慣例）：
  - 現價（免費端點，1 次呼叫涵蓋全部 5 幣）：
      GET https://api.coingecko.com/api/v3/simple/price
          ?ids=bitcoin,ethereum,solana,binancecoin,ripple&vs_currencies=usd
          &include_24hr_change=true&include_market_cap=true
          &include_last_updated_at=true
  - 社群情緒 + 開發活動（免費端點，每幣 1 次呼叫，同一回應同時含兩種資料）：
      GET https://api.coingecko.com/api/v3/coins/{id}
          ?localization=false&tickers=false&market_data=false
          &community_data=false&developer_data=true
      → `sentiment_votes_up_percentage` / `sentiment_votes_down_percentage`
        （CoinGeckoSentimentSource）+ `developer_data.{stars,forks,
        commit_count_4_weeks}`（CoinGeckoDevSource）。
      `community_data` free tier 恆為 null，不使用。

高效抓取（老闆修正：keyless 5-15 req/min 足夠，不必靠 key 硬撐量）：
  排程一輪（5 幣）合計只需 **≈6 次真呼叫**：
    - 現價 1 次（回應本身就涵蓋全 5 幣，`CoinGeckoPriceSource` 內部用
      `_get_price_data()` 記憶體快取，同一輪內不管被呼叫幾次都只真的打
      一次 API）。
    - coins/{id} 詳情每幣 1 次（`_get_coin_detail(gid)` 記憶體快取）：
      `CoinGeckoSentimentSource` 與 `CoinGeckoDevSource` 打同一個端點，
      同一輪內任一個先呼叫時真的打 API 並快取，另一個直接複用快取內容，
      **同一幣的 coin-detail 一輪只打一次**，不會各自獨立重打成 2 次。
  這兩個記憶體快取都是「單一 process 生命週期」的模組級變數：
  `scripts/fetch_scheduler.py` 每次執行都是全新 process，快取自然歸零，
  不會有跨輪髒資料殘留的疑慮；平常測試需要在每個測試案例間重置（見
  `reset_process_cache()`）。

5 幣對映（COIN_POOL 代碼 -> CoinGecko coin id）：
  BTC->bitcoin, ETH->ethereum, SOL->solana, BNB->binancecoin, XRP->ripple
其餘幣種一律視為非目標，`fetch()` 靜默跳過（回傳 []），不會現串任意 URL。

Demo API key（選用，keyless 已足夠，key 只是錦上添花）：
  官方文件：keyless public API 5-15 calls/min；上面的高效抓取策略把一輪
  壓到 ≈6 次、排程間隔 300 秒（見 `cache.py`），平均 <1.2 次/分鐘，keyless
  綽綽有餘。仍支援選用的免費 Demo key（`COINGECKO_API_KEY` env）以防未來
  幣種擴充或排程加密：有值才透過 **`x-cg-demo-api-key` 請求 header**
  （非 URL query param）隨請求送出；**沒有設 env 時完全不受影響，退回
  keyless 呼叫**（不報錯，不加該 header）。
  ⚠️ key 是 secret：只從 env 讀，絕不 hardcode，絕不寫進
  `Document.url`/`meta`/log（避免留痕外洩）——**URL 全程（含實際發出的
  HTTP request）一律乾淨、不含 key**，key 只透過 header 傳遞，不會被
  proxy/tracing/access-log/例外訊息裡常見的「記錄請求 URL」路徑意外側錄
  外洩（codex 對抗審 HIGH 修正：query param 版本即使 `Document.url` 乾淨，
  `Request.full_url` 實際仍含 key，一樣有外洩風險）。

安全措施（同 onchain.py）：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）
  - 固定 User-Agent
  - 不接受外部傳入 URL；URL 只由本檔內建的白名單常數 + 5 幣白名單映射組成
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from urllib.request import Request, urlopen

from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"

# 社群情緒多空票數差距門檻（百分點）：達到門檻才在文字裡採用單一方向的
# 明確措辭，讓 trust.scoring._infer_direction 能推出正確主導方向；差距不足
# 門檻視為五五波、維持中性語意（見 CoinGeckoSentimentSource.fetch）。
_SENTIMENT_DOMINANCE_THRESHOLD = 5.0

# Demo API key env（選用；keyless 已足夠，見模組頂部說明）。只從 env 讀，
# 絕不 hardcode。實際 key 由 CEO 另立步驟在部署環境（systemd/EC2）設定，
# 本檔不經手真實 key 值。
_API_KEY_ENV = "COINGECKO_API_KEY"

# 5 幣白名單：COIN_POOL 代碼 -> CoinGecko coin id（見 schema.COIN_POOL）。
_COINGECKO_IDS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
}

_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=" + ",".join(_COINGECKO_IDS.values())
    + "&vs_currencies=usd&include_24hr_change=true&include_market_cap=true"
    + "&include_last_updated_at=true"
)

# --- 單一 process 生命週期的記憶體快取（高效抓取，見模組頂部說明）--------
# 現價：一輪內不管被呼叫幾次（fetch_scheduler 逐幣呼叫 CoinGeckoPriceSource
# 5 次）都只真的打一次 API；`None` 代表本輪尚未打過。
_price_response_cache: dict | None = None
# coins/{id} 詳情：以 coingecko id 為 key，sentiment 與 dev 兩個 Source
# 共用同一份，任一個先呼叫時才真的打 API。
_coin_detail_cache: dict[str, dict] = {}


def reset_process_cache() -> None:
    """清空上述兩個記憶體快取。供測試在案例之間重置，避免快取內容跨測試
    案例殘留污染；正常執行（`scripts/fetch_scheduler.py`）每次都是全新
    process，不需要呼叫這個函式。"""
    global _price_response_cache
    _price_response_cache = None
    _coin_detail_cache.clear()


def _api_key_headers() -> dict[str, str]:
    """若設定 `COINGECKO_API_KEY` env，回傳含 `x-cg-demo-api-key` 的請求
    header 字典供 `_fetch_url` 附加；未設定則回傳空字典（keyless 降級，
    不報錯、不加該 header）。

    ⚠️ key 一律透過 HTTP header 傳遞，**絕不**附加在 URL query param 上
    （避免 proxy/tracing/access-log/例外訊息等常見「記錄請求 URL」路徑
    side-channel 側錄外洩）；`Document.url`/`meta`/log 一律只存乾淨 URL，
    與此函式回傳值無關。
    """
    key = os.environ.get(_API_KEY_ENV, "").strip()
    if not key:
        return {}
    return {"x-cg-demo-api-key": key}


def _fetch_url(url: str, extra_headers: dict[str, str] | None = None) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 urllib GET（同 onchain.py）。

    `extra_headers`（如有）會與固定 `User-Agent` 一併附加在請求 header
    上；URL 本身不受影響、一律保持乾淨（不含任何 secret）。
    """
    headers = {"User-Agent": _UA}
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url, headers=headers)
    with urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read(_MAX_BYTES)


def _coin_detail_url(coingecko_id: str) -> str:
    """coins/{id} 詳情端點 URL（social/developer_data 開，其餘關閉省流量）。"""
    return (
        f"https://api.coingecko.com/api/v3/coins/{coingecko_id}"
        "?localization=false&tickers=false&market_data=false"
        "&community_data=false&developer_data=true"
    )


def _get_price_data() -> dict:
    """`_PRICE_URL` 的記憶體快取版：本輪（process 生命週期）第一次呼叫才
    真的打 API，之後直接複用，讓 5 幣共用同一次呼叫（見模組頂部說明）。"""
    global _price_response_cache
    if _price_response_cache is None:
        raw = _fetch_url(_PRICE_URL, _api_key_headers())
        _price_response_cache = json.loads(raw)
    return _price_response_cache


def _get_coin_detail(coingecko_id: str) -> dict:
    """`_coin_detail_url(coingecko_id)` 的記憶體快取版：sentiment 與 dev
    兩個 Source 共用，同一幣本輪只真的打一次 API（見模組頂部說明）。"""
    if coingecko_id not in _coin_detail_cache:
        raw = _fetch_url(_coin_detail_url(coingecko_id), _api_key_headers())
        _coin_detail_cache[coingecko_id] = json.loads(raw)
    return _coin_detail_cache[coingecko_id]


class CoinGeckoPriceSource(Source):
    """CoinGecko 即時現價（simple/price，免費端點，1 次呼叫涵蓋 5 幣）。

    `coin` 指定單一目標時只回傳該幣的 Document；`coin` 為空字串時（全市場
    通用查詢）回傳 5 幣各一筆，皆帶顯式 `meta["coin"]`（避免被
    `base._matches_coin()` 誤判成「全市場通用、每幣都納入」的兜底分支——
    現價本質上是幣種特定資料，不是市場通用訊號）。非白名單幣種一律跳過。
    """
    kind = "price_live"
    name = "coingecko-price"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        targets = [coin.upper()] if coin else list(_COINGECKO_IDS)
        targets = [t for t in targets if t in _COINGECKO_IDS]
        if not targets:
            return []
        data = _get_price_data()
        fallback_now = time.time()
        docs: list[Document] = []
        for code in targets:
            gid = _COINGECKO_IDS[code]
            entry = data.get(gid)
            if not isinstance(entry, dict):
                continue
            price = entry.get("usd")
            if price is None:
                continue
            change_24h = entry.get("usd_24h_change")
            mcap = entry.get("usd_market_cap")
            mcap_str = f"{mcap:,.0f}" if isinstance(mcap, (int, float)) else "N/A"
            # 修（codex HIGH，Tier2 同批修正）：原本只寫數字「+8.20%」，不含
            # trust.scoring._infer_direction 認得的方向詞（上漲/下跌等），導致
            # price_live 主張永遠推斷成 neutral——即使漲跌幅再大，客觀類的
            # 信任加權主導方向也恆為 neutral，detect_cross_source_signal 永遠
            # 拒收，背離/共識判定形同虛設。改為依漲跌幅正負附上明確方向詞
            # （持平則不附，維持中性語意，符合實際盤況）。
            if isinstance(change_24h, (int, float)):
                if change_24h > 0:
                    change_str = f"{change_24h:+.2f}%（上漲）"
                elif change_24h < 0:
                    change_str = f"{change_24h:+.2f}%（下跌）"
                else:
                    change_str = f"{change_24h:+.2f}%（持平）"
            else:
                change_str = "N/A"
            ref = f"{code} 現價 {price} USD，24h 變動 {change_str}，市值 {mcap_str} USD"
            doc_id = "coingecko-price-" + hashlib.md5(f"{code}-{ref}".encode()).hexdigest()[:12]
            # 優先用 API 回應本身的 last_updated_at（真正的鮮度來源，反映該筆
            # 報價實際成立的時間點）；缺欄位（未設 include_last_updated_at 生效
            # 前的舊快取/降級回應）才退回本地呼叫當下時間。
            last_updated = entry.get("last_updated_at")
            ts = float(last_updated) if isinstance(last_updated, (int, float)) else fallback_now
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


class CoinGeckoSentimentSource(Source):
    """CoinGecko 社群情緒投票百分比（coins/{id}，每幣 1 次呼叫，與
    `CoinGeckoDevSource` 共用同一份記憶體快取，見模組頂部說明）。

    `coin` 必須是白名單 5 幣之一才知道要打哪個 id 的端點；空字串或非白名單
    幣種一律跳過（回傳 []）——與 `CoinGeckoPriceSource` 不同，此端點無法一次
    呼叫涵蓋多幣，沒有明確目標幣就無法決定要呼叫哪個 URL。
    """
    kind = "sentiment"
    name = "coingecko-sentiment"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        code = coin.upper()
        gid = _COINGECKO_IDS.get(code)
        if gid is None:
            return []
        detail_url = _coin_detail_url(gid)
        data = _get_coin_detail(gid)
        up = data.get("sentiment_votes_up_percentage")
        down = data.get("sentiment_votes_down_percentage")
        if up is None and down is None:
            return []
        up_val = up if isinstance(up, (int, float)) else None
        down_val = down if isinstance(down, (int, float)) else None
        up_str = f"{up_val:.1f}%" if up_val is not None else "N/A"
        down_str = f"{down_val:.1f}%" if down_val is not None else "N/A"
        # 修（codex HIGH，Tier2）：原本文字固定同時寫「看漲 X%，看跌 Y%」，
        # 導致 trust.scoring._infer_direction 對「看漲」「看跌」各計一次而
        # 永遠打平回 neutral，使 detect_cross_source_signal 拒收、背離永不
        # 觸發——把 sentiment 加進 _SENTIMENT_KINDS 形同虛設。改為依多空數字
        # 的實際主導方向組字：差距達門檻才用單一方向詞描述（另一側只給數字、
        # 不帶任何方向關鍵詞，避免又被計成平手）；差距不足門檻或只有單邊
        # 數據極端時才維持/退回原始語意。
        if up_val is not None and down_val is not None:
            diff = up_val - down_val
        elif up_val is not None:
            diff = 100.0  # 只有多方票數 → 視為明確偏多
        else:
            diff = -100.0  # 只有空方票數 → 視為明確偏空
        if diff >= _SENTIMENT_DOMINANCE_THRESHOLD:
            ref = f"{code} 社群情緒偏多：看漲 {up_str}（多數意見），另有 {down_str} 持保留看法"
        elif diff <= -_SENTIMENT_DOMINANCE_THRESHOLD:
            ref = f"{code} 社群情緒偏空：看跌 {down_str}（多數意見），另有 {up_str} 持保留看法"
        else:
            ref = f"{code} 社群情緒投票：看漲 {up_str}，看跌 {down_str}"
        doc_id = "coingecko-sentiment-" + hashlib.md5(f"{code}-{ref}".encode()).hexdigest()[:12]
        return [Document(
            id=doc_id,
            kind=self.kind,
            source=self.name,
            text=ref,
            url=detail_url,
            ts=time.time(),
            meta={"content_reference": ref, "coin": code},
        )]


class CoinGeckoDevSource(Source):
    """CoinGecko 開發活動（coins/{id} 的 developer_data，每幣 1 次呼叫，與
    `CoinGeckoSentimentSource` 共用同一份記憶體快取，見模組頂部說明）。

    與 `CoinGeckoSentimentSource` 打同一個端點（回應本身同時含兩種資料）；
    兩者仍是獨立 Source（不同 kind/cache key/refresh 節奏，見 `cache.py`），
    只是底層 HTTP 呼叫透過 `_get_coin_detail()` 共用一次，換取「兩個獨立
    可排程/降級的 Source」與「同一幣一輪只打一次 API」兩者兼得。
    """
    kind = "dev_activity"
    name = "coingecko-dev"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        code = coin.upper()
        gid = _COINGECKO_IDS.get(code)
        if gid is None:
            return []
        detail_url = _coin_detail_url(gid)
        data = _get_coin_detail(gid)
        dev = data.get("developer_data")
        if not isinstance(dev, dict):
            return []
        stars = dev.get("stars")
        forks = dev.get("forks")
        commits = dev.get("commit_count_4_weeks")
        if stars is None and forks is None and commits is None:
            return []
        stars_str = stars if stars is not None else "N/A"
        forks_str = forks if forks is not None else "N/A"
        commits_str = commits if commits is not None else "N/A"
        ref = (
            f"{code} 開發活動：GitHub stars {stars_str}，forks {forks_str}，"
            f"近 4 週 commits {commits_str}"
        )
        doc_id = "coingecko-dev-" + hashlib.md5(f"{code}-{ref}".encode()).hexdigest()[:12]
        return [Document(
            id=doc_id,
            kind=self.kind,
            source=self.name,
            text=ref,
            url=detail_url,
            ts=time.time(),
            meta={"content_reference": ref, "coin": code},
        )]


def build_coingecko_sources() -> list[Source]:
    """回傳所有已啟用的 CoinGecko 連接器。"""
    return [CoinGeckoPriceSource(), CoinGeckoSentimentSource(), CoinGeckoDevSource()]
