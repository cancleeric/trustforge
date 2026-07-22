"""Test-only reconciliation oracle for issue #452.

Expected literals come from the independently verified legacy golden commit
``8506e7b`` and the CEO-approved decision boundary.  They must not be updated
from the current implementation's output.
"""

from __future__ import annotations

import inspect
import ast
import json
import math
from dataclasses import asdict
from pathlib import Path

import pytest

from trustforge_core import (
    DEFAULT_CALIBRATION_TABLE,
    KERNEL_CONTRACT_VERSION,
    KernelClaim,
    KernelDocument,
    KernelScoredClaim,
    aggregate_scored_claims,
)


def _scored(
    claim_id: str,
    trust: float,
    source: str = "wire",
    *,
    kind: str = "news",
    text: str = "generic market update",
    direction: str = "neutral",
    metadata: tuple[tuple[str, object], ...] = (),
) -> KernelScoredClaim:
    document = KernelDocument(
        id=f"doc-{claim_id}",
        kind=kind,
        source=source,
        text=text,
        timestamp=1.0,
        metadata=metadata,  # type: ignore[arg-type]
    )
    claim = KernelClaim(claim_id, text, document, "fact", direction)
    return KernelScoredClaim(claim, trust)


def _current(
    claims: tuple[KernelScoredClaim, ...],
    *,
    query: str = "",
    coin: str = "",
    support_threshold: float = 0.5,
    calibration_table: tuple[tuple[float, float], ...] = DEFAULT_CALIBRATION_TABLE,
):
    return aggregate_scored_claims(
        claims,
        query=query,
        coin=coin,
        support_threshold=support_threshold,
        calibration_table=calibration_table,
        contract_version=KERNEL_CONTRACT_VERSION,
    )


def test_public_api_exposes_approved_explicit_values() -> None:
    signature = inspect.signature(aggregate_scored_claims)
    assert "calibration_model_version" in signature.parameters
    assert "resolved_direction" in signature.parameters


@pytest.mark.parametrize(
    ("calibrated", "state", "reasons"),
    [
        (0.35, "low_confidence", ("below_normal_confidence",)),
        (0.50, "normal", ()),
    ],
)
def test_decision_boundaries_and_reason_order(
    calibrated: float, state: str, reasons: tuple[str, ...]
) -> None:
    output = _current(
        (_scored("a", 0.8, "one"), _scored("b", 0.8, "two")),
        calibration_table=((0.0, calibrated), (1.0, calibrated)),
    )
    assert output.confidence == calibrated
    assert output.abstain is False
    assert output.decision_state == state
    assert output.reason_codes == reasons


def test_both_abstain_reasons_have_stable_order() -> None:
    output = _current(())
    assert output.reason_codes == (
        "low_calibrated_confidence",
        "insufficient_independent_sources",
    )


def test_same_source_alias_and_flood_do_not_inflate_independence() -> None:
    aliases = (_scored("a", 0.8, "CoinDesk"), _scored("b", 0.8, " coindesk "))
    alias_output = _current(aliases)
    flood_output = _current(tuple(_scored(f"f{i:02}", 0.8, "same") for i in range(12)))
    assert alias_output.independent_sources == 1
    assert alias_output.abstain is True
    assert flood_output.supporting_count == 10
    assert flood_output.independent_sources == 1
    assert flood_output.abstain is True


def test_legacy_sum_query_fallback_and_equal_order() -> None:
    claims = (
        _scored("first", 0.7, "a"),
        _scored("second", 0.7, "b"),
        _scored("third", 0.7, "c"),
    )
    output = _current(claims, query="unmatched query")
    assert output.trust_score == 0.6999999999999998
    assert tuple(item.claim.id for item in output.supporting) == (
        "first",
        "second",
        "third",
    )


def test_coin_scope_keeps_specific_then_generic_and_excludes_other_coin() -> None:
    output = _current(
        (
            _scored("generic", 0.7, "generic"),
            _scored("btc", 0.8, "btc", text="BTC ETF inflow"),
            _scored("eth", 0.9, "eth", text="ETH staking inflow"),
        ),
        coin="BTC",
    )
    assert tuple(item.claim.id for item in output.supporting) == ("btc", "generic")


def test_support_threshold_exact_half_is_inclusive() -> None:
    output = _current((_scored("at", 0.5, "a"), _scored("below", 0.499, "b")))
    assert tuple(item.claim.id for item in output.supporting) == ("at",)
    assert tuple(item.claim.id for item in output.contrarian) == ("below",)


def test_resolved_direction_is_an_exact_outer_passthrough() -> None:
    output = aggregate_scored_claims(
        (_scored("a", 0.8, "one", direction="bearish"),),
        query="",
        calibration_model_version="fixed-heuristic-v1",  # type: ignore[call-arg]
        calibration_table=(),
        resolved_direction="outer-resolved",  # type: ignore[call-arg]
    )
    assert output.direction == "outer-resolved"


def test_fixed_and_isotonic_versions_are_explicit_and_isotonic_parity_is_exact() -> None:
    claims = (_scored("a", 0.8, "one"), _scored("b", 0.8, "two"))
    fixed = aggregate_scored_claims(
        claims,
        query="",
        calibration_model_version="fixed-heuristic-v1",  # type: ignore[call-arg]
        calibration_table=(),
        resolved_direction="neutral",  # type: ignore[call-arg]
    )
    isotonic = aggregate_scored_claims(
        claims,
        query="",
        calibration_model_version="isotonic-v1",  # type: ignore[call-arg]
        calibration_table=((0.0, 0.1), (1.0, 0.9)),
        resolved_direction="neutral",  # type: ignore[call-arg]
    )
    assert fixed.confidence == 0.58
    assert isotonic.confidence == 0.564


def test_unknown_calibration_version_fails_closed() -> None:
    with pytest.raises(ValueError, match="calibration model version"):
        aggregate_scored_claims(
            (),
            query="",
            calibration_model_version="unknown-v1",  # type: ignore[call-arg]
            calibration_table=(),
            resolved_direction="neutral",  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    "trust",
    [-0.01, 1.01, float("nan"), True],
    ids=("negative", "above-one", "nan", "bool"),
)
def test_bad_scored_trust_fails_before_sort_or_mean(trust: object) -> None:
    item = _scored("bad", 0.5, "source")
    object.__setattr__(item, "trust", trust)
    with pytest.raises(ValueError, match="trust"):
        _current((item,))


def test_float_subclass_and_hostile_scalar_hooks_are_rejected_without_calls() -> None:
    calls = {name: 0 for name in ("float", "repr", "eq", "hash")}

    class BadFloat(float):
        def __float__(self) -> float:
            calls["float"] += 1
            raise AssertionError

        def __repr__(self) -> str:
            calls["repr"] += 1
            raise AssertionError

        def __eq__(self, other: object) -> bool:
            calls["eq"] += 1
            raise AssertionError

        def __hash__(self) -> int:
            calls["hash"] += 1
            raise AssertionError

    item = _scored("bad", 0.5, "source")
    object.__setattr__(item, "trust", BadFloat(0.5))
    with pytest.raises(ValueError, match="trust"):
        _current((item,))
    assert calls == {name: 0 for name in calls}


def test_strict_json_coin_metadata_never_coerces_hostile_values() -> None:
    calls = {name: 0 for name in ("str", "repr", "eq", "hash")}

    class Hostile:
        def __str__(self) -> str:
            calls["str"] += 1
            raise AssertionError

        def __repr__(self) -> str:
            calls["repr"] += 1
            raise AssertionError

        def __eq__(self, other: object) -> bool:
            calls["eq"] += 1
            raise AssertionError

        def __hash__(self) -> int:
            calls["hash"] += 1
            raise AssertionError

    item = _scored("bad-json", 0.8, "source")
    object.__setattr__(item.claim.document, "metadata", (("coin", Hostile()),))
    with pytest.raises(ValueError, match="metadata|JSON|coin"):
        _current((item,), coin="BTC")
    assert calls == {name: 0 for name in calls}


def test_fixed_table_constant_remains_the_legacy_literal() -> None:
    assert DEFAULT_CALIBRATION_TABLE == (
        (0.00, 0.00),
        (0.10, 0.03),
        (0.20, 0.08),
        (0.30, 0.20),
        (0.40, 0.40),
        (0.55, 0.55),
        (0.70, 0.70),
        (0.85, 0.85),
        (1.00, 1.00),
    )
    assert all(math.isfinite(x) and math.isfinite(y) for x, y in DEFAULT_CALIBRATION_TABLE)


def test_pre_truncation_pool_drives_calibration_but_output_uses_10_and_5() -> None:
    supporting = tuple(
        _scored(
            f"s{index:02}",
            0.8,
            f"src{index}",
            kind=("price", "onchain", "news")[index % 3],
        )
        for index in range(12)
    )
    contrarian = tuple(
        _scored(f"c{index:02}", 0.4, f"contra{index}", kind="social")
        for index in range(8)
    )
    output = _current(supporting + contrarian)
    assert output.trust_score == 0.8000000000000002
    assert output.confidence == 0.85
    assert tuple(item.claim.id for item in output.supporting) == tuple(
        f"s{index:02}" for index in range(10)
    )
    assert tuple(item.claim.id for item in output.contrarian) == tuple(
        f"c{index:02}" for index in range(5)
    )
    assert output.supporting_count == 10
    assert output.independent_sources == 10


@pytest.mark.parametrize(
    ("claims", "supporting", "contrarian", "raw", "calibrated"),
    [
        (
            (
                _scored("n", 0.7, "n", text="neutral outlook"),
                _scored("bull", 0.8, "bull", text="bull outlook", direction="bullish"),
                _scored("bear", 0.75, "bear", text="bear outlook", direction="bearish"),
                _scored("contra", 0.4, "contra", text="contradiction outlook"),
            ),
            ("bull", "bear", "n"),
            ("contra",),
            0.75,
            0.6125,
        ),
        (
            (
                _scored("good", 0.7, "good"),
                _scored("manip", 0.2, "x", kind="social", text="BTC pump shill"),
            ),
            ("good",),
            ("manip",),
            0.7,
            0.29,
        ),
    ],
    ids=("contradiction", "manipulation-low-trust"),
)
def test_contradiction_and_manipulation_goldens(
    claims: tuple[KernelScoredClaim, ...],
    supporting: tuple[str, ...],
    contrarian: tuple[str, ...],
    raw: float,
    calibrated: float,
) -> None:
    output = _current(claims)
    assert tuple(item.claim.id for item in output.supporting) == supporting
    assert tuple(item.claim.id for item in output.contrarian) == contrarian
    assert output.trust_score == raw
    assert output.confidence == calibrated


@pytest.mark.parametrize(
    "table",
    [
        ((0.0, 0.0), (0.0, 0.1)),
        ((0.0, 0.8), (1.0, 0.7)),
        ((0.0, 0.0), (float("nan"), 1.0)),
        ((0.0, 0.0), (1.0, float("inf"))),
        ((0.0, 0.0), (True, 1.0)),
    ],
    ids=("duplicate-x", "descending-y", "nan", "inf", "bool"),
)
def test_isotonic_invalid_tables_fail_closed(
    table: tuple[tuple[float, float], ...]
) -> None:
    with pytest.raises(ValueError, match="calibration"):
        aggregate_scored_claims(
            (),
            query="",
            calibration_model_version="isotonic-v1",  # type: ignore[call-arg]
            calibration_table=table,
            resolved_direction="neutral",  # type: ignore[call-arg]
        )


def test_fixed_version_rejects_caller_table() -> None:
    with pytest.raises(ValueError, match="fixed|calibration"):
        aggregate_scored_claims(
            (),
            query="",
            calibration_model_version="fixed-heuristic-v1",  # type: ignore[call-arg]
            calibration_table=((0.0, 0.0), (1.0, 1.0)),
            resolved_direction="neutral",  # type: ignore[call-arg]
        )


def test_calibration_table_subclasses_reject_without_hooks() -> None:
    calls = {name: 0 for name in ("float", "repr", "eq", "hash")}

    class BadFloat(float):
        def __float__(self) -> float:
            calls["float"] += 1
            raise AssertionError

        def __repr__(self) -> str:
            calls["repr"] += 1
            raise AssertionError

        def __eq__(self, other: object) -> bool:
            calls["eq"] += 1
            raise AssertionError

        def __hash__(self) -> int:
            calls["hash"] += 1
            raise AssertionError

    class BadTuple(tuple):
        pass

    tables = (
        (BadTuple((0.0, 0.0)), (1.0, 1.0)),
        ((BadFloat(0.0), 0.0), (1.0, 1.0)),
        ((0.0, BadFloat(0.0)), (1.0, 1.0)),
    )
    for table in tables:
        with pytest.raises(ValueError):
            _current((), calibration_table=table)  # type: ignore[arg-type]
    assert calls == {name: 0 for name in calls}


def test_success_output_is_strict_json_serializable() -> None:
    output = _current((_scored("a", 0.8, "one"), _scored("b", 0.8, "two")))
    encoded = json.dumps(asdict(output), allow_nan=False, sort_keys=True)
    assert '"trust_score": 0.8' in encoded


@pytest.mark.parametrize(
    "trust",
    [float("inf"), float("-inf"), 10**10000],
    ids=("positive-inf", "negative-inf", "huge-int"),
)
def test_more_non_probability_trust_values_fail_closed(trust: object) -> None:
    item = _scored("bad", 0.5, "source")
    object.__setattr__(item, "trust", trust)
    with pytest.raises(ValueError, match="trust"):
        _current((item,))


def test_int_and_float_trust_subclasses_fail_without_conversion() -> None:
    class BadInt(int):
        pass

    class BadFloat(float):
        pass

    for value in (BadInt(1), BadFloat(0.5)):
        item = _scored("bad", 0.5, "source")
        object.__setattr__(item, "trust", value)
        with pytest.raises(ValueError, match="trust"):
            _current((item,))


def test_explicit_coin_metadata_and_cross_coin_content() -> None:
    output = _current(
        (
            _scored("explicit", 0.7, "explicit", metadata=(("coin", "BTC"),)),
            _scored("cross", 0.95, "cross", text="BTC and ETH correlation"),
            _scored("other", 0.9, "other", metadata=(("coin", "ETH"),)),
            _scored("generic", 0.6, "generic"),
        ),
        coin="BTC",
    )
    assert tuple(item.claim.id for item in output.supporting) == (
        "explicit",
        "generic",
    )


def test_pure_core_aggregate_has_no_app_or_ambient_import_boundary() -> None:
    path = Path(__file__).parents[1] / "src" / "trustforge_core" / "scoring.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name.startswith("trustforge.") for name in imports)
    assert not imports & {
        "os",
        "pathlib",
        "boto3",
        "socket",
        "urllib",
        "requests",
    }
    aggregate_source = inspect.getsource(aggregate_scored_claims).casefold()
    for forbidden in ("provider", "getenv", "environ", "open(", "path(", "cache"):
        assert forbidden not in aggregate_source
