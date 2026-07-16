"""Capabilities and parsers for point-in-time historical source backfill."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .schema import COIN_POOL, iso_utc


HISTORICAL_SOURCE_CAPABILITIES = (
    {"source": "sec-gov", "kind": "regulatory", "strategy": "official_quarterly_master_index", "status": "ready_partial", "coverage": "metadata_only_since_1994Q3", "terms": "SEC public data and automated-access policy"},
    {"source": "alternative-me-fng", "kind": "sentiment", "strategy": "full_history_api", "status": "ready", "coverage": "provider_available_history", "terms": "Alternative.me attribution required"},
    {"source": "coingecko-market-range", "kind": "market", "strategy": "dated_range_api", "status": "credential_gated", "coverage": "plan_dependent", "terms": "CoinGecko API plan terms"},
    {"source": "news-rss-group", "kind": "news", "strategy": "provider_archive_or_licensed_dataset", "status": "archive_required", "coverage": "rss_is_recent_only", "terms": "per-publisher terms"},
    {"source": "reddit", "kind": "social", "strategy": "official_archive_or_licensed_dataset", "status": "archive_required", "coverage": "rss_is_recent_only", "terms": "Reddit data terms"},
    {"source": "onchain-current-group", "kind": "onchain", "strategy": "historical_chart_block_or_dataset_api", "status": "historical_endpoint_required", "coverage": "current_endpoints_are_not_history", "terms": "per-provider terms"},
    {"source": "hoyabit-ticker", "kind": "market", "strategy": "official_contract", "status": "blocked", "coverage": "unknown", "terms": "official endpoint and contract required"},
)


def historical_source_capabilities() -> list[dict[str, str]]:
    return [dict(item) for item in HISTORICAL_SOURCE_CAPABILITIES]


_SEC_KEYWORDS = {"BTC": ("bitcoin",), "ETH": ("ethereum",)}
_SEC_MARKET_KEYWORDS = ("crypto", "blockchain", "digital asset")


def parse_sec_master_index(text: str, *, retrieved_at: float,
                           start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    """Parse SEC's official master index without claiming full-text coverage."""
    rows: list[dict[str, Any]] = []
    retrieved = iso_utc(retrieved_at)
    in_records = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not in_records:
            if line.startswith("---"):
                in_records = True
            continue
        parts = line.split("|", 4)
        if len(parts) != 5:
            continue
        cik, company, form, filed, filename = (part.strip() for part in parts)
        try:
            filed_at = datetime.strptime(filed, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        if not start_epoch <= filed_at <= end_epoch:
            continue
        haystack = f"{company} {form}".lower()
        coins = {coin for coin, keywords in _SEC_KEYWORDS.items() if any(word in haystack for word in keywords)}
        if any(word in haystack for word in _SEC_MARKET_KEYWORDS):
            coins.update(COIN_POOL)
        if not coins:
            continue
        accession = filename.rsplit("/", 1)[-1].removesuffix(".txt")
        url = f"https://www.sec.gov/Archives/{filename.lstrip('/')}"
        for coin in sorted(coins):
            rows.append({
                "coin": coin, "source": "sec-gov", "kind": "regulatory",
                "published_at": iso_utc(filed_at), "retrieved_at": retrieved,
                "text": f"SEC EDGAR filing metadata: {company} filed {form} ({accession})",
                "url": url, "provider": "SEC EDGAR", "license": "U.S. public record; comply with SEC automated-access policy",
                "scope": "asset" if len(coins) == 1 else "market-wide", "match_scope": "metadata_only",
                "cik": cik, "company": company, "form": form, "accession": accession,
            })
    return sorted(rows, key=lambda row: (row["published_at"], row["coin"], row["accession"]))


def parse_alternative_me_history(payload: dict[str, Any], *, retrieved_at: float,
                                 start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    """Convert provider history to provenance-complete import rows."""
    rows: list[dict[str, Any]] = []
    retrieved = iso_utc(retrieved_at)
    for entry in payload.get("data", []):
        if not isinstance(entry, dict):
            continue
        try:
            timestamp = float(entry.get("timestamp", 0))
            value = int(entry.get("value", ""))
        except (TypeError, ValueError):
            continue
        if not start_epoch <= timestamp <= end_epoch or not 0 <= value <= 100:
            continue
        published = iso_utc(timestamp)
        classification = str(entry.get("value_classification", "unknown"))
        for coin in COIN_POOL:
            rows.append({
                "coin": coin, "source": "alternative-me-fng", "kind": "sentiment",
                "published_at": published, "retrieved_at": retrieved,
                "text": f"Crypto Fear & Greed Index: {value} ({classification})",
                "url": "https://alternative.me/crypto/fear-and-greed-index/",
                "provider": "Alternative.me", "license": "Public API; attribution required; verify provider terms",
                "scope": "market-wide", "value": value, "classification": classification,
            })
    return sorted(rows, key=lambda row: (row["published_at"], row["coin"]))
