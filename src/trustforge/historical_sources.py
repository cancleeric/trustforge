"""Capabilities and parsers for point-in-time historical source backfill."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
import re
import unicodedata

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


def historical_coverage_report(backend, start: date, end: date) -> dict[str, Any]:
    """Measure actual daily archives; capability labels never count as data."""
    if end < start:
        raise ValueError("end must be on or after start")
    from .replay import load_source_snapshot

    capabilities = {item["source"]: dict(item) for item in HISTORICAL_SOURCE_CAPABILITIES}
    total_days = (end - start).days + 1
    by_coin: dict[str, dict[str, Any]] = {}
    observed_sources: set[str] = set()
    for coin in COIN_POOL:
        snapshot_days = 0
        missing_dates: list[str] = []
        coin_observed_sources: set[str] = set()
        source_days: dict[str, int] = {}
        document_count: dict[str, int] = {}
        day = start
        while day <= end:
            snapshot = load_source_snapshot(
                backend, coin, day.isoformat(), archive_type="backfilled_archive",
            )
            if snapshot is not None:
                snapshot_days += 1
                for source in snapshot.get("sources", []):
                    if not isinstance(source, dict) or not source.get("source"):
                        continue
                    source_id = str(source["source"])
                    observed_sources.add(source_id)
                    coin_observed_sources.add(source_id)
                    source_days[source_id] = source_days.get(source_id, 0) + 1
                    documents = source.get("documents")
                    if isinstance(documents, list):
                        document_count[source_id] = document_count.get(source_id, 0) + len(documents)
            else:
                missing_dates.append(day.isoformat())
            day += timedelta(days=1)
        by_coin[coin] = {
            "expected_days": total_days,
            "snapshot_days": snapshot_days,
            "snapshot_coverage": round(snapshot_days / total_days, 6),
            "missing_dates": missing_dates,
            "sources": {
                source_id: {
                    "days": source_days.get(source_id, 0),
                    "coverage": round(source_days.get(source_id, 0) / total_days, 6),
                    "documents": document_count.get(source_id, 0),
                }
                for source_id in sorted(set(capabilities) | coin_observed_sources)
            },
        }
    return {
        "from_date": start.isoformat(), "to_date": end.isoformat(),
        "expected_days": total_days, "coins": by_coin,
        "capabilities": [
            {**capabilities.get(source_id, {
                "source": source_id, "kind": "unknown", "strategy": "unregistered",
                "status": "unregistered", "coverage": "measured_archive_only", "terms": "unknown",
            }), "observed": source_id in observed_sources}
            for source_id in sorted(set(capabilities) | observed_sources)
        ],
    }


_SEC_KEYWORDS = {"BTC": ("bitcoin",), "ETH": ("ethereum",)}
_SEC_MARKET_KEYWORDS = ("crypto", "blockchain", "digital asset")
_SAFE_EXTERNAL_RE = re.compile(r"[^\w\s.,&'()\-+/]", re.UNICODE)
_FNG_CLASSIFICATIONS = {
    "extreme fear": "Extreme Fear", "fear": "Fear", "neutral": "Neutral",
    "greed": "Greed", "extreme greed": "Extreme Greed",
}


def _safe_external_label(value: Any, *, max_length: int, fallback: str = "unknown") -> str:
    """Bound an upstream label before it can enter a stored text template."""
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = " ".join(normalized.split())
    normalized = _SAFE_EXTERNAL_RE.sub("", normalized).strip()
    return normalized[:max_length].rstrip() or fallback


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
        company = _safe_external_label(company, max_length=160)
        form = _safe_external_label(form, max_length=24)
        cik = _safe_external_label(cik, max_length=20)
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,240}", filename):
            continue
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
        raw_classification = _safe_external_label(
            entry.get("value_classification", "unknown"), max_length=32,
        )
        classification = _FNG_CLASSIFICATIONS.get(raw_classification.casefold(), "unknown")
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
