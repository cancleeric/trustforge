"""Characterize the app-to-kernel adapter before production routing (#420, #727)."""

from __future__ import annotations

import pytest

from trustforge.agent.kernel_mapper import to_kernel_input, to_legacy_scoring
from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim, ScoredClaim, aggregate, score
from trustforge_core import run_kernel


def _claims() -> list[Claim]:
    return [
        Claim(
            "c1",
            "BTC ETF inflows expanded after demand improved",
            Document("d1", "news", "Reuters", "BTC ETF inflows expanded", 100.0),
            "fact",
            "bullish",
        ),
        Claim(
            "c2",
            "BTC exchange reserves fell as demand improved",
            Document("d2", "onchain", "Glassnode", "BTC reserves fell", 101.0),
            "fact",
            "bullish",
        ),
        Claim(
            "c3",
            "BTC social posts promise guaranteed profit",
            Document("d3", "social", "anonymous", "guaranteed profit", 102.0),
            "opinion",
            "bearish",
        ),
    ]


def test_adapter_matches_legacy_score_and_aggregate() -> None:
    """Core-to-legacy roundtrip preserves trust values and aggregate structure."""
    claims = _claims()
    kernel_input = to_kernel_input(claims, pit_epoch=110.0, coin="BTC", query="BTC outlook")
    output = run_kernel(kernel_input)
    adapted_scored, adapted_brief = to_legacy_scoring(output, claims)

    assert [item.claim.id for item in adapted_scored] == [c.id for c in claims]
    assert len(adapted_scored) == len(output.scored_claims)
    assert [item.claim.id for item in adapted_brief.supporting] == [
        item.claim.id for item in output.supporting
    ]
    assert [item.claim.id for item in adapted_brief.contrarian] == [
        item.claim.id for item in output.contrarian
    ]
    assert adapted_brief.confidence == output.trust_score
    assert adapted_brief.calibrated_confidence == output.confidence


def test_output_adapter_revalidates_tampered_topology_without_hooks() -> None:
    claims = _claims()
    output = run_kernel(to_kernel_input(claims, pit_epoch=110.0, coin="BTC", query="BTC outlook"))
    object.__setattr__(output, "supporting_count", output.supporting_count + 1)
    with pytest.raises(ValueError, match="supporting_count must match"):
        to_legacy_scoring(output, claims)

    hooks = 0

    class Hostile:
        def __eq__(self, _other: object) -> bool:
            nonlocal hooks
            hooks += 1
            raise AssertionError("hook executed")

        def __hash__(self) -> int:
            nonlocal hooks
            hooks += 1
            raise AssertionError("hook executed")

    clean = run_kernel(to_kernel_input(claims, pit_epoch=110.0, coin="BTC", query="BTC outlook"))
    object.__setattr__(clean.scored_claims[0], "components", (Hostile(),))
    with pytest.raises(ValueError, match="components must contain"):
        to_legacy_scoring(clean, claims)
    assert hooks == 0


def test_output_adapter_rejects_invalid_values_and_summary_graph() -> None:
    claims = _claims()
    output = run_kernel(to_kernel_input(claims, pit_epoch=110.0, coin="BTC", query="BTC"))
    for field, value, match in (
        ("trust_score", 999.0, "trust_score must be in"),
        ("confidence", -5.0, "confidence must be in"),
        ("abstain", False, "abstain must match"),
    ):
        object.__setattr__(output, field, value)
        with pytest.raises(ValueError, match=match):
            to_legacy_scoring(output, claims)
        # Restore valid value
        fresh = run_kernel(to_kernel_input(claims, pit_epoch=110.0, coin="BTC", query="BTC"))
        object.__setattr__(output, field, getattr(fresh, field))


def test_output_adapter_requires_complete_app_claim_graph_equivalence() -> None:
    claims = _claims()
    output = run_kernel(to_kernel_input(claims, pit_epoch=110.0, coin="BTC", query="BTC"))
    forged = _claims()
    forged[0].id = "hijacked"
    with pytest.raises(ValueError, match="complete app claim graph"):
        to_legacy_scoring(output, forged)


@pytest.mark.parametrize("invalid_trust", [-0.1, 1.1, 999.0])
def test_scored_claim_trust_is_bounded_at_construction_and_revalidation(
    invalid_trust: float,
) -> None:
    claims = _claims()
    output = run_kernel(to_kernel_input(claims, pit_epoch=110.0, coin="BTC", query="BTC"))
    item = output.scored_claims[0]
    with pytest.raises(ValueError, match="trust must be in"):
        type(item)(claim=item.claim, trust=invalid_trust)

    object.__setattr__(item, "trust", invalid_trust)
    with pytest.raises(ValueError, match="trust must be in"):
        to_legacy_scoring(output, claims)


def test_kernel_input_rejects_nan_pit_epoch() -> None:
    claims = _claims()
    with pytest.raises(ValueError):
        to_kernel_input(claims, pit_epoch=float("nan"), coin="BTC", query="BTC")


def test_to_legacy_scoring_rejects_non_kernel_output() -> None:
    with pytest.raises(ValueError, match="exact KernelOutput"):
        to_legacy_scoring(object(), [])


def test_hostile_app_claim_ids_are_rejected() -> None:
    claims = _claims()
    hostile = _claims()
    hostile.append(Claim("c4", "extra", Document("d4", "news", "Reuters", "extra", 104.0)))
    output = run_kernel(to_kernel_input(claims, pit_epoch=110.0, coin="BTC", query="BTC"))
    with pytest.raises(ValueError, match="complete app claim graph"):
        to_legacy_scoring(output, hostile)


def test_dim_output_is_equal_roundtrip() -> None:
    """Shapeless interface test: run_kernel -> to_legacy_scoring preserves values."""
    claims = _claims()
    kernel_input = to_kernel_input(claims, pit_epoch=110.0, coin="BTC", query="BTC outlook")
    output = run_kernel(kernel_input)
    adapted_scored, adapted_brief = to_legacy_scoring(output, claims)

    assert adapted_brief.query == "BTC outlook"
    assert adapted_brief.confidence == output.trust_score
    assert adapted_brief.calibrated_confidence == output.confidence
    assert len(adapted_brief.supporting) == output.supporting_count
    assert len(adapted_brief.contrarian) == len(output.contrarian)
    for s, ksc in zip(adapted_scored, output.scored_claims):
        assert s.claim.id == ksc.claim.id
        assert s.trust == ksc.trust
        assert s.components == dict(ksc.components)
        assert s.manip_flags == list(ksc.manip_flags)
