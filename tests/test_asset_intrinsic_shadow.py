from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from trustforge.asset_intrinsic import (
    AssetIntrinsicRepository,
    AssetIntrinsicView,
    IntrinsicDimension,
    IntrinsicDimensionName,
    IntrinsicFactStatus,
    parse_asset_intrinsic_record,
    IntrinsicProvenance,
    load_asset_intrinsic_records,
)
from trustforge.asset_intrinsic_shadow import (
    TOTAL_DELTA_CAP,
    _sanitized_query,
    _sanitized_url,
    assess_intrinsic_shadow,
    build_intrinsic_shadow_observation,
    normalized_source_family,
    validate_intrinsic_shadow_observation,
)
from trustforge.data_contracts import contract_schemas


FIXTURE = Path(__file__).parents[1] / "data" / "asset_intrinsic_records.json"
AS_OF = datetime(2026, 7, 28, tzinfo=timezone.utc)


def provenance(host: str) -> IntrinsicProvenance:
    return IntrinsicProvenance(
        source_urls=(f"https://{host}/source",),
        methodology="test methodology",
        content_hash="a" * 64,
        coverage="test coverage",
        evidence_path="data/asset_intrinsic_evidence/btc-issuance-v30.txt",
        source_revision="test-revision",
        evidence_kind="upstream_excerpt",
        source_coordinates="test coordinates",
    )


def dimension(
    name: IntrinsicDimensionName,
    value: float,
    host: str,
    *,
    valid_from: datetime = AS_OF,
) -> IntrinsicDimension:
    return IntrinsicDimension(
        name=name,
        status=IntrinsicFactStatus.KNOWN,
        value=value,
        as_of=valid_from,
        valid_from=valid_from,
        valid_until=None,
        fetched_at=valid_from,
        provenance=provenance(host),
    )


def view(*dimensions: IntrinsicDimension, asset_id: str = "asset:test") -> AssetIntrinsicView:
    return AssetIntrinsicView(asset_id=asset_id, as_of=AS_OF, dimensions=dimensions)


def conflicted_dimension(
    name: IntrinsicDimensionName,
    *,
    valid_from: datetime = AS_OF,
) -> IntrinsicDimension:
    return IntrinsicDimension(
        name=name,
        status=IntrinsicFactStatus.CONFLICTED,
        value=None,
        as_of=valid_from,
        valid_from=valid_from,
        valid_until=None,
        fetched_at=valid_from,
        provenance=IntrinsicProvenance(
            source_urls=(
                "https://a.example/source-a",
                "https://b.example/source-b",
            ),
            methodology="conflicting upstream observations",
            content_hash="a" * 64,
            coverage="conflicting coverage",
            evidence_path="data/asset_intrinsic_evidence/btc-issuance-v30.txt",
            source_revision="conflict-revision",
            evidence_kind="decision_record",
            source_coordinates="conflicting coordinates",
        ),
    )


def test_gate_passes_three_known_two_families_and_sum_equals_total() -> None:
    result = assess_intrinsic_shadow(
        view(
            dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example"),
            dimension(IntrinsicDimensionName.CONTROL_DISPERSION, 0.0, "b.example"),
            dimension(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, 1.0, "a.example"),
        )
    )

    assert result["gate"]["passed"] is True
    assert result["gate"]["known_count"] == 3
    assert result["gate"]["source_family_count"] == 2
    assert sum(item["signed_delta"] for item in result["dimensions"]) == result["total_delta"]
    assert abs(result["total_delta"]) <= TOTAL_DELTA_CAP
    assert result["affects_official_score"] is False


@pytest.mark.parametrize(
    "dimensions",
    [
        (
            dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example"),
            dimension(IntrinsicDimensionName.CONTROL_DISPERSION, 1.0, "b.example"),
        ),
        (
            dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example"),
            dimension(IntrinsicDimensionName.CONTROL_DISPERSION, 1.0, "a.example"),
            dimension(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, 1.0, "a.example"),
        ),
    ],
)
def test_coverage_gate_failure_zeros_every_dimension_and_total(dimensions) -> None:
    result = assess_intrinsic_shadow(view(*dimensions))

    assert result["gate"]["passed"] is False
    assert result["total_delta"] == 0.0
    assert all(item["signed_delta"] == 0.0 for item in result["dimensions"])


def test_input_order_and_asset_identity_do_not_change_contributions() -> None:
    dimensions = (
        dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example"),
        dimension(IntrinsicDimensionName.CONTROL_DISPERSION, 0.25, "b.example"),
        dimension(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, 0.75, "a.example"),
    )
    first = assess_intrinsic_shadow(view(*dimensions, asset_id="asset:first"))
    second = assess_intrinsic_shadow(view(*reversed(dimensions), asset_id="asset:anything"))

    assert first["dimensions"] == second["dimensions"]
    assert first["total_delta"] == second["total_delta"]


@pytest.mark.parametrize(
    "url",
    [
        "https://user@example.com/path",
        "https://user:pass@example.com/path",
        "https://example.com:443/path",
        "https://example.com:bad/path",
        "https://example%2ecom/path",
        "https://example com/path",
        "http://example.com/path",
    ],
)
def test_source_family_rejects_userinfo_port_and_non_https_tricks(url: str) -> None:
    with pytest.raises(ValueError):
        normalized_source_family(url)


def test_source_family_normalizes_case_and_trailing_dot() -> None:
    assert normalized_source_family("https://EXAMPLE.COM./path") == "example.com"


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_nonfinite_out_of_range_and_bool_fail_closed(invalid) -> None:
    item = dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example")
    object.__setattr__(item, "value", invalid)
    with pytest.raises(ValueError):
        assess_intrinsic_shadow(view(item))


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_five_extreme_dimensions_never_exceed_total_cap(value: float) -> None:
    items = tuple(
        dimension(
            name,
            value,
            "a.example" if index % 2 == 0 else "b.example",
        )
        for index, name in enumerate(IntrinsicDimensionName)
    )
    result = assess_intrinsic_shadow(view(*items))
    expected = -TOTAL_DELTA_CAP if value == 0.0 else TOTAL_DELTA_CAP
    assert result["total_delta"] == expected
    assert sum(item["signed_delta"] for item in result["dimensions"]) == expected


def test_future_known_fact_is_rendered_unknown_and_neutral() -> None:
    future = dimension(
        IntrinsicDimensionName.ISSUANCE_PREDICTABILITY,
        1.0,
        "a.example",
        valid_from=AS_OF + timedelta(days=1),
    )
    result = assess_intrinsic_shadow(view(future))
    issuance = result["dimensions"][0]

    assert issuance["status"] == "unknown"
    assert issuance["signed_delta"] == 0.0
    assert result["total_delta"] == 0.0


def test_real_bnb_is_honest_zero_without_control_governance_proof() -> None:
    repository = AssetIntrinsicRepository(load_asset_intrinsic_records(FIXTURE))
    pit = repository.pit_view("asset:bnb", AS_OF)
    assert pit is not None
    result = assess_intrinsic_shadow(pit)
    assert result["total_delta"] == 0.0
    assert result["gate"]["passed"] is False
    assert result["conflict_detected"] is False


def test_real_btc_control_and_governance_multi_host_produces_nonzero_shadow() -> None:
    repository = AssetIntrinsicRepository(load_asset_intrinsic_records(FIXTURE))
    pit = repository.pit_view("asset:btc", AS_OF)
    assert pit is not None
    result = assess_intrinsic_shadow(pit)
    # BTC now has four known dimensions (issuance, control, supply, governance)
    # spanning at least two source families, so the coverage gate is met.
    assert result["gate"]["passed"] is True
    assert result["gate"]["known_count"] >= 3
    assert result["gate"]["source_family_count"] >= 2
    assert result["total_delta"] != 0.0
    assert result["conflict_detected"] is False
    # Control and governance must not be scored as symbol/issuer branches; each
    # known dimension contributes the same (value - 0.5) * weight formula.
    control = next(d for d in result["dimensions"] if d["name"] == "control_dispersion")
    assert control["status"] == "known"
    assert abs(control["signed_delta"] - (control["normalized"] - 0.5) * control["weight"]) < 1e-9


# ---- #870 conflicted status, PIT replay determinism ----


def test_conflicted_dimension_contributes_zero_and_is_excluded_from_known_count() -> None:
    conflicted_view = view(
        dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example"),
        conflicted_dimension(IntrinsicDimensionName.CONTROL_DISPERSION),
        dimension(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, 1.0, "b.example"),
        dimension(IntrinsicDimensionName.GOVERNANCE_CAPTURE_RESISTANCE, 1.0, "a.example"),
    )
    result = assess_intrinsic_shadow(conflicted_view)

    control = next(d for d in result["dimensions"] if d["name"] == "control_dispersion")
    assert control["status"] == "conflicted"
    assert control["signed_delta"] == 0.0
    assert control["raw"] is None
    assert control["reason_code"] == "fact_conflicted"
    # A conflicted fact never counts toward the known-dimension gate.
    assert result["gate"]["known_count"] == 3
    assert result["gate"]["passed"] is True
    assert result["conflict_detected"] is True

    # The conflicted dimension adds nothing: the total equals the same view
    # with the conflicted fact omitted entirely.
    without_conflict = assess_intrinsic_shadow(
        view(
            dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example"),
            dimension(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, 1.0, "b.example"),
            dimension(IntrinsicDimensionName.GOVERNANCE_CAPTURE_RESISTANCE, 1.0, "a.example"),
        )
    )
    assert without_conflict["conflict_detected"] is False
    assert result["total_delta"] == without_conflict["total_delta"]


def test_pit_replay_is_deterministic_for_same_asset_and_as_of() -> None:
    repository = AssetIntrinsicRepository(load_asset_intrinsic_records(FIXTURE))
    as_of = datetime(2026, 7, 28, tzinfo=timezone.utc)

    first_view = repository.pit_view("asset:btc", as_of)
    second_view = repository.pit_view("asset:btc", as_of)
    assert first_view is not None
    assert second_view is not None
    assert [d.name for d in first_view.dimensions] == [d.name for d in second_view.dimensions]

    first_result = assess_intrinsic_shadow(first_view)
    second_result = assess_intrinsic_shadow(second_view)
    assert first_result == second_result


@pytest.mark.parametrize("target_name", tuple(IntrinsicDimensionName))
def test_conflicted_dimension_replays_through_canonical_pit_path(
    target_name: IntrinsicDimensionName,
) -> None:
    raw = copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))[0])
    raw["profile"]["asset_id"] = "asset:pit-conflicted"
    target = next(
        dimension
        for dimension in raw["profile"]["dimensions"]
        if dimension["name"] == target_name.value
    )
    target["status"] = "conflicted"
    target["value"] = None
    repository = AssetIntrinsicRepository([parse_asset_intrinsic_record(raw)])
    as_of = datetime(2026, 7, 28, tzinfo=timezone.utc)

    replay = repository.pit_view("asset:pit-conflicted", as_of)

    assert replay is not None
    assessment = assess_intrinsic_shadow(replay)
    conflicted = next(
        dimension
        for dimension in assessment["dimensions"]
        if dimension["name"] == target_name.value
    )
    assert conflicted["status"] == "conflicted"
    assert conflicted["reason_code"] == "fact_conflicted"
    assert conflicted["signed_delta"] == 0.0
    assert assessment["conflict_detected"] is True


def test_assessment_schema_accepts_output_and_report_field_remains_optional() -> None:
    result = assess_intrinsic_shadow(
        view(
            dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example"),
            dimension(IntrinsicDimensionName.CONTROL_DISPERSION, 0.0, "b.example"),
            dimension(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, 1.0, "a.example"),
        )
    )
    schemas = contract_schemas()
    Draft202012Validator(
        schemas["AssetIntrinsicAssessment"], format_checker=FormatChecker()
    ).validate(result)

    report_schema = schemas["Report"]
    assert "asset_intrinsic_assessment" not in report_schema["required"]
    assert "asset_intrinsic_assessment" in report_schema["properties"]


# ---- #873 issuance/supply tests ----


def test_known_issuance_and_supply_for_second_protocol_are_eligible() -> None:
    repository = AssetIntrinsicRepository(load_asset_intrinsic_records(FIXTURE))
    eth_valid_as_of = datetime(2026, 7, 30, tzinfo=timezone.utc)
    eth_view = repository.pit_view("asset:eth", eth_valid_as_of)

    assert eth_view is not None
    eth_dimensions = {d.name for d in eth_view.eligible_dimensions}
    assert IntrinsicDimensionName.ISSUANCE_PREDICTABILITY in eth_dimensions
    assert IntrinsicDimensionName.SUPPLY_VERIFIABILITY in eth_dimensions


def test_identical_pep_under_different_asset_id_produces_identical_results() -> None:
    dims = (
        dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "ethereum.org"),
        dimension(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, 1.0, "ethereum.org"),
        dimension(IntrinsicDimensionName.CONTROL_DISPERSION, 0.0, "consensus.ethereum.org"),
    )
    first = assess_intrinsic_shadow(view(*dims, asset_id="asset:eth"))
    second = assess_intrinsic_shadow(view(*dims, asset_id="asset:anything-else"))

    assert first["total_delta"] == second["total_delta"]
    for fd, sd in zip(first["dimensions"], second["dimensions"]):
        assert fd["signed_delta"] == sd["signed_delta"]


def test_stale_future_and_conflicted_issuance_supply_contribute_zero() -> None:
    repository = AssetIntrinsicRepository(load_asset_intrinsic_records(FIXTURE))
    as_of_before_eth = datetime(2026, 7, 28, tzinfo=timezone.utc)
    as_of_after_eth = datetime(2026, 7, 30, tzinfo=timezone.utc)

    # ETH record is valid_from 2026-07-29: as_of_before_eth → no ETH visible (future) → zero
    view_before = repository.pit_view("asset:eth", as_of_before_eth)
    assert view_before is None

    # ETH record is valid at 2026-07-30: has 2 known dimensions → gate fails → zero
    view_after = repository.pit_view("asset:eth", as_of_after_eth)
    assert view_after is not None
    result = assess_intrinsic_shadow(view_after)
    assert result["total_delta"] == 0.0

# ---------------------------------------------------------------------------
# Issue #871: provenance sanitization + intrinsic shadow observation builder.
# ---------------------------------------------------------------------------


def test_sanitized_url_strips_query_fragment_and_userinfo():
    assert _sanitized_url("https://a.example/path?token=secret") == "https://a.example/path"
    assert _sanitized_url("https://a.example/x#fragment") == "https://a.example/x"
    assert _sanitized_url("https://user:pw@a.example/y?z=1") == "https://a.example/y"


@pytest.mark.parametrize("hostile", ["", "not-a-url", "//nohost/x", None])
def test_sanitized_url_drops_unparseable_or_schemeless(hostile):
    assert _sanitized_url(hostile) == ""


def test_sanitized_query_is_stable_sha256_and_carries_no_plaintext():
    digest = _sanitized_query("super secret query")
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64
    assert _sanitized_query("super secret query") == digest
    assert "super secret query" not in digest


def test_build_intrinsic_shadow_observation_shape_and_sanitization():
    vista = view(
        dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example"),
        dimension(IntrinsicDimensionName.CONTROL_DISPERSION, 0.0, "b.example"),
        dimension(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, 1.0, "a.example"),
    )
    result = build_intrinsic_shadow_observation(
        vista, baseline_trust=0.40, candidate_trust=0.42, query="secret query",
    )
    assert result["mode"] == "shadow"
    assert result["affects_official_score"] is False
    assert result["asset_id"] == "asset:test"
    assert result["baseline_trust"] == 0.40
    assert result["candidate_trust"] == 0.42
    assert result["trust_delta"] == round(0.42 - 0.40, 8)
    assert result["facts_hash"].startswith("sha256:")
    assert result["query_hash"].startswith("sha256:")
    assert "secret query" not in json.dumps(result)
    for dimension_payload in result["dimensions"]:
        provenance = dimension_payload.get("provenance")
        if not isinstance(provenance, dict):
            continue
        for url in provenance["source_urls"]:
            assert "?" not in url and "#" not in url and "@" not in url


def test_stale_output_retains_original_known_coverage_gate_semantics():
    stale_at = AS_OF - timedelta(days=366)
    stale_view = view(
        dimension(
            IntrinsicDimensionName.ISSUANCE_PREDICTABILITY,
            1.0,
            "a.example",
            valid_from=stale_at,
        ),
        dimension(
            IntrinsicDimensionName.CONTROL_DISPERSION,
            0.0,
            "b.example",
            valid_from=stale_at,
        ),
        dimension(
            IntrinsicDimensionName.SUPPLY_VERIFIABILITY,
            1.0,
            "a.example",
            valid_from=stale_at,
        ),
    )
    result = build_intrinsic_shadow_observation(
        stale_view,
        baseline_trust=0.4,
        candidate_trust=0.4,
        query="stale regression",
    )
    assert result["gate"]["passed"] is True
    assert result["gate"]["known_count"] == 3
    assert result["gate"]["source_family_count"] == 2
    assert [item["status"] for item in result["dimensions"][:3]] == [
        "stale",
        "stale",
        "stale",
    ]
    assert result["total_delta"] == 0.0
    assert validate_intrinsic_shadow_observation(result) is result


def test_build_intrinsic_shadow_observation_fails_closed_on_nonfinite_trust():
    vista = view(dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example"))
    for hostile in (float("nan"), float("inf"), "x", True):
        with pytest.raises(ValueError):
            build_intrinsic_shadow_observation(
                vista, baseline_trust=hostile, candidate_trust=0.5, query="q",
            )
