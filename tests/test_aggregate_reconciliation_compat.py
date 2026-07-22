"""Compatibility and full-graph regressions for #452 Commit C."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from trustforge_core import (
    DEFAULT_CALIBRATION_TABLE,
    FIXED_HEURISTIC_VERSION,
    ISOTONIC_VERSION,
    KernelClaim,
    KernelDocument,
    KernelReputationTrace,
    KernelScoredClaim,
    SUPPORTED_CALIBRATION_MODEL_VERSIONS,
    aggregate_scored_claims,
)


def _item(
    claim_id: str,
    trust: float,
    source: str,
    *,
    text: str = "generic market update",
    direction: str = "neutral",
) -> KernelScoredClaim:
    document = KernelDocument(claim_id, "news", source, text, 1.0)
    return KernelScoredClaim(
        KernelClaim(claim_id, text, document, "fact", direction), trust
    )


def test_domain_stop_query_token_remains_a_legacy_relevance_match() -> None:
    output = aggregate_scored_claims(
        (
            _item("btc", 0.6, "one", text="BTC signal"),
            _item("other", 0.9, "two", text="unrelated signal"),
        ),
        query="BTC",
    )
    assert tuple(item.claim.id for item in output.supporting) == ("btc",)


def test_omitted_direction_preserves_legacy_inference_but_explicit_is_passthrough() -> None:
    claims = tuple(
        _item(f"b{index}", 0.8, f"source-{index}", direction="bullish")
        for index in range(3)
    )
    compatibility = aggregate_scored_claims(claims, query="")
    explicit = aggregate_scored_claims(
        claims, query="", resolved_direction="outer-resolved"
    )
    assert compatibility.direction == "偏多"
    assert explicit.direction == "outer-resolved"


def test_calibration_provenance_compatibility_and_explicit_policy() -> None:
    claims = (_item("a", 0.8, "one"), _item("b", 0.8, "two"))
    legacy = aggregate_scored_claims(
        claims, query="", calibration_table=DEFAULT_CALIBRATION_TABLE
    )
    fixed_omitted = aggregate_scored_claims(
        claims, query="", calibration_model_version=FIXED_HEURISTIC_VERSION
    )
    fixed_default = aggregate_scored_claims(
        claims,
        query="",
        calibration_model_version=FIXED_HEURISTIC_VERSION,
        calibration_table=DEFAULT_CALIBRATION_TABLE,
    )
    isotonic = aggregate_scored_claims(
        claims,
        query="",
        calibration_model_version=ISOTONIC_VERSION,
        calibration_table=((0.0, 0.1), (1.0, 0.9)),
    )
    assert legacy.confidence == fixed_omitted.confidence == fixed_default.confidence == 0.58
    assert isotonic.confidence == 0.564
    with pytest.raises(ValueError, match="version is required"):
        aggregate_scored_claims(
            claims,
            query="",
            calibration_table=((0.0, 0.1), (1.0, 0.9)),
        )


def test_public_calibration_versions_are_exported() -> None:
    assert SUPPORTED_CALIBRATION_MODEL_VERSIONS == frozenset(
        {FIXED_HEURISTIC_VERSION, ISOTONIC_VERSION}
    )


def test_complete_graph_is_validated_before_empty_query_or_coin_branch() -> None:
    calls = {name: 0 for name in ("str", "repr", "float", "hash", "eq")}

    class Hostile:
        def __str__(self) -> str:
            calls["str"] += 1
            raise AssertionError

        def __repr__(self) -> str:
            calls["repr"] += 1
            raise AssertionError

        def __float__(self) -> float:
            calls["float"] += 1
            raise AssertionError

        def __hash__(self) -> int:
            calls["hash"] += 1
            raise AssertionError

        def __eq__(self, other: object) -> bool:
            calls["eq"] += 1
            raise AssertionError

    item = _item("bad", 0.8, "one")
    object.__setattr__(item.claim, "text", Hostile())
    with pytest.raises(ValueError, match="claim.text"):
        aggregate_scored_claims((item,), query="", coin="")
    assert calls == {name: 0 for name in calls}


@pytest.mark.parametrize(
    "field",
    ("id", "kind", "source", "text", "url"),
)
def test_document_string_graph_fields_are_exact(field: str) -> None:
    item = _item("bad", 0.8, "one")
    object.__setattr__(item.claim.document, field, object())
    with pytest.raises(ValueError, match=field):
        aggregate_scored_claims((item,), query="")


@pytest.mark.parametrize(
    "timestamp",
    (True, float("nan"), float("inf"), object()),
    ids=("bool", "nan", "inf", "object"),
)
def test_document_timestamp_graph_field_is_exact_finite(timestamp: object) -> None:
    item = _item("bad", 0.8, "one")
    object.__setattr__(item.claim.document, "timestamp", timestamp)
    with pytest.raises(ValueError, match="timestamp"):
        aggregate_scored_claims((item,), query="")


def test_recursive_metadata_is_strict_json_and_success_output_never_has_nan() -> None:
    item = _item("good", 0.8, "one")
    object.__setattr__(
        item.claim.document,
        "metadata",
        (("nested", (None, True, 7, 0.5, "value")),),
    )
    output = aggregate_scored_claims((item,), query="")
    json.dumps(asdict(output), allow_nan=False)

    bad = _item("bad", 0.8, "two")
    object.__setattr__(bad.claim.document, "metadata", (("nested", (float("nan"),)),))
    with pytest.raises(ValueError, match="finite JSON"):
        aggregate_scored_claims((bad,), query="")


def _traced_item() -> KernelScoredClaim:
    item = _item("traced", 0.8, "one")
    trace = KernelReputationTrace("one", 0.6, 0.7, 1, 0, 2, "entailment")
    object.__setattr__(item, "reputation_trace", trace)
    return item


@pytest.mark.parametrize("mode", ("evil", "", 7), ids=("evil", "empty", "non-string"))
def test_tampered_reputation_trace_mode_fails_closed(mode: object) -> None:
    item = _traced_item()
    object.__setattr__(item.reputation_trace, "mode", mode)
    with pytest.raises(ValueError, match="reputation_trace.mode"):
        aggregate_scored_claims((item,), query="")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("agree_n", -1),
        ("contradict_n", True),
        ("iterations_run", 1 << 54),
    ),
    ids=("negative", "bool", "huge"),
)
def test_tampered_reputation_trace_counts_fail_closed(
    field: str, value: object
) -> None:
    item = _traced_item()
    object.__setattr__(item.reputation_trace, field, value)
    with pytest.raises(ValueError, match=field):
        aggregate_scored_claims((item,), query="")


@pytest.mark.parametrize(
    ("field", "value"),
    (("prior", float("nan")), ("final", float("inf")), ("prior", True)),
    ids=("prior-nan", "final-inf", "prior-bool"),
)
def test_tampered_reputation_trace_scores_require_exact_finite_numbers(
    field: str, value: object
) -> None:
    item = _traced_item()
    object.__setattr__(item.reputation_trace, field, value)
    with pytest.raises(ValueError, match=field):
        aggregate_scored_claims((item,), query="")


def test_tampered_trace_subclass_and_hostile_hooks_are_never_called() -> None:
    calls = {name: 0 for name in ("int", "repr", "eq", "hash")}

    class BadInt(int):
        def __int__(self) -> int:
            calls["int"] += 1
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

    item = _traced_item()
    object.__setattr__(item.reputation_trace, "agree_n", BadInt(1))
    with pytest.raises(ValueError, match="agree_n"):
        aggregate_scored_claims((item,), query="")
    assert calls == {name: 0 for name in calls}


def test_valid_reputation_trace_output_is_strict_json() -> None:
    output = aggregate_scored_claims((_traced_item(),), query="")
    json.dumps(asdict(output), allow_nan=False)
