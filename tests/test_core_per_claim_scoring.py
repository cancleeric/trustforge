"""Pure per-claim scoring engine boundary tests (#451)."""

from __future__ import annotations

import dataclasses
import inspect
import math

import pytest

from trustforge_core import (
    DEFAULT_HALF_LIVES,
    DEFAULT_SCORE_WEIGHTS,
    DEFAULT_SOURCE_REPUTATIONS,
    KernelClaim,
    KernelDocument,
    KernelReputationTrace,
    corroboration_score,
    manipulation_flags,
    manipulation_penalty,
    score_claim,
    source_reputation,
)


BASE_TS = 1_700_000_000.0


def _claim(
    *,
    kind: str = "news",
    source: str = "wire",
    text: str = "BTC adoption expands",
    timestamp: float = BASE_TS,
    metadata: tuple = (),
) -> KernelClaim:
    return KernelClaim(
        "claim",
        text,
        KernelDocument(
            "document", kind, source, text, timestamp, metadata=metadata
        ),
        "fact",
        "bullish",
    )


def test_score_claim_is_provider_free_and_keyword_only_after_claim():
    signature = inspect.signature(score_claim)

    assert list(signature.parameters) == [
        "claim",
        "now",
        "weights",
        "reputations",
        "half_lives",
        "independent_sources",
        "dynamic_reputation",
        "reputation_trace",
        "info_flags",
    ]
    assert all(
        token not in signature.parameters
        for token in ("callback", "provider", "stance_fn", "claims", "claim_pool")
    )
    assert signature.parameters["now"].kind is inspect.Parameter.KEYWORD_ONLY


def test_default_tables_are_immutable_and_own_fallback_half_life():
    assert type(DEFAULT_SCORE_WEIGHTS) is tuple
    assert type(DEFAULT_SOURCE_REPUTATIONS) is tuple
    assert dict(DEFAULT_HALF_LIVES) == {
        "default": 12.0,
        "whale_onchain": 2.0,
        "celebrity_trade": 2.0,
    }


def test_score_claim_returns_exact_immutable_components():
    claim = _claim()

    result = score_claim(claim, now=BASE_TS)

    assert result.claim is claim
    assert result.trust == 0.475
    assert result.components == (
        ("reputation", 0.65),
        ("corroboration", 0.0),
        ("recency", 1.0),
        ("manipulation", 0.0),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.trust = 0.0  # type: ignore[misc]


def test_manipulation_is_canonical_and_preserves_legacy_pattern_order():
    text = "BTC pump shill 但不會暴漲"

    assert manipulation_flags(text) == ("shill", "pump")
    assert manipulation_penalty(text, "social") == 1.0
    result = score_claim(_claim(kind="social", text=text), now=BASE_TS)
    assert result.trust == 0.0
    assert result.manip_flags == ("shill", "pump")


def test_manipulation_extra_hits_validate_before_arithmetic_and_saturate_huge_values():
    assert manipulation_penalty("clean", "news", extra_hits=10**10_000) == 1.0
    for value in (-(10**10_000), True, False, 1.5):
        with pytest.raises(ValueError):
            manipulation_penalty("clean", "news", extra_hits=value)  # type: ignore[arg-type]


def test_manipulation_scalar_subclasses_are_rejected_without_hooks():
    class BadStr(str):
        hooks = 0

        def __hash__(self) -> int:
            type(self).hooks += 1
            raise RuntimeError("hash must not run")

        def __eq__(self, other: object) -> bool:
            type(self).hooks += 1
            raise RuntimeError("equality must not run")

        def __str__(self) -> str:
            type(self).hooks += 1
            raise RuntimeError("str must not run")

    class BadInt(int):
        hooks = 0

        def __lt__(self, other: object) -> bool:
            type(self).hooks += 1
            raise RuntimeError("comparison must not run")

        def __float__(self) -> float:
            type(self).hooks += 1
            raise RuntimeError("float must not run")

    for args in (
        (BadStr("text"), "news", 0),
        ("text", BadStr("news"), 0),
        ("text", "news", BadInt(1)),
    ):
        with pytest.raises(ValueError):
            manipulation_penalty(args[0], args[1], extra_hits=args[2])  # type: ignore[arg-type]
    assert BadStr.hooks == 0
    assert BadInt.hooks == 0


def test_source_reputation_override_celebrity_cap_and_dynamic_scalar():
    verified = _claim(
        kind="celebrity_trade", metadata=(("verified_onchain", True),)
    )
    unverified = _claim(
        kind="celebrity_trade", metadata=(("verified_onchain", False),)
    )
    overridden = _claim(metadata=(("reputation", 0.8),))

    assert dict(score_claim(verified, now=BASE_TS).components)["reputation"] == 0.5
    assert (
        dict(score_claim(unverified, now=BASE_TS).components)["reputation"] == 0.35
    )
    assert (
        dict(score_claim(overridden, now=BASE_TS).components)["reputation"] == 0.8
    )
    assert (
        dict(
            score_claim(
                unverified, now=BASE_TS, dynamic_reputation=0.9
            ).components
        )["reputation"]
        == 0.35
    )


@pytest.mark.parametrize(
    "kind,metadata,dynamic",
    [
        ("news", {"reputation": 0.8}, None),
        ("news", {}, 0.72),
        ("celebrity_trade", {"verified_onchain": False}, 0.9),
        ("celebrity_trade", {"verified_onchain": True}, 0.9),
    ],
)
def test_public_and_per_claim_source_reputation_share_canonical_semantics(
    kind: str, metadata: dict[str, object], dynamic: float | None
):
    claim = _claim(kind=kind, metadata=tuple(metadata.items()))
    source_key = claim.document.source
    expected = source_reputation(
        kind=kind,
        source_key=source_key,
        metadata=metadata,
        reputations=dict(DEFAULT_SOURCE_REPUTATIONS),
        dynamic=None if dynamic is None else {source_key: dynamic},
    )

    actual = score_claim(
        claim, now=BASE_TS, dynamic_reputation=dynamic
    )

    assert dict(actual.components)["reputation"] == expected


def test_resolved_trace_and_info_flags_pass_through_without_pool_logic():
    trace = KernelReputationTrace("wire", 0.65, 0.72, 3, 1, 2)

    result = score_claim(
        _claim(),
        now=BASE_TS,
        dynamic_reputation=0.72,
        reputation_trace=trace,
        info_flags=("transparent",),
    )

    assert result.reputation_trace is trace
    assert result.info_flags == ("transparent",)
    assert dict(result.components)["reputation"] == 0.72


@pytest.mark.parametrize(
    "sources,expected",
    [
        ((), 0.0),
        (("a",), 0.5),
        (("a", "b"), 0.75),
        (("a", "a", "b"), 0.75),
    ],
)
def test_corroboration_uses_unique_resolved_sources(
    sources: tuple[str, ...], expected: float
):
    assert corroboration_score(sources) == expected
    assert (
        dict(
            score_claim(
                _claim(), now=BASE_TS, independent_sources=sources
            ).components
        )["corroboration"]
        == expected
    )


def test_valid_custom_weights_preserve_formula():
    weights = (("src", 0.4), ("corr", 0.3), ("rec", 0.2), ("manip", 0.5))

    result = score_claim(_claim(), now=BASE_TS, weights=weights)

    assert result.trust == 0.46


def test_per_kind_half_life_override_is_supported():
    result = score_claim(
        _claim(timestamp=BASE_TS),
        now=BASE_TS + 6 * 3_600,
        half_lives=(("default", 12.0), ("news", 6.0)),
    )

    assert dict(result.components)["recency"] == 0.5


@pytest.mark.parametrize(
    "weights",
    [
        [],
        (("src", 0.5),),
        (("src", 0.5), ("src", 0.5), ("corr", 0.25), ("rec", 0.15), ("manip", 0.4)),
        (("src", float("nan")), ("corr", 0.25), ("rec", 0.15), ("manip", 0.4)),
        (("src", -0.5), ("corr", 0.25), ("rec", 0.15), ("manip", 0.4)),
        (("src", True), ("corr", 0.25), ("rec", 0.15), ("manip", 0.4)),
    ],
)
def test_invalid_weights_fail_closed(weights: object):
    with pytest.raises(ValueError):
        score_claim(_claim(), now=BASE_TS, weights=weights)  # type: ignore[arg-type]


def test_duplicate_or_invalid_metadata_scalars_fail_closed():
    duplicate = _claim(metadata=(("reputation", 0.5), ("reputation", 0.7)))
    bad_reputation = _claim(metadata=(("reputation", "high"),))
    bad_verified = _claim(
        kind="celebrity_trade", metadata=(("verified_onchain", 1),)
    )

    for claim in (duplicate, bad_reputation, bad_verified):
        with pytest.raises(ValueError):
            score_claim(claim, now=BASE_TS)


@pytest.mark.parametrize("now", [True, float("nan"), float("inf"), object()])
def test_invalid_now_fails_closed(now: object):
    with pytest.raises(ValueError):
        score_claim(_claim(), now=now)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dynamic_reputation": float("nan")},
        {"dynamic_reputation": 1.1},
        {"independent_sources": ["a"]},
        {"independent_sources": ("a", object())},
        {"info_flags": ["flag"]},
        {"half_lives": (("default", 0.0), ("whale_onchain", 2.0), ("celebrity_trade", 2.0))},
        {"half_lives": (("news", 6.0),)},
        {"half_lives": (("default", 12.0), ("news", 6.0), ("news", 8.0))},
        {"half_lives": (("default", 12.0), ("news", float("nan")))},
        {"reputations": (("news", math.inf),)},
    ],
)
def test_invalid_resolved_inputs_fail_closed(kwargs: dict[str, object]):
    with pytest.raises(ValueError):
        score_claim(_claim(), now=BASE_TS, **kwargs)  # type: ignore[arg-type]
