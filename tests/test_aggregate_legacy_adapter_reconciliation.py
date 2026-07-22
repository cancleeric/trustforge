"""Test-only legacy adapter oracle for issue #452 reconciliation."""

from __future__ import annotations

from trustforge.ingestion.base import Document
from trustforge.trust import scoring


def _legacy_item(claim_id: str, trust: float, source: str) -> scoring.ScoredClaim:
    document = Document(
        id=f"doc-{claim_id}",
        kind="news",
        source=source,
        text="generic market update",
        ts=1.0,
        meta={},
    )
    claim = scoring.Claim(claim_id, document.text, document)
    return scoring.ScoredClaim(claim, trust)


def test_legacy_adapter_loads_parsed_model_once_and_model_changes_output(
    monkeypatch,
) -> None:
    calls = 0
    parsed = [
        {"confidence": 0.0, "calibrated": 0.1},
        {"confidence": 1.0, "calibrated": 0.9},
    ]

    def load_once():
        nonlocal calls
        calls += 1
        return parsed

    monkeypatch.setattr(scoring, "_load_cached_calibration_model", load_once)
    output = scoring.aggregate(
        [_legacy_item("a", 0.8, "one"), _legacy_item("b", 0.8, "two")],
        query="",
    )
    assert calls == 1
    assert output.confidence == 0.8
    assert output.calibrated_confidence == 0.564


def test_legacy_adapter_preserves_original_supporting_and_contrarian_identity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(scoring, "_load_cached_calibration_model", lambda: None)
    supporting = _legacy_item("support", 0.8, "one")
    contrarian = _legacy_item("contra", 0.4, "two")
    output = scoring.aggregate([supporting, contrarian], query="")
    assert output.supporting == [supporting]
    assert output.contrarian == [contrarian]
    assert output.supporting[0] is supporting
    assert output.contrarian[0] is contrarian
