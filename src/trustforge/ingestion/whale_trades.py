"""名人/鯨魚交易信號連接器（Celebrity & Whale Trade Signals）。

追蹤鏈上大額轉帳（鯨魚）與公開知名交易者動向，作為信心參考的佐證型信號。

來源白名單（寫死，防 SSRF）：
  - Whale Alert 大額轉帳  https://api.whale-alert.io/v1/transactions
  - Arkham Intelligence    https://api.arkm.com/transfers

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
import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

from . import safe_fetch
from ..whale_alert_secret import resolve_api_key
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

# Arkham chain identifiers used by the v1 transfers endpoint.
_ARKHAM_COIN_CHAINS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "bsc",
    "XRP": "xrp",
    "ARB": "arbitrum",
}

# API key 環境變數名稱
_WHALE_ALERT_KEY_ENV = "WHALE_ALERT_API_KEY"
_ARKHAM_KEY_ENV = "ARKHAM_API_KEY"
_ARKHAM_LIMIT_ENV = "TRUSTFORGE_ARKHAM_TRANSFER_LIMIT"
_ARKHAM_DEFAULT_LIMIT = 20
_ARKHAM_MAX_LIMIT = 20
_ARKHAM_MIN_INTERVAL_SECONDS = 1.0
_ARKHAM_TX_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_ARKHAM_SYMBOL_RE = re.compile(r"^[A-Z0-9._-]{1,24}$")

_log = logging.getLogger(__name__)
_arkham_throttle_lock = threading.Lock()
_arkham_last_request_monotonic: float | None = None


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


def _parse_iso_timestamp(value: object) -> float | None:
    """Parse an ISO-8601 timestamp as a finite UTC Unix timestamp."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    timestamp = parsed.astimezone(timezone.utc).timestamp()
    return timestamp if math.isfinite(timestamp) and timestamp >= 1_577_836_800 else None


def _safe_attribution_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if (
        candidate
        and len(candidate) <= 120
        and all(character.isprintable() for character in candidate)
    ):
        return candidate
    return ""


def _attribution_name(value: object) -> str:
    direct = _safe_attribution_text(value)
    if direct or not isinstance(value, dict):
        return direct
    for key in ("name", "label"):
        candidate = _safe_attribution_text(value.get(key))
        if candidate:
            return candidate
    return ""


def _extract_entity_name(address: object) -> str:
    """Return Arkham entity/label name, falling back to a short address."""
    if not isinstance(address, dict):
        return "unknown"
    for key in ("arkhamEntity", "entity", "arkhamLabel", "label"):
        candidate = _attribution_name(address.get(key))
        if candidate:
            return candidate
    raw_address = address.get("address")
    if isinstance(raw_address, str) and raw_address.strip():
        return raw_address.strip()[:10]
    return "unknown"


def _has_arkham_attribution(address: object) -> bool:
    """Return whether an address contains a non-empty Arkham entity or label."""
    if not isinstance(address, dict):
        return False
    return any(
        bool(_attribution_name(address.get(key)))
        for key in ("arkhamEntity", "entity", "arkhamLabel", "label")
    )


def _has_arkham_entity(address: object) -> bool:
    """Labels describe wallets; only entity attribution supports this source kind."""
    if not isinstance(address, dict):
        return False
    return any(
        bool(_attribution_name(address.get(key)))
        for key in ("arkhamEntity", "entity")
    )


def _arkham_transfer_limit() -> int:
    """Return a bounded result limit so live probes can control credit usage."""
    raw = os.environ.get(_ARKHAM_LIMIT_ENV, "").strip()
    if not raw:
        return _ARKHAM_DEFAULT_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return _ARKHAM_DEFAULT_LIMIT
    return min(max(value, 1), _ARKHAM_MAX_LIMIT)


def _arkham_parties(transfer: dict, singular: str, plural: str) -> list[dict]:
    """Normalize account-based and UTXO party fields without retaining junk."""
    one = transfer.get(singular)
    if isinstance(one, dict):
        return [one]
    many = transfer.get(plural)
    if not isinstance(many, list):
        return []
    return [party for party in many if isinstance(party, dict)]


def _first_attributed_party(parties: list[dict]) -> dict | None:
    return next((party for party in parties if _has_arkham_entity(party)), None)


def _reset_arkham_throttle_for_tests() -> None:
    global _arkham_last_request_monotonic
    with _arkham_throttle_lock:
        _arkham_last_request_monotonic = None


def _throttle_arkham_request() -> None:
    """Enforce Arkham's documented one-request-per-second endpoint limit."""
    global _arkham_last_request_monotonic
    with _arkham_throttle_lock:
        now = time.monotonic()
        if _arkham_last_request_monotonic is not None:
            wait = _ARKHAM_MIN_INTERVAL_SECONDS - (
                now - _arkham_last_request_monotonic
            )
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
        _arkham_last_request_monotonic = now


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
        api_key, _key_source = resolve_api_key()
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

    端點：GET https://api.arkm.com/transfers
    認證：API-Key header
    參數：usdGte, timeLast, limit, chains
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
            "usdGte": _MIN_VALUE_USD,
            "timeLast": "1h",
            "limit": _arkham_transfer_limit(),
        }
        if coin:
            params["chains"] = _ARKHAM_COIN_CHAINS[coin.upper()]

        url = "https://api.arkm.com/transfers?" + urlencode(params)
        _throttle_arkham_request()
        raw = _fetch_url(url, extra_headers={"API-Key": api_key})
        data = json.loads(raw)

        if not isinstance(data, dict):
            return []

        transfers = data.get("transfers", [])
        if not isinstance(transfers, list):
            return []

        docs: list[Document] = []
        rejected = 0
        for transfer in transfers:
            if not isinstance(transfer, dict):
                rejected += 1
                continue
            doc = self._parse_transfer(transfer, coin)
            if doc is not None:
                docs.append(doc)
            else:
                rejected += 1

        _log.info(
            "Arkham transfer parse summary: returned=%d accepted=%d rejected=%d",
            len(transfers), len(docs), rejected,
        )

        return docs

    def _parse_transfer(self, transfer: dict, target_coin: str) -> Document | None:
        """解析單筆 Arkham 轉帳為 Document。"""
        # `chains=` scopes the network, not the asset.  Keep the requested
        # TrustForge coin as the chain scope and preserve the provider's actual
        # token symbol separately (e.g. WETH on Ethereum).  UTXO transfers such
        # as Bitcoin have no tokenSymbol at all.
        target = target_coin.upper() if target_coin else ""
        chain = transfer.get("chain")
        if not isinstance(chain, str) or not chain.strip():
            return None
        chain = chain.strip().lower()
        if target and _ARKHAM_COIN_CHAINS.get(target) != chain:
            return None

        raw_symbol = transfer.get("tokenSymbol")
        asset_symbol = raw_symbol.strip().upper() if isinstance(raw_symbol, str) else ""
        if asset_symbol and not _ARKHAM_SYMBOL_RE.fullmatch(asset_symbol):
            return None
        coin_scope = target or next(
            (coin for coin, provider_chain in _ARKHAM_COIN_CHAINS.items()
             if provider_chain == chain),
            "",
        )
        if coin_scope not in _SUPPORTED_COINS:
            return None
        if not asset_symbol:
            asset_symbol = coin_scope

        # 金額
        amount_usd = _finite_num(transfer.get("historicalUSD"), lo=_MIN_VALUE_USD)
        if amount_usd is None:
            return None

        # 時間戳
        ts = _parse_iso_timestamp(transfer.get("blockTimestamp"))
        if ts is None:
            return None

        # Account-based chains use singular fields; UTXO chains use lists.
        from_parties = _arkham_parties(transfer, "fromAddress", "fromAddresses")
        to_parties = _arkham_parties(transfer, "toAddress", "toAddresses")
        if not from_parties or not to_parties:
            return None

        from_attributed = _first_attributed_party(from_parties)
        to_attributed = _first_attributed_party(to_parties)
        if from_attributed is None and to_attributed is None:
            # This source represents attributed-wallet activity.  An ordinary
            # large transfer is not evidence of a celebrity/institution trade.
            return None

        if to_attributed is not None and from_attributed is None:
            entity_name = _extract_entity_name(to_attributed)
            relation_desc = "轉入"
        elif from_attributed is not None and to_attributed is None:
            entity_name = _extract_entity_name(from_attributed)
            relation_desc = "轉出"
        else:
            entity_name = (
                f"{_extract_entity_name(from_attributed)} → "
                f"{_extract_entity_name(to_attributed)}"
            )
            relation_desc = "實體間轉移"

        # `/transfers` proves movement, not a trade.  Custody shuffles,
        # wrapping, bridges and staking must never become buy/sell evidence.
        action = "transfer"
        direction_word = "（中性鏈上活動，不代表買賣）"

        usd_str = f"{amount_usd:,.0f}"
        text = (
            f"Arkham 實體（{entity_name}）{relation_desc} {asset_symbol}"
            f"（{chain} 鏈，約 {usd_str} USD），鏈上已驗證"
            f"{direction_word}"
        )

        tx_hash = transfer.get("transactionHash") or transfer.get("txid")
        if not isinstance(tx_hash, str) or not tx_hash.strip():
            return None
        tx_hash = tx_hash.strip()
        if not _ARKHAM_TX_ID_RE.fullmatch(tx_hash):
            return None
        doc_id = "arkham-" + hashlib.md5(
            f"{tx_hash}-{chain}-{asset_symbol}-{ts}".encode()
        ).hexdigest()[:12]

        return Document(
            id=doc_id,
            kind=self.kind,
            source=self.name,
            text=text,
            url=f"https://platform.arkhamintelligence.com/explorer/tx/{tx_hash}",
            ts=ts,
            meta={
                "coin": coin_scope,
                "chain": chain,
                "asset_symbol": asset_symbol,
                "amount_usd": amount_usd,
                "verified_onchain": True,
                "attributed": True,
                "attribution_type": "entity",
                "direction": "neutral",
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
