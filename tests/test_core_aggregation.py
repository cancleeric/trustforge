"""Direct tests for the provider-free pure aggregation boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from trustforge_core import (
    KERNEL_CONTRACT_VERSION,
    KernelClaim,
    KernelDocument,
    KernelScoredClaim,
    UnsupportedKernelContractVersion,
    aggregate_scored_claims,
)
from trustforge_core.aggregation import evidence_strength


def _scored(
    claim_id: str,
    trust: float,
    source: str,
    *,
    kind: str = "news",
    text: str = "generic market update",
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
    return KernelScoredClaim(KernelClaim(claim_id, text, document), trust)


def _aggregate(
    claims: tuple[KernelScoredClaim, ...],
    **overrides: object,
):
    arguments = {
        "scored_claims": claims,
        "query": "",
        "coin": "",
        "support_threshold": 0.5,
        "contract_version": KERNEL_CONTRACT_VERSION,
        "calibration_model_version": "fixed-heuristic-v1",
        "calibration_table": (),
        "resolved_direction": "neutral",
    }
    arguments.update(overrides)
    return aggregate_scored_claims(**arguments)  # type: ignore[arg-type]


def test_direct_fixed_parity_and_direction_pass_through() -> None:
    result = _aggregate(
        (_scored("a", 0.8, "one"), _scored("b", 0.8, "two")),
        resolved_direction="outer-resolved",
    )

    assert result.trust_score == 0.8
    assert result.confidence == 0.58
    assert result.direction == "outer-resolved"
    assert result.abstain is False
    assert result.decision_state == "normal"
    assert result.reason_codes == ()
    assert tuple(item.claim.id for item in result.supporting) == ("a", "b")


def test_canonical_aliases_are_one_independent_source() -> None:
    result = _aggregate(
        (_scored("a", 0.8, "CoinDesk"), _scored("b", 0.8, " coindesk "))
    )
    assert result.independent_sources == 1
    assert result.reason_codes == ("insufficient_independent_sources",)


def test_support_threshold_is_inclusive_at_exact_half() -> None:
    result = _aggregate(
        (_scored("at", 0.5, "a"), _scored("below", 0.499, "b"))
    )
    assert tuple(item.claim.id for item in result.supporting) == ("at",)
    assert tuple(item.claim.id for item in result.contrarian) == ("below",)


def test_source_flood_truncates_but_does_not_inflate_independence() -> None:
    result = _aggregate(tuple(_scored(f"s{i:02}", 0.8, "same") for i in range(12)))
    assert result.supporting_count == 10
    assert result.independent_sources == 1
    assert result.abstain is True


@pytest.mark.parametrize(
    ("claims", "expected_reasons"),
    [
        ((), ("low_calibrated_confidence", "insufficient_independent_sources")),
        (
            (_scored("a", 0.8, "one"),),
            ("insufficient_independent_sources",),
        ),
    ],
)
def test_abstain_reason_codes_are_independent(
    claims: tuple[KernelScoredClaim, ...], expected_reasons: tuple[str, ...]
) -> None:
    result = _aggregate(claims)
    assert result.abstain is True
    assert result.reason_codes == expected_reasons


def test_low_confidence_reason_is_not_an_abstain_reason() -> None:
    result = _aggregate(
        (_scored("a", 0.5, "a"), _scored("b", 0.5, "b")),
    )
    assert result.confidence == 0.475
    assert result.abstain is False
    assert result.decision_state == "low_confidence"
    assert result.reason_codes == ("below_normal_confidence",)


def test_isotonic_table_accepts_interior_endpoints_and_clamps() -> None:
    table = ((0.2, 0.1), (0.8, 0.9))
    low = _aggregate((), calibration_model_version="isotonic-v1", calibration_table=table)
    high = _aggregate(
        (
            _scored("a", 1.0, "a", kind="price"),
            _scored("b", 1.0, "b", kind="onchain"),
            _scored("c", 1.0, "c", kind="news"),
            _scored("d", 1.0, "d", kind="social"),
        ),
        calibration_model_version="isotonic-v1",
        calibration_table=table,
    )
    assert low.confidence == 0.1
    assert high.confidence == 0.9


def test_parsed_isotonic_parity_is_exact() -> None:
    result = _aggregate(
        (_scored("a", 0.8, "one"), _scored("b", 0.8, "two")),
        calibration_model_version="isotonic-v1",
        calibration_table=((0.0, 0.1), (1.0, 0.9)),
    )
    assert result.confidence == 0.564


@pytest.mark.parametrize(
    ("confidence", "state", "reasons"),
    [
        (0.35, "low_confidence", ("below_normal_confidence",)),
        (0.50, "normal", ()),
    ],
)
def test_decision_boundaries_are_inclusive(
    confidence: float, state: str, reasons: tuple[str, ...]
) -> None:
    result = _aggregate(
        (_scored("a", 0.8, "one"), _scored("b", 0.8, "two")),
        calibration_model_version="isotonic-v1",
        calibration_table=((0.0, confidence), (1.0, confidence)),
    )
    assert result.confidence == confidence
    assert result.abstain is False
    assert result.decision_state == state
    assert result.reason_codes == reasons


@pytest.mark.parametrize(
    "table",
    [
        (),
        ((0.0, 0.0),),
        ((0.5, 0.1), (0.5, 0.2)),
        ((0.2, 0.7), (0.8, 0.6)),
        ((-0.1, 0.0), (0.8, 1.0)),
        ((0.1, 0.0), (0.8, float("nan"))),
    ],
)
def test_isotonic_table_validation(table: tuple[tuple[float, float], ...]) -> None:
    with pytest.raises(ValueError):
        _aggregate((), calibration_model_version="isotonic-v1", calibration_table=table)


def test_coin_scope_keeps_specific_then_generic_and_excludes_other_coin() -> None:
    result = _aggregate(
        (
            _scored("generic", 0.9, "generic"),
            _scored("btc", 0.7, "btc", text="BTC ETF inflow"),
            _scored("eth", 1.0, "eth", text="ETH staking"),
            _scored("cross", 0.95, "cross", text="BTC and ETH correlation"),
        ),
        coin="BTC",
    )
    assert tuple(item.claim.id for item in result.supporting) == ("btc", "generic")
    assert result.independent_sources == 2


def test_query_fallback_and_stable_equal_trust_order() -> None:
    claims = (
        _scored("first", 0.7, "a"),
        _scored("second", 0.7, "b"),
        _scored("third", 0.7, "c"),
    )
    result = _aggregate(claims, query="unmatched query")
    assert tuple(item.claim.id for item in result.supporting) == (
        "first",
        "second",
        "third",
    )


def test_pre_truncation_calibration_but_post_truncation_decision_count() -> None:
    supporting = tuple(
        _scored(f"s{index:02}", 0.8, f"source-{index}", kind=("price", "onchain", "news")[index % 3])
        for index in range(12)
    )
    contrarian = tuple(_scored(f"c{index:02}", 0.4, f"contra-{index}") for index in range(8))
    result = _aggregate(supporting + contrarian)
    assert result.confidence == 0.85
    assert result.supporting_count == 10
    assert result.independent_sources == 10
    assert len(result.contrarian) == 5


def test_public_evidence_strength_direct_legacy_parity() -> None:
    supporting = (_scored("a", 0.8, "one"), _scored("b", 0.8, "two"))
    assert evidence_strength(
        supporting=supporting, contrarian=(), trust_score=0.8
    ) == 0.58


def test_public_evidence_strength_exact_boundary_and_hooks() -> None:
    calls = {name: 0 for name in ("hash", "eq", "repr", "float")}

    class Hostile:
        def __hash__(self) -> int:
            calls["hash"] += 1
            raise AssertionError

        def __eq__(self, other: object) -> bool:
            calls["eq"] += 1
            raise AssertionError

        def __repr__(self) -> str:
            calls["repr"] += 1
            raise AssertionError

        def __float__(self) -> float:
            calls["float"] += 1
            raise AssertionError

    class BadTuple(tuple):
        pass

    item = _scored("item", 0.8, "one")
    for arguments in (
        {"supporting": BadTuple((item,)), "contrarian": (), "trust_score": 0.8},
        {"supporting": (), "contrarian": BadTuple((item,)), "trust_score": 0.8},
        {"supporting": (Hostile(),), "contrarian": (), "trust_score": 0.8},
        {"supporting": (), "contrarian": (), "trust_score": Hostile()},
    ):
        with pytest.raises(ValueError):
            evidence_strength(**arguments)  # type: ignore[arg-type]
    assert calls == {name: 0 for name in calls}


def test_public_evidence_strength_validates_nested_trust_before_sets() -> None:
    item = _scored("bad", 0.5, "source")
    object.__setattr__(item, "trust", 1.01)
    with pytest.raises(ValueError, match=r"supporting\[0\]\.trust"):
        evidence_strength(supporting=(item,), contrarian=(), trust_score=0.5)


def test_exact_containers_reject_hostile_subclasses() -> None:
    class HostileTuple(tuple):
        pass

    with pytest.raises(ValueError, match="exact tuple"):
        _aggregate(HostileTuple())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exact tuple"):
        _aggregate((), calibration_model_version="isotonic-v1", calibration_table=HostileTuple(((0.0, 0.0), (1.0, 1.0))))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "trust", [-0.01, 1.01, True, 10**10000], ids=("negative", "above-one", "bool", "huge-int")
)
def test_scored_trust_is_validated_before_aggregation(trust: object) -> None:
    item = _scored("bad", 0.5, "source")
    object.__setattr__(item, "trust", trust)
    with pytest.raises(ValueError, match=r"scored_claims\[0\]\.trust"):
        _aggregate((item,))


def test_hostile_scalars_and_points_are_rejected_without_hooks() -> None:
    calls = {name: 0 for name in ("hash", "eq", "repr", "float")}

    class Hostile:
        def __hash__(self) -> int:
            calls["hash"] += 1
            raise AssertionError

        def __eq__(self, other: object) -> bool:
            calls["eq"] += 1
            raise AssertionError

        def __repr__(self) -> str:
            calls["repr"] += 1
            raise AssertionError

        def __float__(self) -> float:
            calls["float"] += 1
            raise AssertionError

    hostile = Hostile()
    for field in ("query", "coin", "resolved_direction", "calibration_model_version"):
        with pytest.raises(ValueError):
            _aggregate((), **{field: hostile})
    with pytest.raises(ValueError):
        _aggregate((), support_threshold=hostile)
    with pytest.raises(ValueError):
        _aggregate(
            (),
            calibration_model_version="isotonic-v1",
            calibration_table=((hostile, 0.0), (1.0, 1.0)),
        )
    with pytest.raises(ValueError):
        _aggregate(
            (),
            calibration_model_version="isotonic-v1",
            calibration_table=((0.0, hostile), (1.0, 1.0)),
        )
    assert calls == {name: 0 for name in calls}


def test_scalar_and_inner_tuple_subclasses_are_rejected() -> None:
    class BadStr(str):
        pass

    class BadFloat(float):
        pass

    class BadTuple(tuple):
        pass

    for field in ("query", "coin", "resolved_direction", "calibration_model_version"):
        with pytest.raises(ValueError):
            _aggregate((), **{field: BadStr("bad")})
    with pytest.raises(UnsupportedKernelContractVersion):
        _aggregate((), contract_version=BadStr(KERNEL_CONTRACT_VERSION))
    with pytest.raises(ValueError):
        _aggregate((), support_threshold=BadFloat(0.5))
    item = _scored("bad-subclass", 0.5, "source")
    object.__setattr__(item, "trust", BadFloat(0.5))
    with pytest.raises(ValueError, match=r"scored_claims\[0\]\.trust"):
        _aggregate((item,))
    with pytest.raises(ValueError):
        _aggregate(
            (),
            calibration_model_version="isotonic-v1",
            calibration_table=(BadTuple((0.0, 0.0)), (1.0, 1.0)),
        )
    for point in (((BadFloat(0.0), 0.0), (1.0, 1.0)), ((0.0, BadFloat(0.0)), (1.0, 1.0))):
        with pytest.raises(ValueError):
            _aggregate(
                (), calibration_model_version="isotonic-v1", calibration_table=point
            )


def test_module_has_no_app_provider_environment_or_io_imports() -> None:
    module_path = Path(__file__).parents[1] / "src" / "trustforge_core" / "aggregation.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name.startswith("trustforge.") for name in imports)
    assert not imports & {"os", "pathlib", "boto3", "urllib", "requests"}
    source = module_path.read_text(encoding="utf-8").casefold()
    assert "provider" not in source
