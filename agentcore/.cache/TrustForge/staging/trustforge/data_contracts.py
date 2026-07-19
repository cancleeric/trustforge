"""Versioned machine-readable contracts for TrustForge core payloads."""
from __future__ import annotations

from typing import Any

DOCUMENT_SCHEMA_VERSION = "1.0.0"
EVIDENCE_SCHEMA_VERSION = "1.0.0"
REPORT_SCHEMA_VERSION = "1.0.0"


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
            },
            "additionalProperties": False,
        },
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
