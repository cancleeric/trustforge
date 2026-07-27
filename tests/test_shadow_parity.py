"""P0-6 Shadow parity & promotion threshold tests (Issue #732)."""

from __future__ import annotations

import pytest

from trustforge.agent import shadow as sh
from trustforge.agent.kernel_mapper import to_kernel_input
from trustforge.agent.shadow import (
    CANARY_STOP_PARITY_RATE,
    MIN_COIN_COVERAGE,
    MIN_QTYPE_COVERAGE,
    PARITY_CONFIDENCE_DELTA_MAX,
    PARITY_SUPPORTING_JACCARD_MIN,
    PARITY_TRUST_DELTA_MAX,
    PROMOTION_PARITY_RATE_MIN,
    SHADOW_WINDOW,
    ShadowAccumulator,
    ShadowParityResult,
    compare_outputs,
    record_shadow_run,
    reset_shadow_accumulator,
)
from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim, ScoredClaim, aggregate, score
from trustforge_core import (
    KernelClaim,
    KernelDocument,
    KernelOutput,
    KernelScoredClaim,
    canonical_source,
    run_kernel,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_sc(
    claim_id: str, trust: float, direction: str = "bullish", kind: str = "news"
) -> ScoredClaim:
    doc = Document(claim_id + "_doc", kind, "coindesk", "test text", 100.0)
    return ScoredClaim(
        claim=Claim(claim_id, "test claim text", doc, "fact", direction),
        trust=trust,
    )


def _make_minimal_kernel_claim(claim_id: str, direction: str = "bullish", source: str = "coindesk") -> KernelClaim:
    return KernelClaim(
        id=claim_id,
        text="test kernel claim text",
        document=KernelDocument(
            id=claim_id + "_doc",
            kind="news",
            source=source,
            text="test kernel doc text",
            timestamp=100.0,
            url=f"https://{source}.com/{claim_id}",
            metadata=(("coin", "BTC"),),
        ),
        direction=direction,
    )


def _make_minimal_kernel_sc(claim_id: str, trust: float, direction: str = "bullish") -> KernelScoredClaim:
    return KernelScoredClaim(
        claim=_make_minimal_kernel_claim(claim_id, direction),
        trust=trust,
        components=(("reputation", 0.5), ("corroboration", 0.25), ("recency", 0.15)),
    )


def _make_minimal_kernel_output(
    *,
    trust_score: float = 0.7,
    confidence: float = 0.7,
    abstain: bool = False,
    direction: str = "bullish",
    supporting_claims: tuple[str, ...] = ("c1",),
    decision_state: str = "normal",
) -> KernelOutput:
    """Create a minimal valid KernelOutput for shadow parity testing.

    Side-steps the full graph validation by using ``object.__setattr__``
    to populate the sealed slots after constructing a real output.
    """
    claims = tuple(
        _make_minimal_kernel_claim(cid, direction) for cid in ("c1", "c2", "c3")
    )
    kernel_input = to_kernel_input(
        [
            Claim(cid, "t", Document(cid + "_d", "news", "coindesk", "t", 100.0),
                  "fact", direction)
            for cid in ("c1", "c2", "c3")
        ],
        pit_epoch=110.0,
        coin="BTC",
        query="test",
    )
    real_output = run_kernel(kernel_input)

    scs = tuple(
        _make_minimal_kernel_sc(cid, trust_score, direction)
        for cid in ("c1", "c2", "c3")
    )
    supporting = tuple(
        _make_minimal_kernel_sc(cid, trust_score, direction)
        for cid in supporting_claims
    )

    object.__setattr__(real_output, "trust_score", trust_score)
    object.__setattr__(real_output, "confidence", confidence)
    object.__setattr__(real_output, "abstain", abstain)
    object.__setattr__(real_output, "direction", direction)
    object.__setattr__(real_output, "decision_state", decision_state)
    object.__setattr__(real_output, "scored_claims", scs)
    object.__setattr__(real_output, "supporting", supporting)
    object.__setattr__(real_output, "contrarian", ())
    object.__setattr__(real_output, "supporting_count", len(supporting))
    object.__setattr__(
        real_output,
        "independent_sources",
        len({canonical_source(item.claim.document.source) for item in supporting}),
    )
    return real_output


# ---------------------------------------------------------------------------
# compare_outputs — happy path
# ---------------------------------------------------------------------------

def test_compare_outputs_perfect_match() -> None:
    sc = _make_sc("c1", 0.75)
    ko = _make_minimal_kernel_output(
        confidence=0.75,
        trust_score=0.75,
        direction="bullish",
        supporting_claims=("c1",),
    )
    result = compare_outputs(
        kernel=ko,
        legacy_confidence=0.75,
        legacy_trust_raw=0.75,
        legacy_scored=[sc],
        coin="BTC",
        qtype_value="analysis",
    )
    assert result.parity_passed is True
    assert result.blocking_reasons == ()
    assert result.delta_confidence == pytest.approx(0.0)
    assert result.delta_trust == pytest.approx(0.0)
    assert result.direction_match is True
    assert result.decision_state_match is True


# ---------------------------------------------------------------------------
# compare_outputs — blocking failures
# ---------------------------------------------------------------------------

def test_compare_outputs_blocks_on_confidence_delta() -> None:
    sc = _make_sc("c1", 0.75)
    ko = _make_minimal_kernel_output(
        confidence=0.75 + PARITY_CONFIDENCE_DELTA_MAX + 0.01,
        trust_score=0.75,
        direction="bullish",
        supporting_claims=("c1",),
    )
    result = compare_outputs(
        kernel=ko,
        legacy_confidence=0.75,
        legacy_trust_raw=0.75,
        legacy_scored=[sc],
        coin="BTC",
        qtype_value="analysis",
    )
    assert result.parity_passed is False
    assert any("confidence_delta" in r for r in result.blocking_reasons)


def test_compare_outputs_blocks_on_trust_delta() -> None:
    sc = _make_sc("c1", 0.75)
    ko = _make_minimal_kernel_output(
        confidence=0.75,
        trust_score=0.75 + PARITY_TRUST_DELTA_MAX + 0.01,
        direction="bullish",
        supporting_claims=("c1",),
    )
    result = compare_outputs(
        kernel=ko,
        legacy_confidence=0.75,
        legacy_trust_raw=0.75,
        legacy_scored=[sc],
        coin="BTC",
        qtype_value="analysis",
    )
    assert result.parity_passed is False
    assert any("trust_delta" in r for r in result.blocking_reasons)


def test_compare_outputs_blocks_on_direction_mismatch() -> None:
    sc = _make_sc("c1", 0.75, direction="bearish")
    ko = _make_minimal_kernel_output(
        confidence=0.75,
        trust_score=0.75,
        direction="bullish",
        supporting_claims=("c1",),
    )
    result = compare_outputs(
        kernel=ko,
        legacy_confidence=0.75,
        legacy_trust_raw=0.75,
        legacy_scored=[sc],
        coin="BTC",
        qtype_value="analysis",
    )
    assert result.parity_passed is False
    assert any("direction_mismatch" in r for r in result.blocking_reasons)


def test_compare_outputs_blocks_on_decision_state_mismatch() -> None:
    sc = _make_sc("c1", 0.75)
    ko = _make_minimal_kernel_output(
        confidence=0.75,
        trust_score=0.75,
        direction="bullish",
        abstain=True,
        decision_state="abstain",
        supporting_claims=("c1",),
    )
    result = compare_outputs(
        kernel=ko,
        legacy_confidence=0.75,
        legacy_trust_raw=0.75,
        legacy_scored=[sc],
        coin="BTC",
        qtype_value="analysis",
    )
    assert result.parity_passed is False
    assert any("decision_mismatch" in r for r in result.blocking_reasons)


def test_compare_outputs_blocks_on_low_supporting_jaccard() -> None:
    sc_a = _make_sc("c1", 0.75)
    sc_b = _make_sc("c2", 0.75)
    ko = _make_minimal_kernel_output(
        confidence=0.75,
        trust_score=0.75,
        direction="bullish",
        supporting_claims=("c1",),  # only c1 in kernel
    )
    result = compare_outputs(
        kernel=ko,
        legacy_confidence=0.75,
        legacy_trust_raw=0.75,
        legacy_scored=[sc_a, sc_b],  # both c1 and c2 in legacy
        coin="BTC",
        qtype_value="analysis",
    )
    assert result.parity_passed is False
    assert any("supporting_jaccard" in r for r in result.blocking_reasons)


# ---------------------------------------------------------------------------
# ShadowAccumulator — window & promotion gating
# ---------------------------------------------------------------------------

def test_accumulator_window_limits_size() -> None:
    acc = ShadowAccumulator(_window_size=5)
    for i in range(10):
        acc.record(_passing_result(coin="BTC", qtype_value="analysis"))
    assert acc.window_runs == 5
    assert acc.total_runs == 10


def test_accumulator_not_promoted_until_window_full_and_gates_met() -> None:
    acc = ShadowAccumulator(_window_size=3)
    for _ in range(3):
        acc.record(_passing_result(coin="BTC", qtype_value="analysis"))
    # Only 1 coin, 1 qtype -> promotion not eligible
    assert acc.promotion_eligible is False
    assert acc.promoted is False


def test_accumulator_promotes_when_all_gates_met() -> None:
    acc = ShadowAccumulator(_window_size=3)
    coins = ["BTC", "ETH", "SOL"]
    qtypes = ["analysis", "hypothesis"]
    for i in range(3):
        acc.record(_passing_result(coin=coins[i], qtype_value=qtypes[i % 2]))
    assert acc.promotion_eligible is True
    assert acc.promoted is True


def test_accumulator_blocks_promotion_on_parity_rate() -> None:
    acc = ShadowAccumulator(_window_size=3)
    coins = ["BTC", "ETH", "SOL"]
    for i in range(3):
        acc.record(
            _passing_result(coin=coins[i], qtype_value="analysis",
                            parity_passed=(i < 2))
        )
    # 2/3 passed = 0.667 < 0.90
    assert acc.promotion_eligible is False
    assert acc.promoted is False


def test_accumulator_blocks_promotion_on_blocking_streak() -> None:
    acc = ShadowAccumulator(_window_size=5)
    coins = ["BTC", "ETH", "SOL", "ADA", "XRP"]
    for i in range(5):
        acc.record(
            _passing_result(coin=coins[i], qtype_value="analysis",
                            parity_passed=(i < 2))
        )
    # last 3 failed (streak=3) — should block
    assert acc.promotion_eligible is False
    assert acc.promoted is False


# ---------------------------------------------------------------------------
# Canary stop
# ---------------------------------------------------------------------------

def test_canary_stop_revokes_promotion() -> None:
    acc = ShadowAccumulator(_window_size=5)
    coins = ["BTC", "ETH", "SOL", "ADA", "XRP"]
    qtypes = ["analysis", "hypothesis"]
    # Fill window with all-pass to trigger promotion
    for i in range(5):
        acc.record(_passing_result(coin=coins[i], qtype_value=qtypes[i % 2]))
    assert acc.promoted is True
    # Inject failures until canary threshold breached
    for i in range(5):
        acc.record(_passing_result(coin=coins[i], qtype_value=qtypes[i % 2],
                                   parity_passed=False))
    # After all fails, promoted should be False (canary stop revoked it)
    assert acc.promoted is False
    assert acc.parity_rate < CANARY_STOP_PARITY_RATE


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

def test_reset_clears_window_and_revokes_promotion() -> None:
    acc = ShadowAccumulator(_window_size=3)
    coins = ["BTC", "ETH", "SOL"]
    qtypes = ["analysis", "hypothesis"]
    for i in range(3):
        acc.record(_passing_result(coin=coins[i], qtype_value=qtypes[i % 2]))
    assert acc.promoted is True
    acc.reset()
    assert acc.promoted is False
    assert acc.window_runs == 0


# ---------------------------------------------------------------------------
# record_shadow_run integration
# ---------------------------------------------------------------------------

def test_record_shadow_run_returns_diagnostics() -> None:
    reset_shadow_accumulator()
    sc = _make_sc("c1", 0.75)
    ko = _make_minimal_kernel_output(
        confidence=0.75,
        trust_score=0.75,
        direction="bullish",
        supporting_claims=("c1",),
    )
    diag = record_shadow_run(
        kernel=ko,
        legacy_confidence=0.75,
        legacy_trust_raw=0.75,
        legacy_scored=[sc],
        coin="BTC",
        qtype_value="analysis",
    )
    assert diag["last_parity_passed"] is True
    assert diag["parity_rate"] == 1.0
    assert diag["promoted"] is False  # not enough runs yet


# ---------------------------------------------------------------------------
# End-to-end: real kernel vs legacy comparison (characterization, not assertion)
# ---------------------------------------------------------------------------

def test_real_kernel_legacy_comparison_does_not_raise() -> None:
    """Integration: compare real kernel output vs legacy — must not raise.

    The kernel and legacy may legitimately diverge on trust/direction
    (shadow parity exists to *detect* those divergences).  This test
    only verifies that compare_outputs() completes without exceptions.
    """
    claims = [
        Claim(
            "c1",
            "BTC ETF inflows expanded",
            Document("d1", "news", "Reuters", "BTC ETF inflows expanded", 100.0),
            "fact",
            "bullish",
        ),
        Claim(
            "c2",
            "BTC reserves fell",
            Document("d2", "onchain", "Glassnode", "BTC reserves fell", 101.0),
            "fact",
            "bullish",
        ),
    ]
    scored = score(claims, now=110.0, offline=True)
    brief = aggregate(scored, query="BTC outlook", coin="BTC")
    kernel_input = to_kernel_input(claims, pit_epoch=110.0, coin="BTC", query="BTC outlook")
    kernel_output = run_kernel(kernel_input)

    result = compare_outputs(
        kernel=kernel_output,
        legacy_confidence=brief.calibrated_confidence or brief.confidence,
        legacy_trust_raw=brief.confidence,
        legacy_scored=scored,
        coin="BTC",
        qtype_value="analysis",
    )
    # comparison produced valid result w/o raising — parity may differ
    assert isinstance(result.parity_passed, bool)
    assert isinstance(result.blocking_reasons, tuple)


# ---------------------------------------------------------------------------
# Parity edge cases
# ---------------------------------------------------------------------------

def test_empty_legacy_scored_yields_no_direction() -> None:
    ko = _make_minimal_kernel_output(
        confidence=0.5,
        trust_score=0.5,
        direction="neutral",
        supporting_claims=(),
    )
    result = compare_outputs(
        kernel=ko,
        legacy_confidence=0.5,
        legacy_trust_raw=0.5,
        legacy_scored=[],
        coin="BTC",
        qtype_value="analysis",
    )
    assert result.legacy_direction is None
    # neutral ↔ None is considered equivalent
    assert result.direction_match is True


def test_legacy_abstain_detection() -> None:
    sc = _make_sc("c1", 0.2)  # low trust -> confidence low, triggers abstain
    ko = _make_minimal_kernel_output(
        confidence=0.2,
        trust_score=0.2,
        direction="neutral",
        abstain=False,
        decision_state="low_confidence",
        supporting_claims=("c1",),
    )
    result = compare_outputs(
        kernel=ko,
        legacy_confidence=0.2,
        legacy_trust_raw=0.2,
        legacy_scored=[sc],
        coin="BTC",
        qtype_value="analysis",
    )
    assert result.legacy_abstain is True  # confidence < 0.35
    assert result.kernel_abstain is False
    assert result.decision_state_match is False
    assert result.parity_passed is False


# ---------------------------------------------------------------------------
# Accumulator diagnostics
# ---------------------------------------------------------------------------

def test_diagnostics_includes_coverage() -> None:
    acc = ShadowAccumulator(_window_size=5)
    acc.record(_passing_result(coin="BTC", qtype_value="analysis"))
    d = acc.diagnostics()
    assert d["coins_seen"] == ["BTC"]
    assert d["qtypes_seen"] == ["analysis"]
    assert d["parity_rate"] == 1.0
    assert d["promotion_eligible"] is False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _passing_result(
    *,
    coin: str = "BTC",
    qtype_value: str = "analysis",
    parity_passed: bool = True,
) -> ShadowParityResult:
    return ShadowParityResult(
        coin=coin,
        qtype_value=qtype_value,
        legacy_confidence=0.7,
        kernel_confidence=0.7,
        legacy_supporting_ids=frozenset(),
        kernel_supporting_ids=frozenset(),
        legacy_direction="bullish",
        kernel_direction="bullish",
        legacy_abstain=False,
        kernel_abstain=False,
        delta_confidence=0.0,
        delta_trust=0.0,
        supporting_jaccard=1.0,
        direction_match=True,
        decision_state_match=True,
        parity_passed=parity_passed,
    )
