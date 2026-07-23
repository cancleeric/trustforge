"""TVL connector with SSRF-safe fetch boundary and strict validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Callable, Any
from urllib.parse import urlparse

from trustforge.peer_metrics import MetricValue, PeerMetricMethod

ALLOWED_TVL_HOSTS = frozenset({"api.llama.fi", "defillama.com"})
MAX_TVL_AGE = timedelta(hours=24)


@dataclass(frozen=True)
class TvlConnectorResult:
    metric: MetricValue | None
    error: dict[str, str] | None

    @property
    def ok(self) -> bool:
        return self.metric is not None and self.error is None


def fetch_tvl_metric(
    url: str,
    *,
    fetched_at: datetime,
    fetch_json: Callable[[str], dict[str, Any]],
) -> TvlConnectorResult:
    try:
        _validate_source_url(url)
        payload = fetch_json(url)
        payload.setdefault("source", url)
        return TvlConnectorResult(metric=parse_tvl_metric(payload, fetched_at=fetched_at), error=None)
    except Exception as exc:
        return TvlConnectorResult(metric=None, error={"code": "tvl_connector_error", "message": str(exc)})


def parse_tvl_metric(payload: dict[str, Any], *, fetched_at: datetime) -> MetricValue:
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    required = {"asset_id", "tvl_usd", "observed_at", "source"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"missing TVL fields: {', '.join(missing)}")
    extra = sorted(set(payload) - required)
    if extra:
        raise ValueError(f"unexpected TVL fields: {', '.join(extra)}")

    source = payload["source"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError("TVL source must be non-empty string")
    _validate_source_url(source)

    value = payload["tvl_usd"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise ValueError("TVL value must be finite non-negative number")

    observed_at = _parse_timestamp(payload["observed_at"], "observed_at")
    age = fetched_at.astimezone(timezone.utc) - observed_at
    if age < timedelta(0):
        raise ValueError("TVL observation is in the future")
    if age > MAX_TVL_AGE:
        raise ValueError("TVL observation is stale")

    return MetricValue(
        value=float(value),
        unit="usd",
        method=PeerMetricMethod.OBSERVED,
        source=source,
    )


def _validate_source_url(source: str) -> None:
    parsed = urlparse(source)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("TVL source URL must use https without credentials")
    if parsed.hostname not in ALLOWED_TVL_HOSTS:
        raise ValueError(f"TVL source host is not allowed: {parsed.hostname}")


def _parse_timestamp(raw: object, field_name: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"TVL {field_name} must be ISO timestamp string")
    timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError(f"TVL {field_name} must be timezone-aware")
    return timestamp.astimezone(timezone.utc)
