from __future__ import annotations

from trustforge.data_quality import validate_documents
from trustforge.ingestion.base import Document


def _doc(identifier: str, text: str = "valid", **kwargs) -> Document:
    return Document(id=identifier, kind="news", source="unit", text=text, ts=100.0, **kwargs)


def test_quality_gate_accepts_valid_and_quarantines_schema_null_time_and_duplicates() -> None:
    valid = _doc("a")
    invalid = Document(id="", kind="", source="", text="", ts=float("nan"), schema_version="9.0.0")
    duplicate = _doc("a")
    future = Document(id="future", kind="news", source="unit", text="future payload", ts=1000.0)
    accepted, quarantined = validate_documents([valid, invalid, duplicate, future], now=200.0)
    assert accepted == [valid]
    assert set(quarantined[0].reason_codes) == {
        "schema_version_mismatch", "missing_id", "missing_source", "missing_kind",
        "missing_text", "invalid_timestamp",
    }
    assert set(quarantined[1].reason_codes) == {"duplicate_id_in_batch", "duplicate_content_in_batch"}
    assert quarantined[2].reason_codes == ("future_timestamp",)
