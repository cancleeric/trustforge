"""Golden and boundary tests for versioned kernel result contracts (#450)."""

from __future__ import annotations

from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim, ScoredClaim, TrustedBrief


def test_legacy_dto_attribute_shape_golden():
    """Freeze legacy DTO attributes before adding v2 result contracts."""
    document = Document(
        "doc-1",
        "regulatory",
        "sec",
        "ETF inflows increased",
        ts=1_700_000_000.0,
        url="https://example.test/doc-1",
        meta={},
    )
    claim = Claim("claim-1", document.text, document, "fact", "bullish")
    scored = ScoredClaim(
        claim,
        0.82,
        {"source": 0.9, "recency": 0.8},
        {
            "source": "sec",
            "prior": 0.75,
            "final": 0.9,
            "agree_n": 2,
            "contradict_n": 1,
            "iterations_run": 3,
        },
        ["pump"],
        ["similar_text"],
    )
    contrary_document = Document(
        "doc-2",
        "news",
        "wire",
        "Outflows may increase",
        ts=1_700_000_010.0,
        url="https://example.test/doc-2",
        meta={},
    )
    contrary_claim = Claim(
        "claim-2", contrary_document.text, contrary_document, "inference", "bearish"
    )
    contrary = ScoredClaim(
        contrary_claim,
        0.31,
        {"source": 0.4, "recency": 0.7},
    )
    brief = TrustedBrief(
        "BTC outlook",
        [scored],
        [contrary],
        0.82,
        calibrated_confidence=0.71,
    )

    all_scored = (*brief.supporting, *brief.contrarian)

    def report_value(item: ScoredClaim) -> dict:
        return {
            "claim_id": item.claim.id,
            "trust": item.trust,
            "components": item.components,
            "reputation_trace": item.reputation_trace,
            "manip_flags": item.manip_flags,
            "info_flags": item.info_flags,
        }

    actual = {
        "query": brief.query,
        "scored_claims": [report_value(item) for item in all_scored],
        "supporting": [item.claim.id for item in brief.supporting],
        "contrarian": [item.claim.id for item in brief.contrarian],
        "confidence": brief.confidence,
        "calibrated_confidence": brief.calibrated_confidence,
    }

    assert actual == {
        "query": "BTC outlook",
        "scored_claims": [
            {
                "claim_id": "claim-1",
                "trust": 0.82,
                "components": {"source": 0.9, "recency": 0.8},
                "reputation_trace": {
                    "source": "sec",
                    "prior": 0.75,
                    "final": 0.9,
                    "agree_n": 2,
                    "contradict_n": 1,
                    "iterations_run": 3,
                },
                "manip_flags": ["pump"],
                "info_flags": ["similar_text"],
            },
            {
                "claim_id": "claim-2",
                "trust": 0.31,
                "components": {"source": 0.4, "recency": 0.7},
                "reputation_trace": None,
                "manip_flags": [],
                "info_flags": [],
            },
        ],
        "supporting": ["claim-1"],
        "contrarian": ["claim-2"],
        "confidence": 0.82,
        "calibrated_confidence": 0.71,
    }
