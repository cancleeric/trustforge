"""Green compatibility oracle for #453 when no resolution is supplied."""

from __future__ import annotations

from trustforge_core import KernelClaim, KernelDocument, KernelInput, run_kernel


def _claim(
    claim_id: str,
    *,
    source: str | None = None,
    direction: str = "neutral",
) -> KernelClaim:
    document = KernelDocument(
        claim_id,
        "price",
        source or claim_id,
        "BTC signal",
        100.0,
    )
    return KernelClaim(claim_id, "BTC signal", document, "fact", direction)


def test_resolution_absent_empty_compatibility() -> None:
    output = run_kernel(KernelInput((), 100.0, "BTC", "BTC"))
    assert output.trust_score == 0.0
    assert output.confidence == 0.0
    assert output.abstain is True
    assert output.direction == "不明"
    assert output.reason_codes == (
        "low_calibrated_confidence",
        "insufficient_independent_sources",
    )
    assert output.supporting == ()
    assert output.contrarian == ()


def test_resolution_absent_sparse_compatibility() -> None:
    output = run_kernel(KernelInput((_claim("a"),), 100.0, "BTC", "BTC"))
    assert output.trust_score == 0.625
    assert output.confidence == 0.4188
    assert output.abstain is True
    assert output.direction == "不明"
    assert output.reason_codes == ("insufficient_independent_sources",)
    assert output.supporting_count == 1
    assert output.independent_sources == 1


def test_resolution_absent_bullish_and_deterministic_compatibility() -> None:
    claims = tuple(_claim(str(index), direction="bullish") for index in range(3))
    inp = KernelInput(claims, 100.0, "BTC", "BTC")
    first = run_kernel(inp)
    second = run_kernel(inp)
    assert first == second
    assert first.trust_score == 0.625
    assert first.confidence == 0.6187
    assert first.abstain is False
    assert first.direction == "偏多"
    assert first.reason_codes == ()
    assert first.supporting_count == 3
    assert first.independent_sources == 3
