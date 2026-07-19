"""HOYA BIT connector with a fail-closed, organizer-configured contract.

No endpoint is guessed or bundled.  Until the official HTTPS ticker URL is
provided in ``TRUSTFORGE_HOYABIT_TICKER_URL`` this remains the old disabled
sample stub.  Once configured, the scheduler fetches it through the shared
SSRF-safe transport and emits first-party Evidence with explicit freshness.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from . import safe_fetch
from .base import Document, Source, get_source_enabled

_log = logging.getLogger(__name__)
_MAX_BYTES = 512 * 1024
_TIMEOUT = 8
_UA = "TrustForge/1.0 (HOYA BIT connector)"


def log_hoyabit_startup_status() -> bool:
    """Log whether the HOYA BIT online truth baseline is actually available.

    This check is deliberately configuration-only: startup must not probe an
    undocumented endpoint or expose its value.  ``False`` means operators must
    treat HOYA BIT online data as unavailable; the historical OHLCV dataset is
    a separate, explicitly labelled baseline.
    """
    endpoint_configured = bool(os.getenv("TRUSTFORGE_HOYABIT_TICKER_URL", "").strip())
    enabled = get_source_enabled(HoyaBitSource.name)
    if not endpoint_configured or not enabled:
        reasons = []
        if not endpoint_configured:
            reasons.append("TRUSTFORGE_HOYABIT_TICKER_URL 未設定")
        if not enabled:
            reasons.append("hoyabit-ticker disabled")
        _log.warning(
            "HOYA BIT 真值基準未接：%s；ticker 不會提供即時真實資料，"
            "depth/orderbook/trades 仍等待官方合約（issue #167）",
            "、".join(reasons),
        )
        return False
    return True


class HoyaBitSource(Source):
    kind = "hoyabit"
    name = "hoyabit-ticker"

    def __init__(self) -> None:
        super().__init__()
        self.endpoint = os.getenv("TRUSTFORGE_HOYABIT_TICKER_URL", "").strip()
        self.token = os.getenv("TRUSTFORGE_HOYABIT_API_TOKEN", "").strip()
        self.enabled = bool(self.endpoint) and get_source_enabled(self.name)
        self.last_attempts = 0
        self.last_failures = 0
        self.last_degraded = False

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    def _headers(self) -> dict[str, str] | None:
        return {"Authorization": f"Bearer {self.token}"} if self.token else None

    @staticmethod
    def _numeric(value: Any, keys: tuple[str, ...]) -> float | None:
        for key in keys:
            candidate = value.get(key) if isinstance(value, dict) else None
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return float(candidate)
            if isinstance(candidate, str):
                try:
                    return float(candidate)
                except ValueError:
                    pass
        return None

    def _extract(self, payload: Any, coin: str) -> tuple[float, float | None, dict[str, Any]]:
        # Contract adapters deliberately accept only common ticker envelopes;
        # malformed data fails closed and preserves the prior cache.
        entries: list[Any]
        if isinstance(payload, list):
            entries = payload
        elif isinstance(payload, dict):
            data = payload.get("data", payload.get("result", payload))
            entries = data if isinstance(data, list) else [data]
        else:
            raise ValueError("HOYA BIT response must be an object or list")
        target = coin.upper()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            symbol = str(entry.get("symbol", entry.get("market", entry.get("pair", "")))).upper()
            if symbol and target not in symbol:
                continue
            price = self._numeric(entry, ("last", "last_price", "price", "close"))
            if price is None or price <= 0:
                continue
            change = self._numeric(entry, ("change_24h", "price_change_percent_24h", "percent_change_24h"))
            return price, change, entry
        raise ValueError(f"HOYA BIT ticker has no valid {target} price")

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
        self.last_attempts += 1
        if not self.configured:
            return [Document(
                id="hoyabit-stub-placeholder", kind=self.kind, source=self.name,
                text="HOYA BIT connector stub — 未設定官方 API，此為佔位 sample 資料", url="", ts=0.0,
                meta={"sample": True, "stub": True, "disabled": not get_source_enabled(self.name),
                      "content_reference": "HOYA BIT connector stub（未設定官方 API）"},
            )]
        if not coin:
            raise ValueError("HOYA BIT connector requires an explicit coin")
        try:
            raw = safe_fetch.fetch_url(self.endpoint, user_agent=_UA, extra_headers=self._headers(), timeout=_TIMEOUT, max_bytes=_MAX_BYTES)
            price, change, raw_entry = self._extract(json.loads(raw), coin)
        except Exception:
            self.last_failures += 1
            self.last_degraded = True
            raise
        now = time.time()
        change_text = f"；24h {change:+.2f}%" if change is not None else ""
        return [Document(
            id=f"hoyabit-{coin.upper()}-{int(now)}", kind=self.kind, source=self.name,
            text=f"HOYA BIT {coin.upper()} ticker：價格 {price:g}{change_text}", url=self.endpoint, ts=now,
            meta={"live_source": True, "provider": "HOYA BIT", "coin": coin.upper(), "price": price,
                  "change_24h_pct": change, "raw_fields": sorted(raw_entry)[:32],
                  "content_reference": f"HOYA BIT official ticker {coin.upper()} price={price:g}"},
        )]

    def get_depth(self, coin: str) -> list[Document]:
        raise NotImplementedError("HOYA BIT depth contract is not configured")

    def get_orderbook(self, coin: str) -> list[Document]:
        raise NotImplementedError("HOYA BIT orderbook contract is not configured")

    def get_trades(self, coin: str) -> list[Document]:
        raise NotImplementedError("HOYA BIT trades contract is not configured")


def build_hoyabit_sources() -> list[Source]:
    return [HoyaBitSource()]
