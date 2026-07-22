"""Adversarial contract-only coverage for kernel resolution input graphs."""

from __future__ import annotations

import pytest

import trustforge_core
import trustforge_core.contracts as contracts
import trustforge_core.scoring as scoring
from trustforge_core import (
    KernelClaim,
    KernelClaimResolution,
    KernelDocument,
    KernelInput,
    KernelReputationTrace,
    KernelRunResolution,
    canonical_source,
    validate_claim_resolution_graph,
    validate_claim_resolution_order,
    validate_kernel_input_graph,
    validate_reputation_trace_graph,
    validate_run_resolution_graph,
)


def _claim() -> KernelClaim:
    return KernelClaim(
        "claim", "text", KernelDocument("doc", "news", "wire", "text", 1.0)
    )


def _resolution() -> KernelRunResolution:
    return KernelRunResolution((KernelClaimResolution("claim", ("unknown-feed",)),))


def test_strict_json_integer_policy_has_one_canonical_definition() -> None:
    assert scoring.STRICT_JSON_MAX_INTEGER is contracts.STRICT_JSON_MAX_INTEGER
    assert trustforge_core.STRICT_JSON_MAX_INTEGER is contracts.STRICT_JSON_MAX_INTEGER


@pytest.mark.parametrize(
    "source", ("twitter", "sec", "sec edgar", "ß", "coindesk.com", "SEC.GOV")
)
def test_resolution_sources_must_already_equal_single_canonical_identity(
    source: str,
) -> None:
    assert canonical_source(source) != source
    with pytest.raises(ValueError, match="canonical"):
        KernelClaimResolution("claim", (source,))


def test_unknown_but_already_canonical_source_identity_is_accepted() -> None:
    assert canonical_source("unknown-feed") == "unknown-feed"
    value = KernelClaimResolution("claim", ("unknown-feed", "another-source"))
    assert value.independent_sources == ("unknown-feed", "another-source")


def test_source_exact_type_rejection_invokes_no_hostile_hooks() -> None:
    calls = {name: 0 for name in ("eq", "hash", "str", "repr")}

    class HostileStr(str):
        def __eq__(self, other: object) -> bool:
            calls["eq"] += 1
            raise AssertionError

        def __hash__(self) -> int:
            calls["hash"] += 1
            raise AssertionError

        def __str__(self) -> str:
            calls["str"] += 1
            raise AssertionError

        def __repr__(self) -> str:
            calls["repr"] += 1
            raise AssertionError

    with pytest.raises(ValueError):
        KernelClaimResolution("claim", (HostileStr("coindesk"),))
    assert calls == {name: 0 for name in calls}


@pytest.mark.parametrize(
    ("field", "bad"),
    (
        ("independent_sources", ("twitter",)),
        ("dynamic_reputation", 2.0),
        ("info_flags", ["bad"]),
        ("claim_id", ""),
    ),
)
def test_claim_resolution_validator_rejects_post_construction_tampering(
    field: str, bad: object
) -> None:
    value = KernelClaimResolution("claim", ("coindesk",))
    object.__setattr__(value, field, bad)
    with pytest.raises(ValueError):
        validate_claim_resolution_graph(value)


def test_reputation_trace_validator_rejects_nested_tampering_and_unsafe_count() -> None:
    trace = KernelReputationTrace("wire", 0.5, 0.5, 0, 0, 0)
    object.__setattr__(trace, "final", float("nan"))
    with pytest.raises(ValueError):
        validate_reputation_trace_graph(trace)
    with pytest.raises(ValueError):
        KernelReputationTrace("wire", 0.5, 0.5, 2**53, 0, 0)


@pytest.mark.parametrize(
    ("field", "bad"),
    (
        ("claim_resolutions", (object(),)),
        ("resolution_version", "9.0.0"),
        ("resolved_direction", object()),
        ("score_weights", (("src", 0.5),)),
        ("reputations", (("news", float("nan")),)),
        ("half_lives", (("news", 1.0),)),
        ("calibration_model_version", "unknown"),
        ("calibration_table", ((0.0, 0.9), (1.0, 0.1))),
    ),
)
def test_run_resolution_validator_rejects_post_construction_tampering(
    field: str, bad: object
) -> None:
    value = _resolution()
    object.__setattr__(value, field, bad)
    with pytest.raises(ValueError):
        validate_run_resolution_graph(value)


def test_kernel_input_validator_rechecks_claim_document_metadata_and_safe_ints() -> (
    None
):
    value = KernelInput((_claim(),), 1.0, "BTC", "q", resolution=_resolution())
    document = value.claims[0].document
    object.__setattr__(document, "metadata", (("unsafe", 2**53),))
    with pytest.raises(ValueError, match="JSON|integer"):
        validate_kernel_input_graph(value)


def test_order_validator_exact_type_gate_invokes_no_hostile_hooks() -> None:
    calls = {name: 0 for name in ("eq", "hash", "str", "repr", "float")}

    class Hostile:
        def __getattribute__(self, name: str) -> object:
            if name in calls:
                calls[name] += 1
            raise AssertionError

    with pytest.raises(ValueError, match="resolution"):
        validate_claim_resolution_order((_claim(),), Hostile())  # type: ignore[arg-type]
    assert calls == {name: 0 for name in calls}


def test_order_validator_revalidates_tampered_nested_ids_before_comparison() -> None:
    resolution = _resolution()
    nested = resolution.claim_resolutions[0]
    object.__setattr__(nested, "claim_id", object())
    with pytest.raises(ValueError, match="claim_id"):
        validate_claim_resolution_order((_claim(),), resolution)
