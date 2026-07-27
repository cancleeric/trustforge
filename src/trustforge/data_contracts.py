"""Versioned machine-readable contracts for TrustForge core payloads."""
from __future__ import annotations

from typing import Any

from trustforge.asset_context import (
    ASSET_CONTEXT_SCHEMA_VERSION,
    ASSET_LAYERS,
    ASSET_SECTORS,
    MARKET_CAP_TIERS,
    TOKEN_ROLES,
)
from trustforge.asset_intrinsic import (
    ASSET_INTRINSIC_SCHEMA_VERSION,
    INTRINSIC_DIMENSION_NAMES,
    INTRINSIC_FACT_STATUSES,
    MAX_PATH_LENGTH,
    MAX_REVISION_LENGTH,
    MAX_TEXT_LENGTH,
    MAX_URL_COUNT,
    MAX_URL_LENGTH,
)
from trustforge.ecolink import ECOLINK_SCHEMA_VERSION, OFFICIAL_ECOLINK_HOSTS
from trustforge.peer_metrics import PEER_METRICS_SCHEMA_VERSION

DOCUMENT_SCHEMA_VERSION = "1.0.0"
EVIDENCE_SCHEMA_VERSION = "1.0.0"
REPORT_SCHEMA_VERSION = "1.0.0"
KERNEL_SCHEMA_VERSION = "1.0.0"


def _asset_context_schema_properties() -> dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "schema_version",
            "asset_id",
            "symbol",
            "name",
            "sector",
            "layer",
            "token_role",
            "market_cap_tier",
            "ecosystem",
            "parent_asset_id",
            "tags",
        ],
        "properties": {
            "schema_version": {"const": ASSET_CONTEXT_SCHEMA_VERSION},
            "asset_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
            "symbol": {"type": "string", "minLength": 1, "pattern": r"\S"},
            "name": {"type": "string", "minLength": 1, "pattern": r"\S"},
            "sector": {"enum": list(ASSET_SECTORS)},
            "layer": {"enum": list(ASSET_LAYERS)},
            "token_role": {"enum": list(TOKEN_ROLES)},
            "market_cap_tier": {"enum": list(MARKET_CAP_TIERS)},
            "ecosystem": {"type": ["string", "null"]},
            "parent_asset_id": {"type": ["string", "null"]},
            "tags": {"type": "array", "items": {"type": "string"}},
            "settlement_chain": {"type": "string", "minLength": 1},
            "gas_token": {"type": "string", "minLength": 1},
            "dependencies": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }


def _asset_intrinsic_schema_properties() -> dict[str, Any]:
    timestamp = {
        "type": "string",
        "format": "date-time",
        "pattern": r"(?:Z|[+-]\d{2}:\d{2})$",
    }
    provenance = {
        "type": "object",
        "required": [
            "source_urls", "methodology", "content_hash", "coverage",
            "evidence_path", "source_revision",
        ],
        "properties": {
            "source_urls": {
                "type": "array",
                "maxItems": MAX_URL_COUNT,
                "items": {
                    "type": "string", "maxLength": MAX_URL_LENGTH,
                    "pattern": r"^https://",
                },
            },
            "methodology": {
                "type": "string", "minLength": 1, "maxLength": MAX_TEXT_LENGTH,
                "pattern": r"\S",
            },
            "content_hash": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
            "coverage": {
                "type": "string", "minLength": 1, "maxLength": MAX_TEXT_LENGTH,
                "pattern": r"\S",
            },
            "evidence_path": {
                "type": "string",
                "maxLength": MAX_PATH_LENGTH,
                "pattern": r"^data/asset_intrinsic_evidence/[^/]+\.txt$",
            },
            "source_revision": {
                "type": "string", "minLength": 1, "maxLength": MAX_REVISION_LENGTH,
                "pattern": r"\S",
            },
            "evidence_kind": {"enum": ["upstream_excerpt", "decision_record"]},
            "source_coordinates": {
                "type": "string", "minLength": 1, "maxLength": MAX_TEXT_LENGTH,
                "pattern": r"\S",
            },
        },
        "additionalProperties": False,
    }
    dimension = {
        "type": "object",
        "required": [
            "name", "status", "value", "as_of", "valid_from", "valid_until",
            "fetched_at", "provenance",
        ],
        "properties": {
            "name": {"enum": list(INTRINSIC_DIMENSION_NAMES)},
            "status": {"enum": list(INTRINSIC_FACT_STATUSES)},
            "value": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "as_of": timestamp,
            "valid_from": timestamp,
            "valid_until": {
                "type": ["string", "null"],
                "format": "date-time",
                "pattern": r"(?:Z|[+-]\d{2}:\d{2})$",
            },
            "fetched_at": timestamp,
            "provenance": provenance,
        },
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "known"}}},
                "then": {
                    "properties": {
                        "value": {"type": "number", "minimum": 0, "maximum": 1},
                        "provenance": {
                            **provenance,
                            "properties": {
                                **provenance["properties"],
                                "source_urls": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": MAX_URL_COUNT,
                                    "items": {
                                        "type": "string",
                                        "maxLength": MAX_URL_LENGTH,
                                        "pattern": r"^https://",
                                    },
                                },
                                "evidence_kind": {"const": "upstream_excerpt"},
                            },
                        },
                    },
                },
                "else": {"properties": {"value": {"type": "null"}}},
            }
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "required": ["schema_version", "asset_id", "dimensions"],
        "properties": {
            "schema_version": {"const": ASSET_INTRINSIC_SCHEMA_VERSION},
            "asset_id": {
                "type": "string", "minLength": 1, "maxLength": MAX_REVISION_LENGTH,
                "pattern": r"\S",
            },
            "dimensions": {
                "type": "array",
                "minItems": len(INTRINSIC_DIMENSION_NAMES),
                "maxItems": len(INTRINSIC_DIMENSION_NAMES),
                "items": dimension,
            },
        },
        "allOf": [
            {
                "properties": {
                    "dimensions": {
                        "contains": {
                            "type": "object",
                            "properties": {"name": {"const": dimension_name}},
                            "required": ["name"],
                        },
                        "minContains": 1,
                        "maxContains": 1,
                    }
                }
            }
            for dimension_name in INTRINSIC_DIMENSION_NAMES
        ],
        "additionalProperties": False,
    }


def contract_schemas() -> dict[str, dict[str, Any]]:
    """Return deterministic JSON Schema documents used by CI and consumers."""
    return {
        "Document": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://trustforge.local/contracts/document/1.0.0",
            "title": "TrustForge Document", "type": "object",
            "required": ["schema_version", "id", "kind", "source", "text", "url", "ts", "meta"],
            "properties": {
                "schema_version": {"const": DOCUMENT_SCHEMA_VERSION},
                "id": {"type": "string"}, "kind": {"type": "string"},
                "source": {"type": "string"}, "text": {"type": "string"},
                "url": {"type": "string"}, "ts": {"type": "number"},
                "meta": {"type": "object"},
            },
            "additionalProperties": False,
        },
        "Evidence": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://trustforge.local/contracts/evidence/1.0.0",
            "title": "TrustForge Evidence", "type": "object",
            "required": [
                "schema_version", "source", "fetched_at", "content_reference",
                "related_claim", "source_url", "kind", "trust", "trust_components",
            ],
            "properties": {
                "schema_version": {"const": EVIDENCE_SCHEMA_VERSION},
                "source": {"type": "string"}, "fetched_at": {"type": "string"},
                "content_reference": {"type": "string"}, "related_claim": {"type": "string"},
                "source_url": {"type": "string"}, "kind": {"type": "string"},
                "trust": {"type": "number"}, "trust_components": {"type": "object"},
                "flags": {"type": "array", "items": {"type": "string"}},
                "info_flags": {"type": "array", "items": {"type": "string"}},
                "author": {"type": ["string", "null"]},
                "reputation_mode": {"type": ["string", "null"]},
                "data_lineage": {"type": ["object", "null"]},
            },
            "additionalProperties": False,
        },
        "Report": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://trustforge.local/contracts/report/1.0.0",
            "title": "TrustForge Report", "type": "object",
            "required": [
                "schema_version", "coin", "question_type", "question", "market_judgment",
                "facts", "inferences", "key_basis", "confidence", "limits", "could_flip",
                "contrarian", "generated_at", "calibrated_confidence", "decision_state",
            ],
            "properties": {
                "schema_version": {"const": REPORT_SCHEMA_VERSION},
                "coin": {"type": "string"}, "question_type": {"type": "string"},
                "question": {"type": "string"}, "market_judgment": {"type": "string"},
                "facts": {"type": "array", "items": {"type": "string"}},
                "inferences": {"type": "array", "items": {"type": "string"}},
                "key_basis": {"type": "array", "items": {"type": "object"}},
                "confidence": {"type": "number"}, "limits": {"type": "array"},
                "could_flip": {"type": "array"}, "contrarian": {"type": "array"},
                "generated_at": {"type": "string"}, "direction": {"type": "string"},
                "cross_source_signal": {"type": ["object", "null"]},
                "insights": {"type": ["array", "null"]},
                "hypothesis_ledger": {"type": ["object", "null"]},
                "calibrated_confidence": {"type": "number"},
                "decision_state": {"enum": ["abstain", "low_confidence", "normal"]},
                "asset_context": {
                    "anyOf": [
                        {"$ref": "#/$defs/AssetContext"},
                        {"type": "null"},
                    ],
                },
                "risk_notices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["code", "severity", "message"],
                        "properties": {
                            "code": {"type": "string", "minLength": 1},
                            "severity": {"enum": ["info", "warning"]},
                            "message": {"type": "string", "minLength": 1},
                        },
                        "additionalProperties": False,
                    },
                },
                "term_annotations": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/TermAnnotation"},
                },
            },
            "$defs": {
                "AssetContext": _asset_context_schema_properties(),
                "TermAnnotation": {
                    "type": "object",
                    "required": ["term_id", "term_name", "matched_text", "start", "end", "glossary_link"],
                    "properties": {
                        "term_id": {"type": "string", "minLength": 1},
                        "term_name": {"type": "string", "minLength": 1},
                        "matched_text": {"type": "string", "minLength": 1},
                        "start": {"type": "integer", "minimum": 0},
                        "end": {"type": "integer", "minimum": 0},
                        "glossary_link": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        "AssetContext": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://trustforge.local/contracts/asset-context/1.0.0",
            "title": "TrustForge AssetContext",
            **_asset_context_schema_properties(),
        },
        "AssetIntrinsicProfile": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://trustforge.local/contracts/asset-intrinsic-profile/1.0.0",
            "title": "TrustForge AssetIntrinsicProfile",
            **_asset_intrinsic_schema_properties(),
        },
        "PeerMetricsSnapshot": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://trustforge.local/contracts/peer-metrics-snapshot/1.0.0",
            "title": "TrustForge PeerMetricsSnapshot",
            "type": "object",
            "required": [
                "schema_version",
                "asset_id",
                "observed_tps",
                "tvl",
                "gas_fee",
                "activity_breakdown",
                "window_start",
                "window_end",
                "observed_at",
            ],
            "properties": {
                "schema_version": {"const": PEER_METRICS_SCHEMA_VERSION},
                "asset_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
                "observed_tps": _metric_value_schema(),
                "tvl": _metric_value_schema(),
                "gas_fee": _metric_value_schema(),
                "activity_breakdown": {
                    "type": "object",
                    "minProperties": 1,
                    "additionalProperties": _metric_value_schema(),
                },
                "window_start": {"type": "string", "format": "date-time"},
                "window_end": {"type": "string", "format": "date-time"},
                "observed_at": {"type": "string", "format": "date-time"},
            },
            "additionalProperties": False,
        },
        "DependencyEdge": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://trustforge.local/contracts/dependency-edge/1.0.0",
            "title": "TrustForge DependencyEdge",
            "type": "object",
            "required": [
                "schema_version",
                "source_asset_id",
                "target_asset_id",
                "kind",
                "valid_from",
                "valid_until",
                "confidence",
                "official_source_url",
                "observed_at",
            ],
            "properties": {
                "schema_version": {"const": ECOLINK_SCHEMA_VERSION},
                "source_asset_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
                "target_asset_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
                "kind": {
                    "enum": [
                        "bridge",
                        "oracle",
                        "liquidity",
                        "settlement",
                        "governance",
                        "infrastructure",
                        "unknown",
                    ]
                },
                "valid_from": {"type": "string", "format": "date-time"},
                "valid_until": {"type": ["string", "null"], "format": "date-time"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "official_source_url": _official_source_schema(),
                "observed_at": {"type": "string", "format": "date-time"},
            },
            "additionalProperties": False,
        },
        "UpgradeEvent": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://trustforge.local/contracts/upgrade-event/1.0.0",
            "title": "TrustForge UpgradeEvent",
            "type": "object",
            "required": [
                "schema_version",
                "event_id",
                "asset_id",
                "title",
                "scheduled_at",
                "actual_at",
                "status",
                "impact_direction",
                "impacted_asset_ids",
                "official_source_url",
                "observed_at",
            ],
            "properties": {
                "schema_version": {"const": ECOLINK_SCHEMA_VERSION},
                "event_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
                "asset_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
                "title": {"type": "string", "minLength": 1, "pattern": r"\S"},
                "scheduled_at": {"type": ["string", "null"], "format": "date-time"},
                "actual_at": {"type": ["string", "null"], "format": "date-time"},
                "status": {"enum": ["announced", "scheduled", "activated", "cancelled"]},
                "impact_direction": {"enum": ["positive", "negative", "mixed", "unknown"]},
                "impacted_asset_ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "pattern": r"\S"},
                },
                "official_source_url": _official_source_schema(),
                "observed_at": {"type": "string", "format": "date-time"},
            },
            "additionalProperties": False,
        },
    }


def _metric_value_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["value", "unit", "method", "source"],
        "properties": {
            "value": {"type": ["number", "null"], "minimum": 0},
            "unit": {"type": "string", "minLength": 1, "pattern": r"\S"},
            "method": {"enum": ["observed", "estimated", "reported", "unknown"]},
            "source": {"type": "string", "minLength": 1, "pattern": r"\S"},
        },
        "additionalProperties": False,
    }


def _official_source_schema() -> dict[str, Any]:
    hosts = "|".join(sorted(host.replace(".", r"\.") for host in OFFICIAL_ECOLINK_HOSTS))
    return {
        "type": "string",
        "minLength": 1,
        "pattern": rf"^https://({hosts})(/|$)",
    }


def compatibility_violations(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Detect undeclared breaking removals/type changes within one major version."""
    violations: list[str] = []
    old_props, new_props = previous.get("properties", {}), current.get("properties", {})
    for name, old in old_props.items():
        if name not in new_props:
            violations.append(f"removed property: {name}")
        elif old.get("type") != new_props[name].get("type"):
            violations.append(f"changed type: {name}")
    for name in previous.get("required", []):
        if name not in current.get("required", []):
            violations.append(f"removed required field: {name}")
    return violations
