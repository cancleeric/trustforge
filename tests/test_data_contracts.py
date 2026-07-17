from __future__ import annotations

from dataclasses import asdict

from trustforge.data_contracts import (
    DOCUMENT_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    compatibility_violations,
    contract_schemas,
)
from trustforge.ingestion.base import Document
from trustforge.ingestion.cache import doc_from_dict, doc_to_dict
from trustforge.schema import Evidence


def test_versioned_payloads_round_trip_and_legacy_default() -> None:
    document = Document(id="d1", kind="news", source="unit", text="text")
    assert doc_to_dict(document)["schema_version"] == DOCUMENT_SCHEMA_VERSION
    assert doc_from_dict(doc_to_dict(document)).schema_version == DOCUMENT_SCHEMA_VERSION
    assert doc_from_dict({"id": "legacy"}).schema_version == DOCUMENT_SCHEMA_VERSION

    evidence = Evidence(source="unit", fetched_at="2026-01-01T00:00:00Z", content_reference="x", related_claim="c")
    assert asdict(evidence)["schema_version"] == EVIDENCE_SCHEMA_VERSION


def test_contracts_have_versions_and_report_contract() -> None:
    schemas = contract_schemas()
    assert schemas["Document"]["properties"]["schema_version"]["const"] == DOCUMENT_SCHEMA_VERSION
    assert schemas["Evidence"]["properties"]["schema_version"]["const"] == EVIDENCE_SCHEMA_VERSION
    assert schemas["Report"]["properties"]["schema_version"]["const"] == REPORT_SCHEMA_VERSION
    assert "schema_version" in schemas["Report"]["required"]


def test_compatibility_gate_detects_breaking_changes() -> None:
    previous = {"required": ["a"], "properties": {"a": {"type": "string"}, "b": {"type": "number"}}}
    current = {"required": [], "properties": {"a": {"type": "number"}}}
    assert compatibility_violations(previous, current) == [
        "changed type: a", "removed property: b", "removed required field: a"
    ]
