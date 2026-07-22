"""Dependency-neutral source identity normalization."""

from __future__ import annotations


_SOURCE_ALIASES = {
    "coindesk.com": "coindesk",
    "cointelegraph.com": "cointelegraph",
    "theblock.co": "theblock",
    "theblock": "theblock",
    "reuters.com": "reuters",
    "bloomberg.com": "bloomberg",
    "bitcoinmagazine.com": "bitcoinmagazine",
    "newsbtc.com": "newsbtc",
    "cryptoslate.com": "cryptoslate",
    "decrypt.co": "decrypt",
    "utoday.com": "utoday",
    "twitter": "x",
    "x.com": "x",
    "sec edgar": "sec-gov",
    "sec": "sec-gov",
    "sec.gov": "sec-gov",
}


def canonical_source(source: str | None) -> str:
    """Return the single canonical identity used throughout the core."""
    if not source:
        return ""
    key = source.strip().casefold()
    return _SOURCE_ALIASES.get(key, key) if key else ""


__all__ = ["canonical_source"]
