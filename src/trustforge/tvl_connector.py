"""TVL connector parsing with strict source and payload validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite
from urllib.parse import urlparse

from trustforge.peer_metrics import MetricValue, PeerMetricMethod

ALLOWED_TVL_HOSTS = frozenset({"api.llama.fi", "defillama.com"})
MAX_TVL_AGE = timedelta(hours=24)


def parse_tvl_metric(payload: dict, *, fetched_at: datetime) -> MetricValue:
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
    host = urlparse(source).hostname
    if host not in ALLOWED_TVL_HOSTS:
        raise ValueError(f"TVL source host is not allowed: {host}")

    value = payload["tvl_usd"]
    if not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise ValueError("TVL value must be finite non-negative number")

    observed_at = _parse_timestamp(payload["observed_at"], "observed_at")
    if fetched_at.astimezone(timezone.utc) - observed_at > MAX_TVL_AGE:
        raise ValueError("TVL observation is stale")

    return MetricValue(
        value=float(value),
        unit="usd",
        method=PeerMetricMethod.OBSERVED,
        source=source,
    )


def _parse_timestamp(raw: object, field_name: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"TVL {field_name} must be ISO timestamp string")
    timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError(f"TVL {field_name} must be timezone-aware")
    return timestamp.astimezone(timezone.utc)
