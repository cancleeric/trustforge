"""Pure, explainable shadow contribution for asset-intrinsic facts.

The result is observational only.  Nothing in this module imports or mutates
the trust scorer, calibration, decision state, ranking, or market judgment.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from trustforge.asset_intrinsic import (
    INTRINSIC_DIMENSION_NAMES,
    STALE_WINDOW_DAYS,
    AssetIntrinsicView,
    IntrinsicDimension,
    IntrinsicFactStatus,
)

ASSESSMENT_SCHEMA_VERSION = "1.0.0"
DIMENSION_WEIGHT = 0.032
TOTAL_DELTA_CAP = 0.08
REQUIRED_KNOWN_DIMENSIONS = 3
REQUIRED_SOURCE_FAMILIES = 2


_FORBIDDEN_INFERENCE_PATTERNS: list[tuple[str, str]] = [
    (
        r"(price|market\s*cap|trading\s*volume|exchange\s*rate|价格).*(infer|推|derive|導出|trust|score|confidence)",
        "price-inferred",
    ),
    (
        r"\b(lost|dormant|inaccessible|dead)\s.*\b(coin|key|wallet|address|私鑰|錢包|地址)",
        "lost-key estimates",
    ),
    (
        r"(address_cluster|address|地址).*(represents|equals|maps\s+to|is\s+the\s+same\s+as|is|==|=|＝|代表|等同於|映射).*(entity|実体|entity|實體)",
        "address=entity",
    ),
    (
        r"(popularity|popular|widely\s+used|most\s+traded|adoption|受欢迎|普及).*(trust|score|infer|推|implies|暗示|信賴|評分|confidence)",
        "popularity-inferred",
    ),
    (
        r"(wall\s+street|institution.*hold|ETF\s+inflow|fund\s+owns|华尔街|機構|ETF|基金).*(ownership|所有權|保有|trust|score|信賴|評分|safety)",
        "Wall Street ownership",
    ),
    (
        r"(trust|distrust|this\s+coin|the\s+issuer|issuer|symbol|name|発行者|発行体|信任|不信任).*(is|=|等于|＝|安全|secure|safe|deterministic|確定|good|bad|trustworthy|rug|scam|骗局|風險)",
        "issuer/symbol hardcode",
    ),
]


_FORBIDDEN_INFERENCE_NEGATION = re.compile(
    r"\b(?:no|not|exclud\w*|without|never|deny|denies|denied|reject\w*)\b",
    flags=re.IGNORECASE,
)


def validate_intrinsic_forbidden_inferences(profile) -> list[str]:
    """Scan dimension provenance fields for forbidden-inference patterns.

    Covers methodology, coverage and source_coordinates.  Returns a list
    of violation strings; callers must raise ValueError fail-closed when
    the list is non-empty.
    """
    violations: list[str] = []
    for dim in profile.dimensions:
        texts = [
            ("methodology", dim.provenance.methodology),
            ("coverage", dim.provenance.coverage),
            ("source_coordinates", dim.provenance.source_coordinates),
        ]
        for field_name, text in texts:
            for pattern, label in _FORBIDDEN_INFERENCE_PATTERNS:
                for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                    prefix = text[:match.start()]
                    prefix_window = prefix[-60:]
                    if not _FORBIDDEN_INFERENCE_NEGATION.search(prefix_window):
                        violations.append(
                            f"forbidden inference: {label} in dimension "
                            f"{dim.name.value} ({field_name})"
                        )
                        break
    return violations


def normalized_source_family(url: str) -> str:
    """Return a conservative HTTPS-host family or reject ambiguous authority."""
    if not isinstance(url, str) or not url:
        raise ValueError("source URL must be a non-empty string")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.hostname is None:
        raise ValueError("source URL must use HTTPS with a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source URL userinfo is forbidden")
    if "%" in parsed.netloc:
        raise ValueError("source URL encoded authority is forbidden")
    try:
        explicit_port = parsed.port
    except ValueError as exc:
        raise ValueError("source URL has an invalid port") from exc
    if explicit_port is not None:
        raise ValueError("source URL explicit ports are forbidden")
    hostname = parsed.hostname.rstrip(".").lower()
    if (
        not hostname
        or ":" in hostname
        or hostname.startswith(".")
        or ".." in hostname
        or re.fullmatch(r"[a-z0-9.-]+", hostname) is None
    ):
        raise ValueError("source URL hostname is unsupported")
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("source URL hostname is invalid") from exc


def assess_intrinsic_shadow(view: AssetIntrinsicView) -> dict:
    """Build a deterministic shadow assessment from one already-PIT-safe view."""
    if not isinstance(view, AssetIntrinsicView):
        raise ValueError("view must be AssetIntrinsicView")
    dimensions_by_name: dict[str, IntrinsicDimension] = {}
    for dimension in view.dimensions:
        name = dimension.name.value
        if name in dimensions_by_name:
            raise ValueError(f"duplicate intrinsic dimension: {name}")
        dimensions_by_name[name] = dimension

    eligible = [
        dimension
        for dimension in view.dimensions
        if dimension.status is IntrinsicFactStatus.KNOWN
        and dimension.eligible_at(view.as_of)
    ]
    families = {
        normalized_source_family(url)
        for dimension in eligible
        for url in dimension.provenance.source_urls
    }
    gate_passed = (
        len(eligible) >= REQUIRED_KNOWN_DIMENSIONS
        and len(families) >= REQUIRED_SOURCE_FAMILIES
    )
    output_dimensions: list[dict] = []
    for name in INTRINSIC_DIMENSION_NAMES:
        dimension = dimensions_by_name.get(name)
        if dimension is None:
            output_dimensions.append(_unknown_dimension(name, "fact_unavailable"))
            continue
        output_dimensions.append(_dimension_output(dimension, view.as_of, gate_passed))

    rounded_deltas = [item["signed_delta"] for item in output_dimensions]
    total_delta = round(sum(rounded_deltas), 8)
    total_delta = max(-TOTAL_DELTA_CAP, min(TOTAL_DELTA_CAP, total_delta))
    if not math.isfinite(total_delta):
        raise ValueError("shadow total must be finite")
    return {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "mode": "shadow",
        "affects_official_score": False,
        "asset_id": view.asset_id,
        "as_of": view.as_of.isoformat().replace("+00:00", "Z"),
        "total_delta": total_delta,
        "total_delta_cap": TOTAL_DELTA_CAP,
        "gate": {
            "passed": gate_passed,
            "known_count": len(eligible),
            "required_known": REQUIRED_KNOWN_DIMENSIONS,
            "source_family_count": len(families),
            "required_source_families": REQUIRED_SOURCE_FAMILIES,
            "reason_code": "eligible" if gate_passed else "insufficient_coverage",
        },
        "dimensions": output_dimensions,
    }


def _dimension_output(
    dimension: IntrinsicDimension, assessment_as_of: datetime, gate_passed: bool
) -> dict:
    if dimension.status is not IntrinsicFactStatus.KNOWN or not dimension.eligible_at(
        assessment_as_of
    ):
        return _unknown_dimension(
            dimension.name.value,
            "fact_unknown",
            coverage=dimension.provenance.coverage,
            provenance=_public_provenance(dimension),
        )
    if assessment_as_of - dimension.as_of > timedelta(days=STALE_WINDOW_DAYS):
        return _stale_dimension(
            dimension.name.value,
            coverage=dimension.provenance.coverage,
            provenance=_public_provenance(dimension),
        )
    if type(dimension.value) not in {int, float}:
        raise ValueError("known intrinsic value must be numeric")
    normalized = float(dimension.value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError("known intrinsic value must be finite and within [0, 1]")
    candidate = (normalized - 0.5) * DIMENSION_WEIGHT
    signed_delta = round(candidate, 8) if gate_passed else 0.0
    return {
        "name": dimension.name.value,
        "status": "known",
        "raw": normalized,
        "normalized": normalized,
        "weight": DIMENSION_WEIGHT,
        "signed_delta": signed_delta,
        "reason_code": "eligible" if gate_passed else "coverage_gate_not_met",
        "coverage": dimension.provenance.coverage,
        "provenance": _public_provenance(dimension),
    }


def _unknown_dimension(
    name: str,
    reason_code: str,
    *,
    coverage: str = "no PIT-visible verified fact",
    provenance: dict | None = None,
) -> dict:
    return {
        "name": name,
        "status": "unknown",
        "raw": None,
        "normalized": None,
        "weight": DIMENSION_WEIGHT,
        "signed_delta": 0.0,
        "reason_code": reason_code,
        "coverage": coverage,
        "provenance": provenance,
    }


def _stale_dimension(
    name: str,
    *,
    coverage: str,
    provenance: dict,
) -> dict:
    return {
        "name": name,
        "status": "stale",
        "raw": None,
        "normalized": None,
        "weight": DIMENSION_WEIGHT,
        "signed_delta": 0.0,
        "reason_code": "stale",
        "coverage": coverage,
        "provenance": provenance,
    }


def _public_provenance(dimension: IntrinsicDimension) -> dict:
    provenance = dimension.provenance
    return {
        "source_urls": list(provenance.source_urls),
        "source_revision": provenance.source_revision,
        "content_hash": provenance.content_hash,
        "evidence_kind": provenance.evidence_kind,
        "source_coordinates": provenance.source_coordinates,
        "as_of": dimension.as_of.isoformat().replace("+00:00", "Z"),
        "fetched_at": dimension.fetched_at.isoformat().replace("+00:00", "Z"),
    }
