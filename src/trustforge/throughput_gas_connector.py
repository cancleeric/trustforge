"""Observed TPS and gas metric connector parsers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse

from trustforge.ingestion import safe_fetch
from trustforge.peer_metrics import MetricValue, PeerMetricMethod

ALLOWED_NETWORK_METRIC_HOSTS = frozenset({"arbiscan.io", "api.arbiscan.io", "etherscan.io"})
MAX_NETWORK_METRIC_AGE = timedelta(hours=6)
ALLOWED_TX_TYPES = frozenset({"transfer", "swap", "bridge", "contract_call"})
_MAX_BYTES = 64 * 1024
_TIMEOUT = 5
_UA = "TrustForge/1.0 (network-metrics)"


@dataclass(frozen=True)
class ObservedGasMetric:
    metric: MetricValue
    native_fee: float
    usd_fee: float
    tx_type: str
    observed_at: datetime
    source: str

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric.to_dict(),
            "native_fee": self.native_fee,
            "usd_fee": self.usd_fee,
            "tx_type": self.tx_type,
            "observed_at": self.observed_at.isoformat(),
            "source": self.source,
        }


@dataclass(frozen=True)
class TpsConnectorResult:
    metric: MetricValue | None
    error: dict[str, str] | None

    @property
    def ok(self) -> bool:
        return self.metric is not None and self.error is None


@dataclass(frozen=True)
class GasConnectorResult:
    metric: ObservedGasMetric | None
    error: dict[str, str] | None

    @property
    def ok(self) -> bool:
        return self.metric is not None and self.error is None


def fetch_tps_metric(
    url: str,
    *,
    fetched_at: datetime,
) -> TpsConnectorResult:
    try:
        _validate_fetch_source_url(url)
        raw = _fetch_network_metric(url)
        payload = _decode_network_metric(raw)
        if "source" in payload and payload["source"] != url:
            raise ValueError("TPS payload source does not match fetched URL")
        payload.setdefault("source", url)
        return TpsConnectorResult(metric=parse_observed_tps_metric(payload, fetched_at=fetched_at), error=None)
    except HTTPError as exc:
        if exc.code == 429:
            return TpsConnectorResult(
                metric=None,
                error={"code": "rate_limited", "message": "TPS source rate limited"},
            )
        return TpsConnectorResult(metric=None, error={"code": "tps_connector_error", "message": str(exc)})
    except Exception as exc:
        return TpsConnectorResult(metric=None, error={"code": "tps_connector_error", "message": str(exc)})


def fetch_gas_metric(
    url: str,
    *,
    fetched_at: datetime,
) -> GasConnectorResult:
    try:
        _validate_fetch_source_url(url)
        raw = _fetch_network_metric(url)
        payload = _decode_network_metric(raw)
        if "source" in payload and payload["source"] != url:
            raise ValueError("Gas payload source does not match fetched URL")
        payload.setdefault("source", url)
        return GasConnectorResult(metric=parse_gas_metric(payload, fetched_at=fetched_at), error=None)
    except HTTPError as exc:
        if exc.code == 429:
            return GasConnectorResult(
                metric=None,
                error={"code": "rate_limited", "message": "Gas source rate limited"},
            )
        return GasConnectorResult(metric=None, error={"code": "gas_connector_error", "message": str(exc)})
    except Exception as exc:
        return GasConnectorResult(metric=None, error={"code": "gas_connector_error", "message": str(exc)})


def parse_observed_tps_metric(payload: dict, *, fetched_at: datetime) -> MetricValue:
    _ensure_fetched_at(fetched_at)
    required = {"asset_id", "observed_tps", "observed_at", "source"}
    _validate_required(payload, required, "TPS")
    _reject_extra(payload, required, "TPS")
    _validate_source(payload["source"])
    observed_at = _parse_observed_at(payload["observed_at"])
    _reject_stale_or_future(observed_at, fetched_at, "TPS")
    value = _finite_non_negative(payload["observed_tps"], "TPS value")
    return MetricValue(value=value, unit="count/s", method=PeerMetricMethod.OBSERVED, source=payload["source"])


def parse_gas_metric(payload: dict, *, fetched_at: datetime) -> ObservedGasMetric:
    required = {"asset_id", "native_fee", "usd_fee", "tx_type", "observed_at", "source"}
    _ensure_fetched_at(fetched_at)
    _validate_required(payload, required, "Gas")
    _reject_extra(payload, required, "Gas")
    source = payload["source"]
    _validate_source(source)
    observed_at = _parse_observed_at(payload["observed_at"])
    _reject_stale_or_future(observed_at, fetched_at, "Gas")
    native_fee = _finite_non_negative(payload["native_fee"], "Gas native_fee")
    usd_fee = _finite_non_negative(payload["usd_fee"], "Gas usd_fee")
    tx_type = payload["tx_type"]
    if not isinstance(tx_type, str) or tx_type not in ALLOWED_TX_TYPES:
        raise ValueError("Gas tx_type must be approved transaction type")
    return ObservedGasMetric(
        metric=MetricValue(value=usd_fee, unit="usd", method=PeerMetricMethod.OBSERVED, source=source),
        native_fee=native_fee,
        usd_fee=usd_fee,
        tx_type=tx_type,
        observed_at=observed_at,
        source=source,
    )


def _validate_fetch_source_url(source: str) -> None:
    parsed = urlparse(source)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("network metric fetch URL must use https without credentials")
    if parsed.hostname not in ALLOWED_NETWORK_METRIC_HOSTS:
        raise ValueError(f"network metric fetch host is not allowed: {parsed.hostname}")


def _fetch_network_metric(url: str) -> bytes:
    raw = safe_fetch.fetch_url(url, user_agent=_UA, timeout=_TIMEOUT, max_bytes=_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("network metric response exceeds maximum size")
    return raw


def _decode_network_metric(raw: bytes) -> dict[str, Any]:
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("network metric payload must be a JSON object")
    return payload


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
    parsed = urlparse(source)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("network metric source URL must use https without credentials")
    if parsed.hostname not in ALLOWED_NETWORK_METRIC_HOSTS:
        raise ValueError(f"network metric source host is not allowed: {parsed.hostname}")


def _parse_observed_at(raw: object) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("observed_at must be ISO timestamp string")
    timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    return timestamp.astimezone(timezone.utc)


def _reject_stale_or_future(observed_at: datetime, fetched_at: datetime, label: str) -> None:
    age = fetched_at.astimezone(timezone.utc) - observed_at
    if age < timedelta(0):
        raise ValueError(f"{label} observation is in the future")
    if age > MAX_NETWORK_METRIC_AGE:
        raise ValueError(f"{label} observation is stale")


def _finite_non_negative(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise ValueError(f"{field_name} must be finite non-negative number")
    return float(value)
