"""名人/鯨魚交易信號連接器（Celebrity & Whale Trade Signals）。

追蹤鏈上大額轉帳（鯨魚）與公開知名交易者動向，作為信心參考的佐證型信號。

來源白名單（寫死，防 SSRF）：
  - Whale Alert 大額轉帳  https://api.whale-alert.io/v1/transactions
  - Arkham Intelligence    https://api.arkhamintelligence.com/transfers

信號分層：
  - whale_onchain（鏈上可驗證的大額轉帳）：信譽 0.88
    → 交易所流出＝囤積訊號（偏多）；交易所流入＝賣壓訊號（偏空）
  - celebrity_trade（已標記錢包/名人公開交易）：信譽 0.50
    → 未經鏈上驗證者自動降級至 social 等級 0.35

安全措施（同 onchain.py / coingecko.py 慣例）：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）
  - 固定 User-Agent
  - 不接受外部傳入 URL；URL 只由本檔內建白名單常數組成
  - SSRF-safe fetch（見 `safe_fetch.py`）
  - API key 僅從環境變數讀取（WHALE_ALERT_API_KEY / ARKHAM_API_KEY），
    絕不 hardcode，絕不寫進 Document.url/meta/log
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from urllib.parse import urlencode

from . import safe_fetch
from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"

# 最低金額門檻（USD）：低於此門檻的轉帳不視為鯨魚信號
_MIN_VALUE_USD = 1_000_000

# 5 幣白名單（與 base.py 一致）
_SUPPORTED_COINS = frozenset({"BTC", "ETH", "SOL", "BNB", "XRP", "ARB"})

# Whale Alert 幣種映射（API symbol → 我們的代碼）
_WHALE_ALERT_SYMBOLS: dict[str, str] = {
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ethereum": "ETH",
    "sol": "SOL", "solana": "SOL",
    "bnb": "BNB",
    "xrp": "XRP", "ripple": "XRP",
    "arb": "ARB", "arbitrum": "ARB",
}

# API key 環境變數名稱
_WHALE_ALERT_KEY_ENV = "WHALE_ALERT_API_KEY"
_ARKHAM_KEY_ENV = "ARKHAM_API_KEY"


def _finite_num(v: object, lo: float | None = None, hi: float | None = None) -> float | None:
    """驗證欄位為有限數值（排除 bool/NaN/inf），選用值域檢查。不合格回 None。"""
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


def _fetch_url(url: str, extra_headers: dict[str, str] | None = None) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 SSRF-safe GET。"""
    return safe_fetch.fetch_url(
        url, user_agent=_UA, extra_headers=extra_headers,
        timeout=_TIMEOUT, max_bytes=_MAX_BYTES,
    )


def _classify_direction(from_owner: str, to_owner: str) -> tuple[str, str]:
    """根據轉帳來源/目的地分類方向與語義。

    回傳 (direction_tag, direction_word)：
      - exchange_outflow → 偏多（從交易所轉出＝囤積）
      - exchange_inflow  → 偏空（轉入交易所＝可能賣出）
      - whale_transfer   → 中性（鯨魚間轉帳）
    """
    from_lower = from_owner.lower() if from_owner else ""
    to_lower = to_owner.lower() if to_owner else ""

    # 已知交易所名稱關鍵字
    exchange_keywords = {"binance", "coinbase", "kraken", "okx", "bybit",
                         "bitfinex", "huobi", "kucoin", "gate.io", "exchange"}

    from_is_exchange = any(kw in from_lower for kw in exchange_keywords)
    to_is_exchange = any(kw in to_lower for kw in exchange_keywords)

    if from_is_exchange and not to_is_exchange:
        return "exchange_outflow", "轉出交易所（囤積訊號，偏多）"
    elif to_is_exchange and not from_is_exchange:
        return "exchange_inflow", "轉入交易所（賣壓訊號，偏空）"
    else:
        return "whale_transfer", "鯨魚間轉帳"


# ---------------------------------------------------------------------------
# WhaleAlertSource — 鏈上大額轉帳（kind = whale_onchain）
# ---------------------------------------------------------------------------

class WhaleAlertSource(Source):
    """Whale Alert API 連接器：追蹤鏈上大額轉帳。

    端點：GET https://api.whale-alert.io/v1/transactions
    參數：api_key, min_value, start, cursor
    環境變數：WHALE_ALERT_API_KEY（選用；無 key 時降級為離線模式靜默跳過）

    信號語義：
      - 交易所流出（exchange_outflow）→ 鯨魚囤積，偏多
      - 交易所流入（exchange_inflow）→ 鯨魚拋售，偏空
      - 鯨魚間轉帳（whale_transfer）→ 中性，僅供參考
    """

    kind = "whale_onchain"
    name = "whale-alert"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        api_key = os.environ.get(_WHALE_ALERT_KEY_ENV, "").strip()
        if not api_key:
            # 無 API key → 靜默回傳空（不報錯，由 collect() 的離線降級處理）
            return []

        # 只查最近 1 小時的交易（鯨魚信號時效性強）
        start_epoch = int(time.time()) - 3600

        params: dict[str, str | int] = {
            "api_key": api_key,
            "min_value": _MIN_VALUE_USD,
            "start": start_epoch,
        }

        # 若指定幣種，加入 currency 過濾
        if coin and coin.upper() in _SUPPORTED_COINS:
            params["currency"] = coin.lower()

        url = "https://api.whale-alert.io/v1/transactions?" + urlencode(params)
        raw = _fetch_url(url)
        data = json.loads(raw)

        if not isinstance(data, dict) or data.get("result") != "success":
            return []

        transactions = data.get("transactions", [])
        if not isinstance(transactions, list):
            return []

        docs: list[Document] = []
        for tx in transactions:
            if not isinstance(tx, dict):
                continue

            doc = self._parse_transaction(tx, coin)
            if doc is not None:
                docs.append(doc)

        return docs

    def _parse_transaction(self, tx: dict, target_coin: str) -> Document | None:
        """解析單筆 Whale Alert 交易回應為 Document。"""
        # 幣種驗證
        symbol = str(tx.get("symbol", "")).lower()
        coin_code = _WHALE_ALERT_SYMBOLS.get(symbol)
        if coin_code is None or coin_code not in _SUPPORTED_COINS:
            return None

        # 若指定目標幣，跳過非目標幣
        if target_coin and coin_code != target_coin.upper():
            return None

        # 金額驗證
        amount_usd = _finite_num(tx.get("amount_usd"), lo=_MIN_VALUE_USD)
        if amount_usd is None:
            return None

        amount = _finite_num(tx.get("amount"), lo=0)
        if amount is None:
            return None

        # 時間戳驗證
        ts = _finite_num(tx.get("timestamp"), lo=1_577_836_800)  # >= 2020-01-01
        if ts is None:
            ts = time.time()

        # 來源/目的地
        from_data = tx.get("from", {})
        to_data = tx.get("to", {})
        from_owner = str(from_data.get("owner", "unknown")) if isinstance(from_data, dict) else "unknown"
        to_owner = str(to_data.get("owner", "unknown")) if isinstance(to_data, dict) else "unknown"

        # 方向分類
        direction_tag, direction_desc = _classify_direction(from_owner, to_owner)

        # 產生描述文字（含方向詞，供 _infer_direction 推斷）
        amount_str = f"{amount:,.0f}" if amount >= 1 else f"{amount:.4f}"
        usd_str = f"{amount_usd:,.0f}"
        text = (
            f"{coin_code} 鯨魚大額轉帳：{amount_str} {coin_code}"
            f"（約 {usd_str} USD）從 {from_owner} 轉至 {to_owner}，"
            f"{direction_desc}"
        )

        # 交易 hash 作為去重 ID
        tx_hash = tx.get("hash", "")
        doc_id = "whale-alert-" + hashlib.md5(
            f"{tx_hash}-{coin_code}-{ts}".encode()
        ).hexdigest()[:12]

        # URL 不含 API key（安全：不洩漏 key）
        clean_url = f"https://whale-alert.io/transaction/{tx.get('blockchain', 'unknown')}/{tx_hash}"

        return Document(
            id=doc_id,
            kind=self.kind,
            source=self.name,
            text=text,
            url=clean_url,
            ts=ts,
            meta={
                "coin": coin_code,
                "amount_usd": amount_usd,
                "amount": amount,
                "direction": direction_tag,
                "from": from_owner,
                "to": to_owner,
                "content_reference": text[:120],
            },
        )


# ---------------------------------------------------------------------------
# ArkhamIntelSource — 名人/標記錢包交易（kind = celebrity_trade）
# ---------------------------------------------------------------------------

class ArkhamIntelSource(Source):
    """Arkham Intelligence 連接器：追蹤已標記錢包（名人/機構）交易。

    端點：GET https://api.arkhamintelligence.com/transfers
    參數：apiKey, base, usdGte
    環境變數：ARKHAM_API_KEY（選用；無 key 時降級為離線模式靜默跳過）

    信號語義：
      - verified_onchain=True → 鏈上已驗證，信譽 0.50
      - verified_onchain=False → 未驗證，自動降級至 social 等級 0.35
    """

    kind = "celebrity_trade"
    name = "arkham-intel"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        api_key = os.environ.get(_ARKHAM_KEY_ENV, "").strip()
        if not api_key:
            return []

        if coin and coin.upper() not in _SUPPORTED_COINS:
            return []

        params: dict[str, str | int] = {
            "apiKey": api_key,
            "usdGte": _MIN_VALUE_USD,
        }
        if coin:
            params["base"] = coin.lower()

        url = "https://api.arkhamintelligence.com/transfers?" + urlencode(params)
        raw = _fetch_url(url)
        data = json.loads(raw)

        if not isinstance(data, dict):
            return []

        transfers = data.get("transfers", [])
        if not isinstance(transfers, list):
            return []

        docs: list[Document] = []
        for transfer in transfers:
            if not isinstance(transfer, dict):
                continue
            doc = self._parse_transfer(transfer, coin)
            if doc is not None:
                docs.append(doc)

        return docs

    def _parse_transfer(self, transfer: dict, target_coin: str) -> Document | None:
        """解析單筆 Arkham 轉帳為 Document。"""
        # 幣種
        token = transfer.get("token", {})
        symbol = str(token.get("symbol", "")).upper() if isinstance(token, dict) else ""
        if symbol not in _SUPPORTED_COINS:
            return None
        if target_coin and symbol != target_coin.upper():
            return None

        # 金額
        amount_usd = _finite_num(transfer.get("unitValueUsd"), lo=_MIN_VALUE_USD)
        if amount_usd is None:
            return None

        # 時間戳
        block_ts = transfer.get("blockTimestamp")
        ts = _finite_num(block_ts, lo=1_577_836_800) if block_ts else None
        if ts is None:
            ts = time.time()

        # 實體標記（名人/機構名稱）
        from_entity = transfer.get("fromAddress", {})
        to_entity = transfer.get("toAddress", {})
        from_label = ""
        to_label = ""
        if isinstance(from_entity, dict):
            from_label = str(from_entity.get("arkhamLabel", "") or from_entity.get("address", "")[:10])
        if isinstance(to_entity, dict):
            to_label = str(to_entity.get("arkhamLabel", "") or to_entity.get("address", "")[:10])

        # 判斷是否鏈上驗證（有 Arkham 標記 = 已驗證）
        verified = bool(
            (isinstance(from_entity, dict) and from_entity.get("arkhamLabel"))
            or (isinstance(to_entity, dict) and to_entity.get("arkhamLabel"))
        )

        # 判斷買/賣方向
        # 如果「已標記實體」是 toAddress → 買入；是 fromAddress → 賣出
        entity_name = to_label if (isinstance(to_entity, dict) and to_entity.get("arkhamLabel")) else from_label
        if isinstance(to_entity, dict) and to_entity.get("arkhamLabel"):
            action = "buy"
            action_desc = "買入"
        else:
            action = "sell"
            action_desc = "賣出"

        # 方向詞（供 _infer_direction 推斷）
        if action == "buy":
            direction_word = f"（看漲訊號：名人{action_desc}）"
        else:
            direction_word = f"（看跌訊號：名人{action_desc}）"

        usd_str = f"{amount_usd:,.0f}"
        verified_str = "鏈上已驗證" if verified else "未經鏈上驗證"

        text = (
            f"已標記錢包（{entity_name}）{action_desc} {symbol}"
            f"（約 {usd_str} USD），{verified_str}"
            f"{direction_word}"
        )

        tx_hash = transfer.get("transactionHash", "")
        doc_id = "arkham-" + hashlib.md5(
            f"{tx_hash}-{symbol}-{ts}".encode()
        ).hexdigest()[:12]

        return Document(
            id=doc_id,
            kind=self.kind,
            source=self.name,
            text=text,
            url=f"https://platform.arkhamintelligence.com/explorer/tx/{tx_hash}",
            ts=ts,
            meta={
                "coin": symbol,
                "amount_usd": amount_usd,
                "verified_onchain": verified,
                "entity": entity_name,
                "action": action,
                "content_reference": text[:120],
            },
        )


# ---------------------------------------------------------------------------
# 工廠函式
# ---------------------------------------------------------------------------

def build_whale_sources() -> list[Source]:
    """回傳所有已啟用的鯨魚/名人交易連接器。"""
    return [WhaleAlertSource(), ArkhamIntelSource()]
