"""Observed TPS and gas metric connector parsers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite
from urllib.parse import urlparse

from trustforge.peer_metrics import MetricValue, PeerMetricMethod

ALLOWED_NETWORK_METRIC_HOSTS = frozenset({"arbiscan.io", "api.arbiscan.io", "etherscan.io"})
MAX_NETWORK_METRIC_AGE = timedelta(hours=6)


def parse_observed_tps_metric(payload: dict, *, fetched_at: datetime) -> MetricValue:
    _ensure_fetched_at(fetched_at)
    _validate_required(payload, {"asset_id", "observed_tps", "observed_at", "source"}, "TPS")
    _reject_extra(payload, {"asset_id", "observed_tps", "observed_at", "source"}, "TPS")
    _validate_source(payload["source"])
    observed_at = _parse_observed_at(payload["observed_at"])
    _reject_stale(observed_at, fetched_at, "TPS")
    value = _finite_non_negative(payload["observed_tps"], "TPS value")
    return MetricValue(value=value, unit="count/s", method=PeerMetricMethod.OBSERVED, source=payload["source"])


def parse_gas_metric(payload: dict, *, fetched_at: datetime) -> MetricValue:
    required = {"asset_id", "native_fee", "usd_fee", "tx_type", "observed_at", "source"}
    _ensure_fetched_at(fetched_at)
    _validate_required(payload, required, "Gas")
    _reject_extra(payload, required, "Gas")
    _validate_source(payload["source"])
    observed_at = _parse_observed_at(payload["observed_at"])
    _reject_stale(observed_at, fetched_at, "Gas")
    native_fee = _finite_non_negative(payload["native_fee"], "Gas native_fee")
    usd_fee = _finite_non_negative(payload["usd_fee"], "Gas usd_fee")
    tx_type = payload["tx_type"]
    if not isinstance(tx_type, str) or not tx_type.strip():
        raise ValueError("Gas tx_type must be non-empty string")
    return MetricValue(
        value=usd_fee,
        unit=f"usd/{tx_type}",
        method=PeerMetricMethod.OBSERVED,
        source=f"{payload['source']}#native={native_fee}",
    )


def _ensure_fetched_at(fetched_at: datetime) -> None:
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")


def _validate_required(payload: dict, required: set[str], label: str) -> None:
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"missing {label} fields: {', '.join(missing)}")


def _reject_extra(payload: dict, allowed: set[str], label: str) -> None:
    extra = sorted(set(payload) - allowed)
    if extra:
        raise ValueError(f"unexpected {label} fields: {', '.join(extra)}")


def _validate_source(source: object) -> None:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("network metric source must be non-empty string")
    host = urlparse(source).hostname
    if host not in ALLOWED_NETWORK_METRIC_HOSTS:
        raise ValueError(f"network metric source host is not allowed: {host}")


def _parse_observed_at(raw: object) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("observed_at must be ISO timestamp string")
    timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    return timestamp.astimezone(timezone.utc)


def _reject_stale(observed_at: datetime, fetched_at: datetime, label: str) -> None:
    if fetched_at.astimezone(timezone.utc) - observed_at > MAX_NETWORK_METRIC_AGE:
        raise ValueError(f"{label} observation is stale")


def _finite_non_negative(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise ValueError(f"{field_name} must be finite non-negative number")
    return float(value)
