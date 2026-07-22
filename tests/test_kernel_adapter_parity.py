"""Characterize the app-to-kernel adapter before production routing (#420)."""

from __future__ import annotations

import pytest

from trustforge.agent.kernel_mapper import to_legacy_scoring, to_resolved_kernel_input
from trustforge.direction_resolution import DIRECTION_POLICY_VERSION, ResolvedDirection
from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim, aggregate, score
from trustforge.trust.scoring import resolve_kernel_run_resolution
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


def test_resolved_adapter_matches_legacy_score_and_aggregate() -> None:
    claims = _claims()
    direction = ResolvedDirection(
        value="bullish",
        policy_version=DIRECTION_POLICY_VERSION,
        method="ohlcv-close",
        input_ids=("d1", "d2"),
        reason="characterization fixture",
    )

    legacy_scored = score(
        claims,
        now=110.0,
        stance_fn=lambda _left, _right: "neutral",
        dynamic_reputation=False,
    )
    legacy = aggregate(legacy_scored, query="BTC outlook", coin="BTC")

    request = to_resolved_kernel_input(
        claims,
        pit_epoch=110.0,
        coin="BTC",
        query="BTC outlook",
        direction=direction,
        stance_fn=lambda _left, _right: "neutral",
        dynamic_reputation=False,
    )
    output = run_kernel(request)
    adapted_scored, adapted_brief = to_legacy_scoring(output, claims)

    assert [item.claim.id for item in adapted_scored] == [
        item.claim.id for item in legacy_scored
    ]
    assert [item.trust for item in adapted_scored] == [
        item.trust for item in legacy_scored
    ]
    assert [item.claim.id for item in adapted_brief.supporting] == [
        item.claim.id for item in legacy.supporting
    ]
    assert [item.claim.id for item in adapted_brief.contrarian] == [
        item.claim.id for item in legacy.contrarian
    ]
    assert adapted_brief.confidence == legacy.confidence
    assert adapted_brief.calibrated_confidence == legacy.calibrated_confidence
    assert output.direction == "bullish"


def test_resolved_adapter_does_not_repeat_stance_provider_calls() -> None:
    legacy_calls: list[tuple[str, str]] = []
    adapter_calls: list[tuple[str, str]] = []

    def legacy_stance(left: str, right: str) -> str:
        legacy_calls.append((left, right))
        return "neutral"

    def adapter_stance(left: str, right: str) -> str:
        adapter_calls.append((left, right))
        return "neutral"

    direction = ResolvedDirection(
        value="neutral",
        policy_version=DIRECTION_POLICY_VERSION,
        method="no-signal",
        input_ids=(),
        reason="no signal",
    )
    score(
        _claims(),
        now=110.0,
        stance_fn=legacy_stance,
        dynamic_reputation=True,
        offline=False,
    )
    request = to_resolved_kernel_input(
        _claims(),
        pit_epoch=110.0,
        coin="BTC",
        query="BTC outlook",
        direction=direction,
        stance_fn=adapter_stance,
        dynamic_reputation=True,
        offline=False,
    )
    calls_after_resolution = len(adapter_calls)

    run_kernel(request)

    assert adapter_calls == legacy_calls
    assert len(adapter_calls) == calls_after_resolution


def test_dynamic_offline_resolution_preserves_trace_flags_and_scores() -> None:
    claims = _claims()
    direction = ResolvedDirection(
        value="unknown",
        policy_version=DIRECTION_POLICY_VERSION,
        method="no-signal",
        input_ids=(),
        reason="no signal",
    )
    legacy = score(
        claims,
        now=110.0,
        stance_fn=lambda _left, _right: "neutral",
        dynamic_reputation=True,
        offline=True,
    )
    output = run_kernel(
        to_resolved_kernel_input(
            claims,
            pit_epoch=110.0,
            coin="BTC",
            query="BTC outlook",
            direction=direction,
            stance_fn=lambda _left, _right: "neutral",
            dynamic_reputation=True,
            offline=True,
        )
    )

    adapted, _ = to_legacy_scoring(output, claims)
    assert [item.trust for item in adapted] == [item.trust for item in legacy]
    assert [item.info_flags for item in adapted] == [item.info_flags for item in legacy]
    assert [item.reputation_trace for item in adapted] == [
        item.reputation_trace for item in legacy
    ]


def test_resolution_matches_legacy_when_explicit_stance_callback_raises() -> None:
    shared = "BTC ETF institutional demand expands market liquidity significantly"
    claims = [
        Claim(
            f"x{i}",
            shared,
            Document(f"dx{i}", "news", f"source-{i}", shared, 100.0 + i),
            "fact",
            "bullish",
        )
        for i in range(2)
    ]
    legacy_calls = 0
    adapter_calls = 0

    def broken_legacy(_left: str, _right: str) -> str:
        nonlocal legacy_calls
        legacy_calls += 1
        raise RuntimeError("provider unavailable")

    def broken_adapter(_left: str, _right: str) -> str:
        nonlocal adapter_calls
        adapter_calls += 1
        raise RuntimeError("provider unavailable")

    direction = ResolvedDirection(
        value="neutral",
        policy_version=DIRECTION_POLICY_VERSION,
        method="no-signal",
        input_ids=(),
        reason="no signal",
    )
    with pytest.raises(RuntimeError, match="provider unavailable"):
        score(claims, now=110.0, stance_fn=broken_legacy, dynamic_reputation=True)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        to_resolved_kernel_input(
            claims,
            pit_epoch=110.0,
            coin="BTC",
            query="BTC outlook",
            direction=direction,
            stance_fn=broken_adapter,
            dynamic_reputation=True,
        )
    assert adapter_calls == legacy_calls
    assert adapter_calls > 0


def test_resolved_adapter_rejects_non_contract_direction() -> None:
    direction = object()

    try:
        to_resolved_kernel_input(
            _claims(),
            pit_epoch=110.0,
            coin="BTC",
            query="BTC outlook",
            direction=direction,  # type: ignore[arg-type]
            stance_fn=None,
        )
    except ValueError as exc:
        assert str(exc) == "direction must be an exact ResolvedDirection"
    else:  # pragma: no cover - explicit hostile-input assertion
        raise AssertionError("hostile direction was accepted")


def test_resolved_adapter_rejects_invalid_pit_before_resolution() -> None:
    direction = ResolvedDirection(
        value="neutral",
        policy_version=DIRECTION_POLICY_VERSION,
        method="no-signal",
        input_ids=(),
        reason="no signal",
    )
    calls = 0

    def stance(_left: str, _right: str) -> str:
        nonlocal calls
        calls += 1
        return "neutral"

    with pytest.raises(ValueError, match="pit_epoch must be a finite number"):
        to_resolved_kernel_input(
            _claims(),
            pit_epoch=float("nan"),
            coin="BTC",
            query="BTC outlook",
            direction=direction,
            stance_fn=stance,
        )
    assert calls == 0


@pytest.mark.parametrize(
    "weights",
    [
        {"src": 1.0},
        {"src": 0.5, "corr": 0.25, "rec": 0.15, "manip": 0.4, "extra": 0.1},
        {"src": True, "corr": 0.25, "rec": 0.15, "manip": 0.4},
        {"src": float("nan"), "corr": 0.25, "rec": 0.15, "manip": 0.4},
        {"src": float("inf"), "corr": 0.25, "rec": 0.15, "manip": 0.4},
        {"src": 1.1, "corr": 0.25, "rec": 0.15, "manip": 0.4},
    ],
)
def test_resolution_rejects_invalid_policy_before_callback(weights: dict) -> None:
    claims = _claims()
    calls = 0

    def stance(_left: str, _right: str) -> str:
        nonlocal calls
        calls += 1
        return "neutral"

    with pytest.raises(ValueError):
        resolve_kernel_run_resolution(
            claims,
            110.0,
            resolved_direction="neutral",
            weights=weights,
            stance_fn=stance,
        )
    assert calls == 0


def test_resolution_rejects_duplicate_claim_ids() -> None:
    claims = _claims()
    duplicate = [claims[0], Claim("c1", claims[1].text, claims[1].doc)]
    direction = ResolvedDirection(
        value="neutral",
        policy_version=DIRECTION_POLICY_VERSION,
        method="no-signal",
        input_ids=(),
        reason="no signal",
    )
    with pytest.raises(ValueError, match="duplicate claim IDs"):
        to_resolved_kernel_input(
            duplicate,
            pit_epoch=110.0,
            coin="BTC",
            query="BTC outlook",
            direction=direction,
        )


def test_output_adapter_revalidates_tampered_topology_without_hooks() -> None:
    claims = _claims()
    direction = ResolvedDirection(
        value="neutral",
        policy_version=DIRECTION_POLICY_VERSION,
        method="no-signal",
        input_ids=(),
        reason="no signal",
    )
    output = run_kernel(
        to_resolved_kernel_input(
            claims,
            pit_epoch=110.0,
            coin="BTC",
            query="BTC outlook",
            direction=direction,
            dynamic_reputation=False,
        )
    )
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

    clean = run_kernel(
        to_resolved_kernel_input(
            claims,
            pit_epoch=110.0,
            coin="BTC",
            query="BTC outlook",
            direction=direction,
            dynamic_reputation=False,
        )
    )
    object.__setattr__(clean.scored_claims[0], "components", (Hostile(),))
    with pytest.raises(ValueError, match="components must contain"):
        to_legacy_scoring(clean, claims)
    assert hooks == 0


@pytest.mark.parametrize(
    ("now", "kwargs"),
    [
        (float("nan"), {}),
        (float("inf"), {}),
        (-1.0, {}),
        (110.0, {"reputation_iterations": 0}),
        (110.0, {"reputation_iterations": 6}),
        (110.0, {"reputation_iterations": True}),
        (110.0, {"dynamic_reputation": 1}),
        (110.0, {"offline": 0}),
        (110.0, {"stance_pair_budget": -1}),
        (110.0, {"stance_pair_budget": True}),
    ],
)
def test_direct_resolution_validates_cost_boundary_before_callback(
    now: float, kwargs: dict
) -> None:
    calls = 0

    def stance(_left: str, _right: str) -> str:
        nonlocal calls
        calls += 1
        return "neutral"

    with pytest.raises(ValueError):
        resolve_kernel_run_resolution(
            _claims(),
            now,
            resolved_direction="neutral",
            stance_fn=stance,
            **kwargs,
        )
    assert calls == 0


def test_direct_resolution_rejects_duplicate_and_non_claims_before_callback() -> None:
    claims = _claims()
    calls = 0

    def stance(_left: str, _right: str) -> str:
        nonlocal calls
        calls += 1
        return "neutral"

    duplicate = [claims[0], Claim("c1", claims[1].text, claims[1].doc)]
    with pytest.raises(ValueError, match="duplicate claim IDs"):
        resolve_kernel_run_resolution(
            duplicate,
            110.0,
            resolved_direction="neutral",
            stance_fn=stance,
        )
    with pytest.raises(ValueError, match="exact list of exact Claim"):
        resolve_kernel_run_resolution(  # type: ignore[arg-type]
            [object()],
            110.0,
            resolved_direction="neutral",
            stance_fn=stance,
        )
    assert calls == 0
