"""Expected-red resolved-input oracle for #453.

The future contract is intentionally referenced dynamically so the green
resolution-absent compatibility file can still collect and run independently.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import trustforge_core
from trustforge_core import (
    DEFAULT_HALF_LIVES,
    DEFAULT_SCORE_WEIGHTS,
    DEFAULT_SOURCE_REPUTATIONS,
    KernelClaim,
    KernelDocument,
    KernelReputationTrace,
)


KERNEL_V2_2 = "2.2.0"
RESOLUTION_V1 = "1.0.0"


def _future(name: str) -> Any:
    value = getattr(trustforge_core, name, None)
    assert value is not None, f"missing future public symbol: {name}"
    return value


def _claim(
    claim_id: str,
    *,
    source: str | None = None,
    kind: str = "price",
    text: str = "BTC signal",
    direction: str = "neutral",
) -> KernelClaim:
    document = KernelDocument(claim_id, kind, source or claim_id, text, 100.0)
    return KernelClaim(claim_id, text, document, "fact", direction)


def _claim_resolution(
    claim_id: str,
    *,
    independent_sources: tuple[str, ...] = (),
    dynamic_reputation: float | None = None,
    reputation_trace: KernelReputationTrace | None = None,
    info_flags: tuple[str, ...] = (),
):
    return _future("KernelClaimResolution")(
        claim_id,
        independent_sources,
        dynamic_reputation,
        reputation_trace,
        info_flags,
    )


def _run_resolution(
    claim_resolutions: tuple[Any, ...],
    *,
    score_weights: tuple[tuple[str, float], ...] = DEFAULT_SCORE_WEIGHTS,
    reputations: tuple[tuple[str, float], ...] = DEFAULT_SOURCE_REPUTATIONS,
    half_lives: tuple[tuple[str, float], ...] = DEFAULT_HALF_LIVES,
    calibration_model_version: str = "fixed-heuristic-v1",
    calibration_table: tuple[tuple[float, float], ...] = (),
    resolved_direction: str = "neutral",
    resolution_version: str = RESOLUTION_V1,
):
    return _future("KernelRunResolution")(
        claim_resolutions=claim_resolutions,
        score_weights=score_weights,
        reputations=reputations,
        half_lives=half_lives,
        calibration_model_version=calibration_model_version,
        calibration_table=calibration_table,
        resolved_direction=resolved_direction,
        resolution_version=resolution_version,
    )


def _input(claims: tuple[KernelClaim, ...], resolution: Any):
    return _future("KernelInput")(
        claims,
        100.0,
        "BTC",
        "BTC",
        KERNEL_V2_2,
        resolution,
    )


def _run(claims: tuple[KernelClaim, ...], resolution: Any):
    return _future("run_kernel")(_input(claims, resolution))


def test_contract_versions_and_append_only_input_shape() -> None:
    assert trustforge_core.KERNEL_CONTRACT_VERSION == KERNEL_V2_2
    assert _future("KERNEL_RESOLUTION_VERSION") == RESOLUTION_V1
    fields = tuple(inspect.signature(_future("KernelInput")).parameters)
    assert fields[:5] == (
        "claims",
        "pit_epoch",
        "coin",
        "query",
        "contract_version",
    )
    assert fields[5] == "resolution"


@pytest.mark.parametrize("source_count", (0, 1, 2))
def test_resolved_independent_source_vectors(source_count: int) -> None:
    claims = (_claim("a"),)
    sources = tuple(f"source-{index}" for index in range(source_count))
    output = _run(
        claims,
        _run_resolution(
            (_claim_resolution("a", independent_sources=sources),),
            resolved_direction="outer-neutral",
        ),
    )
    expected_corroboration = (0.0, 0.5, 0.75)[source_count]
    components = dict(output.scored_claims[0].components)
    assert components["corroboration"] == expected_corroboration
    assert output.direction == "outer-neutral"


def test_already_canonical_unique_sources_drive_corroboration() -> None:
    resolution = _claim_resolution(
        "a",
        independent_sources=("coindesk", "reuters"),
    )
    assert resolution.independent_sources == ("coindesk", "reuters")
    output = _run((_claim("a"),), _run_resolution((resolution,)))
    assert dict(output.scored_claims[0].components)["corroboration"] == 0.75


def test_contradiction_neutral_and_manipulation_vectors() -> None:
    claims = (
        _claim("neutral", source="n", text="BTC neutral"),
        _claim("contra", source="c", text="BTC contradiction", direction="bearish"),
        _claim("manip", source="m", kind="social", text="BTC pump shill", direction="bullish"),
    )
    resolutions = tuple(
        _claim_resolution(claim.id, independent_sources=("external",))
        for claim in claims
    )
    output = _run(
        claims,
        _run_resolution(resolutions, resolved_direction="bearish"),
    )
    assert output.direction == "bearish"
    assert output.scored_claims[0].manip_flags == ()
    assert output.scored_claims[1].claim.direction == "bearish"
    assert output.scored_claims[2].manip_flags == ("shill", "pump")
    assert output.scored_claims[2].trust < output.scored_claims[0].trust


def test_dynamic_reputation_trace_and_info_flags_are_forwarded_exactly() -> None:
    trace = KernelReputationTrace("source", 0.65, 0.9, 2, 0, 3, "entailment")
    resolution = _claim_resolution(
        "a",
        dynamic_reputation=0.9,
        reputation_trace=trace,
        info_flags=("resolved-info",),
    )
    output = _run((_claim("a", kind="news"),), _run_resolution((resolution,)))
    scored = output.scored_claims[0]
    assert dict(scored.components)["reputation"] == 0.9
    assert scored.reputation_trace is trace
    assert scored.info_flags == ("resolved-info",)


def test_fixed_isotonic_and_direction_are_resolved_values() -> None:
    claims = (_claim("a"), _claim("b"))
    resolutions = (_claim_resolution("a"), _claim_resolution("b"))
    fixed = _run(
        claims,
        _run_resolution(resolutions, resolved_direction="fixed-direction"),
    )
    isotonic = _run(
        claims,
        _run_resolution(
            resolutions,
            calibration_model_version="isotonic-v1",
            calibration_table=((0.0, 0.1), (1.0, 0.9)),
            resolved_direction="isotonic-direction",
        ),
    )
    assert fixed.direction == "fixed-direction"
    assert isotonic.direction == "isotonic-direction"
    assert fixed.confidence != isotonic.confidence


@pytest.mark.parametrize(
    "case",
    ("missing", "extra", "duplicate", "out-of-order"),
)
def test_resolution_set_must_match_claims_exactly_in_order(
    case: str,
) -> None:
    claim_resolutions = {
        "missing": (),
        "extra": (_claim_resolution("a"), _claim_resolution("extra")),
        "duplicate": (_claim_resolution("a"), _claim_resolution("a")),
        "out-of-order": (_claim_resolution("b"), _claim_resolution("a")),
    }[case]
    with pytest.raises(ValueError, match="resolution|claim"):
        _run((_claim("a"), _claim("b")), _run_resolution(claim_resolutions))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("resolution_version", "0.0.0"),
        ("calibration_model_version", "unknown"),
        ("resolved_direction", None),
        ("calibration_table", ((0.0, 0.0), (float("nan"), 1.0))),
        ("calibration_table", ((0.0, 0.0), (1.0, float("inf")))),
    ),
    ids=("version", "model-version", "direction-type", "nan", "inf"),
)
def test_malformed_run_resolution_fails_closed(field: str, value: object) -> None:
    arguments = {field: value}
    with pytest.raises(ValueError):
        _run_resolution((_claim_resolution("a"),), **arguments)


def test_malformed_claim_resolution_numbers_and_subclasses_fail_closed() -> None:
    class BadFloat(float):
        pass

    for value in (float("nan"), float("inf"), 10**10000, True, BadFloat(0.5)):
        with pytest.raises(ValueError):
            _claim_resolution("a", dynamic_reputation=value)  # type: ignore[arg-type]


def test_hostile_resolution_values_invoke_zero_hooks() -> None:
    calls = {name: 0 for name in ("float", "repr", "eq", "hash")}

    class Hostile:
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

    with pytest.raises(ValueError):
        _claim_resolution("a", dynamic_reputation=Hostile())  # type: ignore[arg-type]
    assert calls == {name: 0 for name in calls}


def test_validation_happens_before_scoring_or_aggregation(monkeypatch) -> None:
    scoring = __import__("trustforge_core.scoring", fromlist=["scoring"])
    calls = {"score": 0, "aggregate": 0}

    def counted_score(*args: object, **kwargs: object):
        calls["score"] += 1
        raise AssertionError

    def counted_aggregate(*args: object, **kwargs: object):
        calls["aggregate"] += 1
        raise AssertionError

    monkeypatch.setattr(scoring, "score_claim", counted_score)
    monkeypatch.setattr(scoring, "aggregate_scored_claims", counted_aggregate)
    bad = _run_resolution((_claim_resolution("wrong"),))
    with pytest.raises(ValueError):
        _run((_claim("a"),), bad)
    assert calls == {"score": 0, "aggregate": 0}


def test_score_once_per_claim_and_aggregate_once(monkeypatch) -> None:
    scoring = __import__("trustforge_core.scoring", fromlist=["scoring"])
    real_score = scoring.score_claim
    real_aggregate = scoring.aggregate_scored_claims
    calls = {"score": 0, "aggregate": 0}

    def counted_score(*args: object, **kwargs: object):
        calls["score"] += 1
        return real_score(*args, **kwargs)

    def counted_aggregate(*args: object, **kwargs: object):
        calls["aggregate"] += 1
        return real_aggregate(*args, **kwargs)

    monkeypatch.setattr(scoring, "score_claim", counted_score)
    monkeypatch.setattr(scoring, "aggregate_scored_claims", counted_aggregate)
    claims = (_claim("a"), _claim("b"))
    _run(
        claims,
        _run_resolution((_claim_resolution("a"), _claim_resolution("b"))),
    )
    assert calls == {"score": 2, "aggregate": 1}


def test_core_resolution_path_has_no_provider_or_io_surface() -> None:
    root = Path(__file__).parents[1] / "src" / "trustforge_core"
    for path in (root / "contracts.py", root / "scoring.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not any(name.startswith("trustforge.") for name in imports)
        assert not imports & {"os", "pathlib", "boto3", "socket", "urllib", "requests"}


def test_scoring_policy_tables_are_forwarded_exactly_once(monkeypatch) -> None:
    scoring = __import__("trustforge_core.scoring", fromlist=["scoring"])
    real_score = scoring.score_claim
    captured: list[dict[str, object]] = []
    weights = (("src", 0.4), ("corr", 0.3), ("rec", 0.2), ("manip", 0.1))
    reputations = (("price", 0.7),)
    half_lives = (("default", 24.0),)

    def spy(claim: object, **kwargs: object):
        captured.append(kwargs)
        return real_score(claim, **kwargs)

    monkeypatch.setattr(scoring, "score_claim", spy)
    resolution = _run_resolution(
        (_claim_resolution("a"),),
        score_weights=weights,
        reputations=reputations,
        half_lives=half_lives,
    )
    _run((_claim("a"),), resolution)
    assert len(captured) == 1
    assert captured[0]["weights"] is weights
    assert captured[0]["reputations"] is reputations
    assert captured[0]["half_lives"] is half_lives


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("score_weights", (("src", 0.5),)),
        (
            "score_weights",
            (("src", 0.5), ("src", 0.5), ("corr", 0.0), ("rec", 0.0), ("manip", 0.0)),
        ),
        ("reputations", (("price", float("nan")),)),
        ("reputations", (("price", True),)),
        ("half_lives", (("price", 12.0),)),
        ("half_lives", (("default", float("inf")),)),
    ),
    ids=(
        "weights-missing",
        "weights-duplicate",
        "reputation-nan",
        "reputation-bool",
        "half-life-missing-default",
        "half-life-inf",
    ),
)
def test_malformed_scoring_policy_tables_fail_closed(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _run_resolution((_claim_resolution("a"),), **{field: value})


def test_policy_subclasses_and_hostile_hooks_are_rejected_without_calls() -> None:
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

    with pytest.raises(ValueError):
        _run_resolution(
            (_claim_resolution("a"),), reputations=(("price", BadFloat(0.7)),)
        )
    assert calls == {name: 0 for name in calls}


def test_default_2_2_compatibility_and_explicit_old_or_unknown_contracts_fail() -> None:
    KernelInput = _future("KernelInput")
    current = KernelInput((_claim("a"),), 100.0, "BTC", "BTC")
    assert current.contract_version == KERNEL_V2_2
    assert current.resolution is None
    with pytest.raises(ValueError, match="contract version"):
        KernelInput((), 100.0, "BTC", "BTC", "2.1.0")
    with pytest.raises(ValueError, match="contract version"):
        KernelInput((), 100.0, "BTC", "BTC", "99.0.0")


@pytest.mark.parametrize("name", ("KernelClaimResolution", "KernelRunResolution"))
def test_resolution_dtos_are_frozen_slotted_sealed_and_strict_json(name: str) -> None:
    claim_resolution = _claim_resolution("a", independent_sources=("coindesk",))
    value = claim_resolution if name == "KernelClaimResolution" else _run_resolution((claim_resolution,))
    assert not hasattr(value, "__dict__")
    field = "claim_id" if name == "KernelClaimResolution" else "resolved_direction"
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        setattr(value, field, "changed")
    with pytest.raises(TypeError):
        type("Evil", (type(value),), {})
    json.dumps(dataclasses.asdict(value), allow_nan=False, sort_keys=True)


def test_dynamic_reputation_must_equal_trace_final() -> None:
    trace = KernelReputationTrace("one", 0.6, 0.8, 1, 0, 1, "entailment")
    valid = _claim_resolution("a", dynamic_reputation=0.8, reputation_trace=trace)
    assert valid.dynamic_reputation == valid.reputation_trace.final
    with pytest.raises(ValueError, match="dynamic_reputation|trace"):
        _claim_resolution("a", dynamic_reputation=0.7, reputation_trace=trace)


@pytest.mark.parametrize(
    "sources",
    (
        ("",),
        (7,),
        (" CoinDesk ",),
        ("coindesk.com",),
        ("coindesk", "coindesk"),
        ("coindesk", "coindesk.com"),
    ),
    ids=(
        "empty",
        "non-string",
        "noncanonical",
        "alias",
        "duplicate",
        "alias-duplicate",
    ),
)
def test_independent_sources_reject_ambiguous_inputs(sources: tuple[object, ...]) -> None:
    # Outer adapters own canonicalization.  Core accepts only final canonical
    # identities so it never silently merges or reorders ambiguous input.
    with pytest.raises(ValueError, match="independent_sources"):
        _claim_resolution("a", independent_sources=sources)  # type: ignore[arg-type]


def test_canonical_sources_are_unique_and_preserve_first_seen_order() -> None:
    resolution = _claim_resolution(
        "a", independent_sources=("reuters", "coindesk", "sec-gov")
    )
    assert resolution.independent_sources == ("reuters", "coindesk", "sec-gov")


def test_independent_source_subclasses_invoke_zero_hooks() -> None:
    calls = {name: 0 for name in ("repr", "eq", "hash")}

    class BadStr(str):
        def __repr__(self) -> str:
            calls["repr"] += 1
            raise AssertionError

        def __eq__(self, other: object) -> bool:
            calls["eq"] += 1
            raise AssertionError

        def __hash__(self) -> int:
            calls["hash"] += 1
            raise AssertionError

    with pytest.raises(ValueError):
        _claim_resolution("a", independent_sources=(BadStr("coindesk"),))
    assert calls == {name: 0 for name in calls}


def test_resolution_tuples_and_nested_dto_graph_are_exact() -> None:
    class BadTuple(tuple):
        pass

    with pytest.raises(ValueError):
        _run_resolution(BadTuple((_claim_resolution("a"),)))
    valid = _run_resolution((_claim_resolution("a"),))
    object.__setattr__(valid, "claim_resolutions", (object(),))
    with pytest.raises(ValueError, match="claim_resolutions|resolution"):
        _run((_claim("a"),), valid)


def test_resolved_run_is_deterministic_across_python_hash_seeds() -> None:
    source_root = Path(__file__).parents[1] / "src"
    script = """
import dataclasses, json
from trustforge_core import (
    KernelClaim, KernelClaimResolution, KernelDocument, KernelInput,
    KernelRunResolution, run_kernel,
)
claims = tuple(
    KernelClaim(str(i), "BTC signal", KernelDocument(str(i), "price", source, "BTC signal", 100.0), "fact", "bullish")
    for i, source in enumerate(("reuters", "coindesk", "sec-gov"))
)
resolutions = tuple(
    KernelClaimResolution(claim.id, ("reuters", "coindesk", "sec-gov"), None, None, ())
    for claim in claims
)
resolution = KernelRunResolution(claim_resolutions=resolutions, resolved_direction="bullish")
output = run_kernel(KernelInput(claims, 100.0, "BTC", "BTC", "2.2.0", resolution))
print(json.dumps(dataclasses.asdict(output), sort_keys=True, ensure_ascii=False, allow_nan=False))
"""
    outputs: list[str] = []
    for seed in ("1", "987654"):
        environment = dict(os.environ)
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = str(source_root)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
