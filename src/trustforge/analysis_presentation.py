"""Agent-owned, transport-neutral projection helpers for analysis results."""

from __future__ import annotations

from ._version import VERSION


def public_evidence_dict(evidence) -> dict:
    result = evidence.to_dict()
    result.pop("author", None)
    return result


def aggregate_trust_components(evidence: list) -> dict:
    keys = ("reputation", "corroboration", "recency", "manipulation")
    sums = {key: 0.0 for key in keys}
    counts = {key: 0 for key in keys}
    for item in evidence:
        components = getattr(item, "trust_components", None) or {}
        for key in keys:
            value = components.get(key)
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            sums[key] += numeric
            counts[key] += 1
    return {
        key: round(sums[key] / counts[key], 3) if counts[key] else None
        for key in keys
    }


def price_provenance_data(evidence: list) -> dict:
    ohlcv = next((item for item in evidence if item.source == "ohlcv-csv"), None)
    live = next((item for item in evidence if item.source == "coingecko-price"), None)
    result: dict = {}
    if ohlcv is not None:
        result["ohlcv"] = {
            "content_reference": ohlcv.content_reference,
            "fetched_at": ohlcv.fetched_at,
            "source_url": ohlcv.source_url,
            "data_lineage": getattr(ohlcv, "data_lineage", None),
        }
    if live is not None:
        result["live"] = {
            "content_reference": live.content_reference,
            "fetched_at": live.fetched_at,
            "source_url": live.source_url,
        }
    return result
