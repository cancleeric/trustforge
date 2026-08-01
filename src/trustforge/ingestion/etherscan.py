"""Etherscan V2 key-based 鯨魚交易連接器（#1168，CEO 審核 gray 計劃）。

Etherscan 提供 Ethereum 鏈上帳戶交易紀錄，用來追蹤已知鯨魚地址的大額 ETH
轉帳，作為 `whale_onchain` 信號的第二條獨立來源（與 `whale_trades.py` 的
WhaleAlertSource 同 kind、不同 source，互相獨立佐證）。

認證方式（security，harper 雙審重點）：
  Etherscan V2 是 key-based API。**V2 free-tier 只支援 query param `apikey=`**
  （這是 Etherscan 官方唯一接受的免費層認證方式，與 whale_alert_secret 的
  `api_key=` query 同構；CMC/Coingecko 走 header 是因為它們官方指定 header）。
  因此 key **必然**出現在「發出的請求 URL」上。為避免 key 透過 log / Document /
  例外訊息 side-channel 外洩：
    1. key 由 `etherscan_secret` 模組解析（SSM SecureString 優先 → 本機檔 0o600
       → env），本檔不經手 key 的儲存/管理。
    2. **HTTPError（429/5xx）與 Etherscan API error payload（HTTP 200 + status
       !="1"）一律 raise sanitized RuntimeError**（絕非 silent `return []`）。
       早期版本為防 key 外洩而把 HTTPError 吞成 `return []`，但那是 silent
       failure：排程器記「成功空結果」、覆蓋 cache、無故障記錄（codex P1）。
       現改 raise，讓 fetch_scheduler 的 catch+log+failure 路徑接住（可觀測、
       保留舊 cache、不覆蓋），同時守住 key 防線：訊息只用固定字串 + HTTP
       status code/reason（**絕不含 URL/apikey**），並用 `from None` 中斷
       exception chaining，杜絕原 `HTTPError.url`（含 key）透過 traceback 外洩。
    3. `Document.url` 一律乾淨 `https://etherscan.io/tx/{hash}`，**絕不**含 key。
    4. `Document.meta` 只存 coin/value/direction/from/to/content_reference，**不含 key**。

來源白名單（寫死，防 SSRF；比照 `whale_trades.py`/`defillama.py` 慣例）：
  - 鯨魚交易（module=account action=txlist，逐已知地址查詢）：
      GET https://api.etherscan.io/v2/api
          ?chainid=1&module=account&action=txlist&address=<whale-address>
          &page=1&offset=20&sort=desc&apikey=<key>
      （page/offset 限縮回應大小避免被 safe_fetch 截斷，見 `_TXLIST_OFFSET`。）
      → `{"status":"1","message":"OK","result":[{from,to,value,hash,timeStamp,
          gas,gasPrice}, ...]}`，value 為 wei 字串。
    追蹤地址由 env `TRUSTFORGE_ETHERSCAN_WHALE_ADDRESSES`（逗號分隔）指定，
    預設含一個已知鯨魚地址；每個地址過 `_ADDRESS_RE`（0x+40 hex）驗證，杜絕
    path/query injection。coin 代碼（ETH）絕不直接拼進 URL。

信號語義（direction deferred — harper 誠實降級，見 `_classify_direction`）：
  - Etherscan txlist 只回 **raw hex 地址**（0x+40hex），**不帶 exchange label**
    （whale_alert 回傳 owner name 才能比對交易所關鍵字）。hex 地址只含
    [0-9a-f]，永遠不含 "binance"/"coinbase"… 等關鍵字 → inflow/outflow 方向
    在此**無法**靠關鍵字比對完成（舊版抄自 whale_trades 的比對是死碼）。
  - 故目前方向一律誠實標為中性（whale_transfer），不假冒方向訊號。要恢復方向
    需 address→exchange mapping（維護已知 CEX 熱錢包地址表），列為 follow-up。

gas 端點（gasoracle）：V2 也提供 gas 估價，但 gas 是單一中性數值、無對應
corroboration 價值（沒有第二條獨立來源可交叉佐證「方向」——gas 不帶方向訊號），
故本檔**不**實作 gas source（簡化：只做有佐證價值的 whale source）。gas 不進
OBJECTIVE_KINDS、不註冊。

安全措施（同 whale_trades.py / defillama.py 慣例）：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）/ 固定 User-Agent。
  - 不接受外部傳入 URL；URL 只由本檔內建白名單常數 + 驗證過的地址組成。
  - SSRF-safe fetch（見 `safe_fetch.py`）：逐跳驗證（含初始 URL）scheme/hostname/
    port/私有 IP，DNS pinning，禁自動跟轉——`_fetch_url` 是本模組唯一外呼出口。
  - 共享節流器（整個 api.etherscan.io host）：間隔 0.25s（4/s，留 1/s 餘裕於
    官方 5/s 上限），不論 gas（未實作）或 whale、不論查哪個地址，只要是真的
    HTTP 請求就受同一份狀態節流（仿 `coingecko.py` 共享節流器範式）。

未配置憑證時靜默降級（回 []，不報錯、不造假）；已配置但 SSM/網路暫失敗時
`fetch()` 改 raise（見 `build_etherscan_sources`），排程器 catch+log 並計入
failures（可觀測）。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode

from . import safe_fetch
from ..etherscan_secret import resolve_api_key
from .base import Document, Source

_MAX_BYTES = 512 * 1024  # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"

# Etherscan 只覆 ETH（它是 Ethereum 鏈的資料源；非 ETH 幣直接回 []）。
_SUPPORTED_COINS = frozenset({"ETH"})

# 大額門檻（wei）：value（wei 字串）>= 此值才視為鯨魚訊號。
_MIN_VALUE_WEI = 10**17  # 0.1 ETH

# 時間戳合理範圍下限（同 cmc.py/coingecko.py 慣例，擋 0/負值等明顯異常）。
_MIN_PLAUSIBLE_EPOCH = 1_577_836_800.0  # 2020-01-01T00:00:00Z

# V2 基底端點（寫死常數，防 SSRF）：query 由各 fetch 組裝（chainid/module/action
# /address/page/offset/apikey），URL 全程只含本檔內建常數 + 驗證過的地址 + key。
_WHALE_TXLIST_URL = "https://api.etherscan.io/v2/api"

# txlist 分頁（修1，CRITICAL — CEO 親測 JSONDecodeError）：高活躍 whale 地址
# （如 0xf977814e90da44bfa03b6295a0616a897441acec）txlist 全量回應 >512KB → 被
# safe_fetch 的 max_bytes 截斷 → `json.loads` 拋 "Unterminated string at char
# 524275"。Etherscan V2 支援 page/offset（offset=每頁筆數，max 10000）；設保守
# offset=20（最近 20 筆 tx，回應約 10-18 KB ≪ _MAX_BYTES），夠 whale 信號且回應
# 遠 < 截斷上限。client-side value 過濾（≥10^17）照舊。
_TXLIST_PAGE = 1
_TXLIST_OFFSET = 20

# 地址驗證：0x + 40 hex（標準 Ethereum 外部帳戶地址），杜絕 path/query injection。
_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# 預設追蹤地址（已知 ETH 鯨魚；可被 env TRUSTFORGE_ETHERSCAN_WHALE_ADDRESSES 覆寫）。
_DEFAULT_WHALE_ADDRESSES: tuple[str, ...] = (
    "0x3f5CE5FBFe3E9af3971DD833D26bA9b5C936f0bE",  # 已知高活躍 ETH 鯨魚
)

_WHALE_ADDRESSES_ENV = "TRUSTFORGE_ETHERSCAN_WHALE_ADDRESSES"


# --- 整個 api.etherscan.io host 共享的節流器（仿 coingecko.py 範式）----------
# `_last_request_monotonic` 記錄「最近一次真 HTTP 請求」的單調時鐘時間戳，
# 不論查哪個地址，只要是真請求就受同一份狀態節流。用 `time.monotonic()`（不受
# 系統時鐘調整影響，節流間隔量測才可靠）。間隔 0.25s = 4/s，留 1/s 餘裕於官方
# 5/s 上限。
_last_request_monotonic: float | None = None
_provider_request_lock = threading.RLock()
_MIN_INTERVAL_SECONDS = 0.25


def reset_throttle() -> None:
    """清空節流狀態。供測試在案例之間重置；正常執行（排程器）每次都是全新
    process，不需要呼叫。"""
    global _last_request_monotonic
    with _provider_request_lock:
        _last_request_monotonic = None


def _throttle_before_request() -> None:
    """在每次真 HTTP 請求送出前呼叫：確保跟「上一次真請求」之間至少間隔
    `_MIN_INTERVAL_SECONDS` 秒，不足則同步 `time.sleep()` 補足。整個 host
    共享單一節流狀態，不論查哪個地址。"""
    global _last_request_monotonic
    with _provider_request_lock:
        now = time.monotonic()
        if _last_request_monotonic is not None:
            wait = _MIN_INTERVAL_SECONDS - (now - _last_request_monotonic)
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
        _last_request_monotonic = now


def _resolve_whale_addresses() -> list[str]:
    """解析要追蹤的鯨魚地址清單。

    來源：env `TRUSTFORGE_ETHERSCAN_WHALE_ADDRESSES`（逗號分隔）優先；未設則用
    預設常數 `_DEFAULT_WHALE_ADDRESSES`。每個地址必須通過 `_ADDRESS_RE`
    （0x + 40 hex）驗證才納入——地址會拼進 URL query，未驗證等於放開 path/query
    injection。無任何有效地址 → 回 []（呼叫端 fetch 據此回空、不打網路）。
    """
    raw = os.getenv(_WHALE_ADDRESSES_ENV, "").strip()
    if raw:
        candidates = [c.strip() for c in raw.split(",") if c.strip()]
    else:
        candidates = list(_DEFAULT_WHALE_ADDRESSES)
    return [c for c in candidates if _ADDRESS_RE.fullmatch(c)]


def _finite_num(
    v: object, lo: float | None = None, hi: float | None = None
) -> float | None:
    """驗證欄位為有限數值（排除 bool/NaN/inf），選用值域檢查。不合格回 None。

    同 whale_trades.py::_finite_num（#24 不造假）。
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    fv = float(v)
    if not math.isfinite(fv):
        return None
    if lo is not None and fv < lo:
        return None
    if hi is not None and fv > hi:
        return None
    return fv


def _classify_direction(from_addr: str, to_addr: str) -> tuple[str, str]:
    """分類轉帳方向與語義。

    **目前一律回中性（whale_transfer）**（harper 誠實降級，codex P1 + harper High）。
    原因：Etherscan txlist 只回 raw hex 地址（0x+40hex），不帶 exchange label；
    舊版抄自 `whale_trades.py::_classify_direction` 的交易所關鍵字比對永遠
    match 不到（hex 只含 [0-9a-f]，不含 "binance"/"coinbase"…）→ inflow/outflow
    恆不觸發、是死碼。方向訊號 deferred：要恢復需 address→exchange mapping
    （維護已知 CEX 熱錢包地址表），列為 follow-up（不在本連接器範圍）。

    保留 from_addr/to_addr 參數與簽名是為未來 address→exchange lookup 預留單一
    接入點；目前刻意不使用（誠實中性，不假冒方向）。
    """
    # TODO(#1168 follow-up): 接入 address→exchange mapping 後，依 from/to 是否
    # 命中已知 CEX 熱錢包地址恢復 exchange_outflow（囤積、偏多）/ exchange_inflow
    # （賣壓、偏空）方向分類。在此之前一律中性。
    _ = (from_addr, to_addr)
    return "whale_transfer", "鯨魚間轉帳"


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 SSRF-safe GET。

    本模組所有真 HTTP 請求唯一的出口。URL 含 apikey（V2 唯一認證方式）；例外
    由呼叫端（`EtherscanWhaleSource.fetch`）catch HTTPError → raise sanitized
    RuntimeError（訊息只用 status code/reason + `from None` 斷 chain，絕不含
    URL/apikey），交給 fetch_scheduler 的 catch+log+failure 路徑（可觀測）。
    """
    return safe_fetch.fetch_url(
        url,
        user_agent=_UA,
        timeout=_TIMEOUT,
        max_bytes=_MAX_BYTES,
    )


def _parse_wei_value(raw: object) -> float | None:
    """把 Etherscan 回應的 wei value（字串）解析為有限 float。

    只接受純數字字串（Etherscan 的 value 恆為十進位 wei 字串）；含空白/非數字/
    空字串一律回 None（不造假、不退化）。#24：壞 value 直接跳過該筆交易。
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    if not raw.strip().isdigit():
        return None
    return _finite_num(float(raw))


def _parse_epoch_string(raw: object) -> float | None:
    """把 Etherscan 回應的 unix epoch 字串（如 "1700000000"）解析為有限 float。

    只接受非負整數字串；含空白/非數字/空字串一律回 None（由呼叫端 fallback
    to now）。防單筆壞 timeStamp 拋 ValueError 中斷整個 tx 迴圈。
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    if not raw.strip().isdigit():
        return None
    return _finite_num(float(raw), lo=_MIN_PLAUSIBLE_EPOCH)


# ---------------------------------------------------------------------------
# EtherscanWhaleSource — 鏈上大額 ETH 轉帳（kind = whale_onchain）
# ---------------------------------------------------------------------------


class EtherscanWhaleSource(Source):
    """Etherscan V2 鯨魚交易連接器：追蹤已知地址的鏈上大額 ETH 轉帳。

    端點：GET https://api.etherscan.io/v2/api
        ?chainid=1&module=account&action=txlist&address=<addr>
        &page=1&offset=20&sort=desc&apikey=<key>
    （page/offset 限縮回應大小避免截斷，見 `_TXLIST_OFFSET`；方向 deferred。）
    認證：query param `apikey=`（V2 free-tier 唯一選項）。
    環境變數：ETHERSCAN_API_KEY（選用；經 etherscan_secret 解析，無 key 時降級）。

    **憑證在 fetch 時解析**（`build_etherscan_sources` 永遠註冊，不在 build-time
    決定）。`resolve_api_key()` 回 `(None, "unconfigured")`（完全未設）→ `fetch()`
    回 []（靜默降級）；回 `(None, "unavailable")`（已配置但 SSM/網路暫失敗）→
    `fetch()` raise RuntimeError（排程器 catch+log 並計入 failures，可觀測）。

    只覆 ETH（Etherscan 是 Ethereum 鏈資料源；非 ETH 幣回 []）。
    """

    kind = "whale_onchain"
    name = "etherscan-whale"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        # Etherscan 只覆 ETH：非 ETH 幣直接回 []（不打網路）。
        if coin and coin.upper() != "ETH":
            return []

        # 憑證在 fetch 時解析（build 永遠註冊、不在 build-time 決定）。兩種「無 key」
        # 情況刻意分開（同 cmc.py，codex P1 可觀測性）：
        #   - "unconfigured"（完全未設）→ 回 []（非故障，靜默降級）。
        #   - "unavailable"（已配置但 SSM/網路暫失敗）→ raise RuntimeError，
        #     排程器 catch+log 並計入 failures（可觀測失敗），避免隱形憑證中斷。
        key, key_source = resolve_api_key()
        if not key:
            if key_source == "unavailable":
                raise RuntimeError(
                    "Etherscan credential unavailable (configured but SSM/network failed)"
                )
            return []

        addresses = _resolve_whale_addresses()
        if not addresses:
            return []

        docs: list[Document] = []
        # 修3（codex P2）：追蹤多個 whale 地址時，同筆 tx（from/to 都是追蹤地址）
        # 會在兩次 txlist query 各回一次 → 同 Document ID 重複。by tx hash 去重。
        seen_hashes: set[str] = set()
        for address in addresses:
            _throttle_before_request()
            query_str = urlencode(
                {
                    "chainid": 1,
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    # 修1：分頁限縮回應大小（見 _TXLIST_PAGE/_TXLIST_OFFSET）。
                    "page": _TXLIST_PAGE,
                    "offset": _TXLIST_OFFSET,
                    "sort": "desc",
                    "apikey": key,
                }
            )
            url = f"{_WHALE_TXLIST_URL}?{query_str}"
            # codex P1（silent-failure 修復）：HTTPError（429/5xx）與 Etherscan
            # API error payload（HTTP 200 + status!="1"）原本都被 `return []`
            # 吞掉——排程器記「成功空結果」、覆蓋 cache、無故障記錄，故障變隱形。
            # 改成 raise sanitized RuntimeError，讓 fetch_scheduler 的 catch+log+
            # failure 路徑接住（可觀測、保留舊 cache、不覆蓋）。
            #
            # ⛔ query-key 防線：raise 的例外訊息**絕不含 URL/apikey**。URL 含
            # apikey（V2 唯一認證方式）；只能用固定字串 + HTTP status code/reason
            # 組訊息。並用 `from None` 中斷 exception chaining——否則
            # `RuntimeError.__cause__` 會指向原 `HTTPError`（其 `.url` 屬性含
            # apikey），一旦有人走 logging.exception / 印完整 traceback 就會把
            # URL（含 key）side-channel 外洩。`from None` 設 __suppress_context__=
            # True、__cause__=None，traceback 不再帶出原 exception。
            try:
                raw = _fetch_url(url)
            except HTTPError as exc:
                # exc.code（int status）/ exc.reason（status reason phrase，如
                # "Too Many Requests"）是 response status line 成分，不含 request
                # URL/query/apikey；exc.url 含 key 故絕不引用。
                raise RuntimeError(
                    f"Etherscan request failed: HTTP {exc.code} {exc.reason or ''}"
                ) from None
            data = json.loads(raw)
            if not isinstance(data, dict):
                # 非預期回應格式（非 dict）→ raise（非 silent []），可觀測。
                # 固定字串，不帶任何 response 細節（防 key/address side-channel）。
                raise RuntimeError("Etherscan API error: unexpected response format")
            # 修2（codex P2）：用 result 型別區分「正常空」vs「真錯誤」，**不再用
            # status 欄位判 raise**。Etherscan 對「合法地址無 tx」回
            # `{"status":"0","message":"No transactions found","result":[]}`
            # （result 是**空 list**）→ 這是正常空結果非 failure；舊版把 status!="1"
            # 一律 raise → 排程器誤報故障、覆蓋 stale cache。回應形狀：
            #   - 成功有 tx：status="1", result=[txs]
            #   - 合法空（無 tx）：status="0", message="No transactions found", result=[]
            #   - 真錯誤（invalid key/rate limit）：status="0", message="NOTOK",
            #     result="..."（result 是 **string**）
            # 故判準：result 是 list → 處理（空 list→[] 成功，含 tx→parse）；result
            # 是 string（或非 list）→ raise sanitized RuntimeError。raise 訊息固定
            # 字串 + from None（防 key/address 透過 chain/response 文字 side-channel）。
            result = data.get("result")
            if not isinstance(result, list):
                raise RuntimeError("Etherscan API error (status=0)") from None
            for tx in result:
                if not isinstance(tx, dict):
                    continue
                tx_hash = str(tx.get("hash", "")).strip()
                # 修3：跨地址同 tx 去重（by hash）。
                if tx_hash and tx_hash in seen_hashes:
                    continue
                doc = self._parse_tx(tx)
                if doc is not None:
                    if tx_hash:
                        seen_hashes.add(tx_hash)
                    docs.append(doc)
        return docs

    def _parse_tx(self, tx: dict) -> Document | None:
        """解析單筆 Etherscan txlist 回應為 Document。"""
        # 修1（codex P1）：reverted/失敗 tx 過濾（防假證據）。Etherscan txlist 每筆
        # tx 帶 isError（"0"成功/"1"失敗）與 txreceipt_status（"0"/"1"）。reverted
        # tx（isError=="1" 或 txreceipt_status=="0"）的 value 仍記錄企圖轉帳額，但
        # 實際未轉 → 若納入會把「企圖但失敗的大額轉帳」當成真 whale 證據（假證據）。
        # 故取 value 前先跳過：兩欄任一標失敗即 return None（不產 Document）。缺欄
        # 視為非失敗（"" != "1"/"0"），避免誤殺缺 receipt 的 pending/舊 tx。
        is_error = str(tx.get("isError", "")).strip()
        txreceipt_status = str(tx.get("txreceipt_status", "")).strip()
        if is_error == "1" or txreceipt_status == "0":
            return None
        # value（wei 字串）→ float；非數字/壞值 → 跳過該筆（#24 不造假）。
        value = _parse_wei_value(tx.get("value"))
        if value is None or value < _MIN_VALUE_WEI:
            return None

        from_addr = str(tx.get("from", "")).strip()
        to_addr = str(tx.get("to", "")).strip()
        tx_hash = str(tx.get("hash", "")).strip()
        if not from_addr or not to_addr or not tx_hash:
            return None

        # 時間戳（unix epoch 字串）；壞值/缺欄退回 now（真實、有限、非未來）。
        ts = _parse_epoch_string(tx.get("timeStamp"))
        if ts is None:
            ts = time.time()

        # 方向分類：Etherscan hex 地址無 exchange label → 一律中性（見
        # `_classify_direction`，direction deferred）。
        direction_tag, direction_desc = _classify_direction(from_addr, to_addr)

        amount_eth = value / 1e18
        # 縮短地址顯示（避免 text 過長；完整地址存 meta）。
        from_short = f"{from_addr[:10]}...{from_addr[-4:]}"
        to_short = f"{to_addr[:10]}...{to_addr[-4:]}"
        text = (
            f"ETH 鯨魚大額轉帳：{amount_eth:.4f} ETH"
            f" 從 {from_short} 轉至 {to_short}，{direction_desc}"
        )

        doc_id = (
            "etherscan-whale-"
            + hashlib.md5(f"{tx_hash}-{ts}".encode()).hexdigest()[:12]
        )

        # URL 乾淨（不含 apikey）；meta 不含 key。
        return Document(
            id=doc_id,
            kind=self.kind,
            source=self.name,
            text=text,
            url=f"https://etherscan.io/tx/{tx_hash}",
            ts=ts,
            meta={
                "coin": "ETH",
                "amount_eth": amount_eth,
                "value_wei": value,
                "direction": direction_tag,
                "from": from_addr,
                "to": to_addr,
                "content_reference": text[:120],
            },
        )


def build_etherscan_sources() -> list[Source]:
    """永遠註冊 Etherscan 鯨魚連接器（同 `build_cmc_sources`/`build_whale_sources`
    慣例）。

    **不在 build-time resolve 憑證**——憑證解析延後到 `fetch()`。理由（codex P1，
    可觀測性）：若在 build-time 呼叫 `resolve_api_key()`，SSM 暫時不可用時回
    `(None, "unavailable")` → build 回 [] → source 從 registry 消失 → 排程器根本
    不會跑它 → 憑證中斷變成隱形失敗（cron/監控看不到、cache 無聲過期、產品端
    斷料才被發現）。永遠註冊後，憑證狀態在 `fetch()` 時才決定：
      - "unconfigured"（完全未設）→ `fetch()` 回 []（非故障，靜默降級）。
      - "unavailable"（已配置但 SSM/網路暫失敗）→ `fetch()` raise RuntimeError，
        排程器 catch+log 並計入 failures（可觀測失敗）。
    """
    return [EtherscanWhaleSource()]
