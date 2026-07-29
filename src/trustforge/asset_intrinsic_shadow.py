"""Pure, explainable shadow contribution for asset-intrinsic facts.

The result is observational only.  Nothing in this module imports or mutates
the trust scorer, calibration, decision state, ranking, or market judgment.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from trustforge.asset_intrinsic import (
    INTRINSIC_DIMENSION_NAMES,
    STALE_WINDOW_DAYS,
    AssetIntrinsicView,
    IntrinsicDimension,
    IntrinsicFactStatus,
)

ASSESSMENT_SCHEMA_VERSION = "1.0.0"
INTRINSIC_SHADOW_OBSERVATION_VERSION = "1.0.0"
_FACTS_DOMAIN = b"trustforge.intrinsic.facts.v1\x00"
DIMENSION_WEIGHT = 0.032
TOTAL_DELTA_CAP = 0.08
REQUIRED_KNOWN_DIMENSIONS = 3
REQUIRED_SOURCE_FAMILIES = 2
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


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
    conflict_detected = any(
        dimension.status is IntrinsicFactStatus.CONFLICTED
        for dimension in view.dimensions
    )
    return {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "mode": "shadow",
        "affects_official_score": False,
        "asset_id": view.asset_id,
        "as_of": view.as_of.isoformat().replace("+00:00", "Z"),
        "total_delta": total_delta,
        "total_delta_cap": TOTAL_DELTA_CAP,
        "conflict_detected": conflict_detected,
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
    if dimension.status is IntrinsicFactStatus.CONFLICTED:
        return _unknown_dimension(
            dimension.name.value,
            "fact_conflicted",
            status="conflicted",
            coverage=dimension.provenance.coverage,
            provenance=_public_provenance(dimension),
        )
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
    status: str = "unknown",
    coverage: str = "no PIT-visible verified fact",
    provenance: dict | None = None,
) -> dict:
    return {
        "name": name,
        "status": status,
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


# ---------------------------------------------------------------------------
# Issue #871: provenance sanitization + shadow observation context.
#
# These helpers turn one pure ``assess_intrinsic_shadow`` result into the
# observational ``intrinsic_shadow`` payload attached to a ShadowObservation.
# Nothing here imports or mutates trust/scoring, calibration, decision state,
# direction, or market judgment.  Sensitive URL query strings and credentials
# are excluded (AC4); nonfinite/malformed inputs fail closed (AC3).
# ---------------------------------------------------------------------------


def _sanitized_url(url: str) -> str:
    """Return scheme://host/path with query, fragment, and userinfo stripped.

    Sensitive query strings (tokens, credentials) and fragments never survive
    into a shadow event.  Unparseable or scheme-less values collapse to an
    empty string so the caller can drop them entirely (fail closed).
    """
    if not isinstance(url, str) or not url:
        return ""
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if not scheme or not hostname:
        return ""
    netloc = hostname.rstrip(".").lower()
    try:
        rebuilt = urlunsplit((scheme, netloc, parsed.path, "", ""))
    except ValueError:
        return ""
    return rebuilt


def _sanitized_query(query: str) -> str:
    """Return a domain-tagged SHA-256 digest of a query string.

    The plaintext query is never carried into provenance; only its stable
    identifier is retained so observations remain idempotent and comparable.
    """
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    return "sha256:" + hashlib.sha256(
        b"trustforge.intrinsic.query.v1\x00" + query.encode("utf-8")
    ).hexdigest()


def _finite_trust(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def intrinsic_facts_hash(dimensions: list[dict]) -> str:
    """Return the producer-canonical digest for sanitized dimension facts."""
    facts_material = []
    for dimension in dimensions:
        provenance = dimension.get("provenance")
        facts_material.append(
            {
                "name": dimension.get("name"),
                "status": dimension.get("status"),
                "raw": dimension.get("raw"),
                "content_hash": (
                    provenance.get("content_hash")
                    if isinstance(provenance, dict)
                    else None
                ),
            }
        )
    return "sha256:" + hashlib.sha256(
        _FACTS_DOMAIN + json.dumps(facts_material, sort_keys=True).encode("utf-8")
    ).hexdigest()


def validate_intrinsic_shadow_observation(payload: dict) -> dict:
    """Validate and reconstruct every producer-derived intrinsic field.

    Persisted shadow evidence is untrusted at promotion time.  This validator
    mirrors :func:`build_intrinsic_shadow_observation` and rejects values that
    cannot be reconstructed from the canonical dimensions.
    """
    expected_keys = {
        "schema_version",
        "assessment_schema_version",
        "mode",
        "affects_official_score",
        "asset_id",
        "as_of",
        "baseline_trust",
        "candidate_trust",
        "trust_delta",
        "total_delta",
        "total_delta_cap",
        "facts_hash",
        "query_hash",
        "gate",
        "dimensions",
    }
    if set(payload) != expected_keys:
        raise ValueError("intrinsic shadow observation fields are malformed")
    if (
        payload["schema_version"] != INTRINSIC_SHADOW_OBSERVATION_VERSION
        or payload["assessment_schema_version"] != ASSESSMENT_SCHEMA_VERSION
        or payload["mode"] != "shadow"
        or payload["affects_official_score"] is not False
        or not isinstance(payload["asset_id"], str)
        or not payload["asset_id"]
        or not isinstance(payload["as_of"], str)
        or not payload["as_of"]
        or not isinstance(payload["query_hash"], str)
        or _DIGEST_RE.fullmatch(payload["query_hash"]) is None
    ):
        raise ValueError("intrinsic shadow observation contract is malformed")

    baseline = _finite_trust(payload["baseline_trust"], "baseline_trust")
    candidate = _finite_trust(payload["candidate_trust"], "candidate_trust")
    trust_delta = _finite_trust(payload["trust_delta"], "trust_delta")
    if trust_delta != round(candidate - baseline, 8):
        raise ValueError("intrinsic trust_delta conflicts with candidate-baseline")

    gate = payload["gate"]
    gate_keys = {
        "passed",
        "known_count",
        "required_known",
        "source_family_count",
        "required_source_families",
        "reason_code",
    }
    if (
        not isinstance(gate, dict)
        or set(gate) != gate_keys
        or not isinstance(gate["passed"], bool)
        or isinstance(gate["known_count"], bool)
        or not isinstance(gate["known_count"], int)
        or isinstance(gate["source_family_count"], bool)
        or not isinstance(gate["source_family_count"], int)
        or gate["required_known"] != REQUIRED_KNOWN_DIMENSIONS
        or gate["required_source_families"] != REQUIRED_SOURCE_FAMILIES
    ):
        raise ValueError("intrinsic coverage gate is malformed")

    dimensions = payload["dimensions"]
    if not isinstance(dimensions, list) or len(dimensions) != len(
        INTRINSIC_DIMENSION_NAMES
    ):
        raise ValueError("intrinsic dimensions are malformed")
    names: set[str] = set()
    known_count = 0
    families: set[str] = set()
    dimension_keys = {
        "name",
        "status",
        "raw",
        "normalized",
        "weight",
        "signed_delta",
        "reason_code",
        "coverage",
        "provenance",
    }
    provenance_keys = {
        "source_urls",
        "source_revision",
        "content_hash",
        "evidence_kind",
        "source_coordinates",
        "as_of",
        "fetched_at",
    }
    for dimension in dimensions:
        if not isinstance(dimension, dict) or set(dimension) != dimension_keys:
            raise ValueError("intrinsic dimension fields are malformed")
        name = dimension["name"]
        status = dimension["status"]
        if (
            name not in INTRINSIC_DIMENSION_NAMES
            or name in names
            or status not in {"known", "unknown", "stale", "conflicted"}
            or isinstance(dimension["weight"], bool)
            or dimension["weight"] != DIMENSION_WEIGHT
            or isinstance(dimension["signed_delta"], bool)
            or not isinstance(dimension["signed_delta"], (int, float))
            or not math.isfinite(float(dimension["signed_delta"]))
            or not isinstance(dimension["coverage"], str)
        ):
            raise ValueError("intrinsic dimension contract is malformed")
        names.add(name)
        provenance = dimension["provenance"]
        if provenance is not None and not isinstance(provenance, dict):
            raise ValueError("intrinsic dimension provenance is malformed")
        if provenance is not None:
            urls = provenance.get("source_urls")
            if (
                set(provenance) != provenance_keys
                or not isinstance(urls, list)
                or any(
                    not isinstance(url, str)
                    or not url
                    or _sanitized_url(url) != url
                    for url in urls
                )
                or not isinstance(provenance["source_revision"], str)
                or not provenance["source_revision"]
                or not isinstance(provenance["content_hash"], str)
                or re.fullmatch(r"[0-9a-f]{64}", provenance["content_hash"]) is None
                or provenance["evidence_kind"]
                not in {"upstream_excerpt", "decision_record"}
                or not isinstance(provenance["source_coordinates"], str)
                or not provenance["source_coordinates"]
                or not isinstance(provenance["as_of"], str)
                or not provenance["as_of"]
                or not isinstance(provenance["fetched_at"], str)
                or not provenance["fetched_at"]
            ):
                raise ValueError("intrinsic dimension provenance is malformed")
        if status == "known":
            raw, normalized = dimension["raw"], dimension["normalized"]
            if (
                isinstance(raw, bool)
                or not isinstance(raw, (int, float))
                or isinstance(normalized, bool)
                or not isinstance(normalized, (int, float))
                or not math.isfinite(float(raw))
                or not math.isfinite(float(normalized))
                or not 0.0 <= float(raw) <= 1.0
                or float(raw) != float(normalized)
                or provenance is None
                or provenance["evidence_kind"] != "upstream_excerpt"
            ):
                raise ValueError("known intrinsic dimension value is malformed")
            urls = provenance.get("source_urls")
            if not isinstance(urls, list) or not urls:
                raise ValueError("known intrinsic provenance is malformed")
            families.update(normalized_source_family(url) for url in urls)
            known_count += 1
            expected_reason = "eligible" if gate["passed"] else "coverage_gate_not_met"
            expected_delta = (
                round((float(normalized) - 0.5) * DIMENSION_WEIGHT, 8)
                if gate["passed"]
                else 0.0
            )
            if (
                dimension["reason_code"] != expected_reason
                or float(dimension["signed_delta"]) != expected_delta
            ):
                raise ValueError("known intrinsic dimension delta is malformed")
        else:
            expected_reason = {
                "unknown": {"fact_unknown", "fact_unavailable"},
                "stale": {"stale"},
                "conflicted": {"fact_conflicted"},
            }[status]
            if (
                dimension["raw"] is not None
                or dimension["normalized"] is not None
                or float(dimension["signed_delta"]) != 0.0
                or dimension["reason_code"] not in expected_reason
                or (status in {"stale", "conflicted"} and provenance is None)
                or (
                    status == "unknown"
                    and (
                        (provenance is None)
                        != (dimension["reason_code"] == "fact_unavailable")
                    )
                )
                or (
                    status == "stale"
                    and provenance is not None
                    and provenance["evidence_kind"] != "upstream_excerpt"
                )
            ):
                raise ValueError("non-known intrinsic dimension is malformed")
    if names != set(INTRINSIC_DIMENSION_NAMES) or [
        item["name"] for item in dimensions
    ] != list(INTRINSIC_DIMENSION_NAMES):
        raise ValueError("intrinsic dimension set is malformed")

    expected_passed = (
        known_count >= REQUIRED_KNOWN_DIMENSIONS
        and len(families) >= REQUIRED_SOURCE_FAMILIES
    )
    if (
        gate["known_count"] != known_count
        or gate["source_family_count"] != len(families)
        or gate["passed"] is not expected_passed
        or gate["reason_code"]
        != ("eligible" if expected_passed else "insufficient_coverage")
    ):
        raise ValueError("intrinsic coverage gate conflicts with dimensions")

    total_delta = _finite_trust(payload["total_delta"], "total_delta")
    if payload["total_delta_cap"] != TOTAL_DELTA_CAP:
        raise ValueError("intrinsic total_delta_cap is malformed")
    expected_total = round(sum(float(item["signed_delta"]) for item in dimensions), 8)
    expected_total = max(-TOTAL_DELTA_CAP, min(TOTAL_DELTA_CAP, expected_total))
    if total_delta != expected_total:
        raise ValueError("intrinsic total_delta conflicts with dimension deltas")
    if payload["facts_hash"] != intrinsic_facts_hash(dimensions):
        raise ValueError("intrinsic facts_hash conflicts with canonical facts")
    return payload


def build_intrinsic_shadow_observation(
    view: AssetIntrinsicView,
    *,
    baseline_trust: float,
    candidate_trust: float,
    query: str,
) -> dict:
    """Build a sanitized, deterministic intrinsic shadow observation payload.

    Captures baseline/candidate trust, the trust delta, the intrinsic total
    delta, a facts hash, the as-of schema, the coverage gate (with known and
    source-family counts), and sanitized per-dimension provenance.  Malformed
    or nonfinite inputs raise ``ValueError`` (fail closed); the shadow runtime
    degrades such failures to ``intrinsic_shadow=None`` so the official report
    is never affected (AC2, AC3).
    """
    baseline = _finite_trust(baseline_trust, "baseline_trust")
    candidate = _finite_trust(candidate_trust, "candidate_trust")
    assessment = assess_intrinsic_shadow(view)

    sanitized_dimensions: list[dict] = []
    for dimension in assessment["dimensions"]:
        dimension_copy = dict(dimension)
        provenance = dimension_copy.get("provenance")
        sanitized_provenance: dict | None
        if isinstance(provenance, dict):
            sanitized_provenance = dict(provenance)
            raw_urls = sanitized_provenance.get("source_urls")
            if isinstance(raw_urls, list):
                sanitized_provenance["source_urls"] = [
                    cleaned
                    for cleaned in (_sanitized_url(str(url)) for url in raw_urls)
                    if cleaned
                ]
            dimension_copy["provenance"] = sanitized_provenance
        else:
            sanitized_provenance = None
        sanitized_dimensions.append(dimension_copy)
    facts_hash = intrinsic_facts_hash(sanitized_dimensions)

    result = {
        "schema_version": INTRINSIC_SHADOW_OBSERVATION_VERSION,
        "assessment_schema_version": assessment["schema_version"],
        "mode": "shadow",
        "affects_official_score": False,
        "asset_id": assessment["asset_id"],
        "as_of": assessment["as_of"],
        "baseline_trust": round(baseline, 8),
        "candidate_trust": round(candidate, 8),
        "trust_delta": round(candidate - baseline, 8),
        "total_delta": assessment["total_delta"],
        "total_delta_cap": assessment["total_delta_cap"],
        "facts_hash": facts_hash,
        "query_hash": _sanitized_query(query),
        "gate": assessment["gate"],
        "dimensions": sanitized_dimensions,
    }
    validate_intrinsic_shadow_observation(result)
    return result
