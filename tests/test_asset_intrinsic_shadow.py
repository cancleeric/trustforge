from __future__ import annotations

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
    IntrinsicProvenance,
    load_asset_intrinsic_records,
)
from trustforge.asset_intrinsic_shadow import (
    TOTAL_DELTA_CAP,
    assess_intrinsic_shadow,
    normalized_source_family,
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


def test_real_btc_and_bnb_are_honest_zero() -> None:
    repository = AssetIntrinsicRepository(load_asset_intrinsic_records(FIXTURE))
    for asset_id in ("asset:btc", "asset:bnb"):
        pit = repository.pit_view(asset_id, AS_OF)
        assert pit is not None
        result = assess_intrinsic_shadow(pit)
        assert result["total_delta"] == 0.0
        assert result["gate"]["passed"] is False


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
