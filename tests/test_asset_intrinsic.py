from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from trustforge.asset_intrinsic import (
    AssetIntrinsicRepository,
    IntrinsicDimensionName,
    IntrinsicFactStatus,
    MAX_EVIDENCE_FILE_BYTES,
    MAX_RECORD_COUNT,
    MAX_RECORDS_FILE_BYTES,
    MAX_URL_LENGTH,
    load_asset_intrinsic_records,
    parse_asset_intrinsic_profile,
    parse_asset_intrinsic_record,
)
from trustforge.data_contracts import contract_schemas


FIXTURE = Path(__file__).parents[1] / "data" / "asset_intrinsic_records.json"


def utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def raw_records() -> list[dict]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, list)
    return value


def test_fixture_has_btc_and_bnb_and_eth_without_symbol_field_or_unsupported_claims() -> None:
    records = load_asset_intrinsic_records(FIXTURE)

    assert {record.profile.asset_id for record in records} == {"asset:btc", "asset:bnb", "asset:eth"}
    serialized = FIXTURE.read_text(encoding="utf-8").lower()
    assert "wall street" not in serialized
    assert "lost coin" not in serialized
    assert all("symbol" not in record.profile.to_dict() for record in records)


def test_profiles_contain_each_dimension_exactly_once() -> None:
    for record in load_asset_intrinsic_records(FIXTURE):
        assert {dimension.name for dimension in record.profile.dimensions} == set(
            IntrinsicDimensionName
        )


def test_unknown_is_null_and_never_eligible() -> None:
    records = load_asset_intrinsic_records(FIXTURE)
    for record in records:
        concentration = next(
            item
            for item in record.profile.dimensions
            if item.name is IntrinsicDimensionName.HOLDER_CONCENTRATION
        )
        assert concentration.status is IntrinsicFactStatus.UNKNOWN
        assert concentration.value is None
        assert not concentration.eligible_at(utc(2026, 8, 1))


@pytest.mark.parametrize("status", ["unknown", "stale", "conflicted"])
def test_non_known_numeric_value_fails_closed(status: str) -> None:
    payload = copy.deepcopy(raw_records()[0]["profile"])
    payload["dimensions"][4]["status"] = status
    payload["dimensions"][4]["value"] = 0.5

    with pytest.raises(ValueError, match="non-known dimension.value must be null"):
        parse_asset_intrinsic_profile(payload)


def test_known_requires_https_provenance_and_finite_bounded_value() -> None:
    payload = copy.deepcopy(raw_records()[0]["profile"])
    dimension = payload["dimensions"][0]
    dimension["provenance"]["source_urls"] = []
    with pytest.raises(ValueError, match="at least one source URL"):
        parse_asset_intrinsic_profile(payload)

    for invalid in (-0.01, 1.01, float("inf"), float("nan"), "1"):
        payload = copy.deepcopy(raw_records()[0]["profile"])
        payload["dimensions"][0]["value"] = invalid
        with pytest.raises(ValueError, match="known dimension.value"):
            parse_asset_intrinsic_profile(payload)


def test_strict_profile_dimension_provenance_and_record_keys() -> None:
    base = raw_records()[0]
    mutations = [
        (("profile",), "schema_version", "missing profile fields"),
        (("profile",), "extra", "unexpected profile fields"),
        (("profile", "dimensions", 0), "status", "missing dimension fields"),
        (("profile", "dimensions", 0), "extra", "unexpected dimension fields"),
        (
            ("profile", "dimensions", 0, "provenance"),
            "coverage",
            "missing provenance fields",
        ),
        (
            ("profile", "dimensions", 0, "provenance"),
            "extra",
            "unexpected provenance fields",
        ),
        ((), "fetched_at", "missing record fields"),
        ((), "extra", "unexpected record fields"),
    ]
    for path, key, expected in mutations:
        payload = copy.deepcopy(base)
        target = payload
        for part in path:
            target = target[part]
        if key == "extra":
            target[key] = True
        else:
            del target[key]
        with pytest.raises(ValueError, match=expected):
            parse_asset_intrinsic_record(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("valid_from",), "2026-01-01T00:00:00"),
        (("fetched_at",), "2026-01-01T00:00:00"),
        (("profile", "dimensions", 0, "as_of"), "2026-01-01T00:00:00"),
        (("profile", "dimensions", 0, "valid_from"), "not-a-date"),
        (("profile", "dimensions", 0, "fetched_at"), 123),
    ],
)
def test_naive_malformed_and_non_string_timestamps_fail_closed(
    path: tuple[object, ...], value: object
) -> None:
    payload = copy.deepcopy(raw_records()[0])
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValueError, match="ISO timestamp|timezone-aware"):
        parse_asset_intrinsic_record(payload)


def test_repository_excludes_future_record_validity_and_fetch() -> None:
    old = copy.deepcopy(raw_records()[0])
    future_valid = copy.deepcopy(old)
    future_valid["profile"]["asset_id"] = "asset:future-valid"
    future_valid["valid_from"] = "2027-01-01T00:00:00Z"
    future_fetch = copy.deepcopy(old)
    future_fetch["profile"]["asset_id"] = "asset:future-fetch"
    future_fetch["fetched_at"] = "2027-01-01T00:00:00Z"
    repository = AssetIntrinsicRepository(
        parse_asset_intrinsic_record(item) for item in (future_valid, future_fetch)
    )

    assert repository.lookup("asset:future-valid", utc(2026, 8, 1)) is None
    assert repository.lookup("asset:future-fetch", utc(2026, 8, 1)) is None


def test_repository_selection_is_deterministic_for_multiple_versions() -> None:
    older = copy.deepcopy(raw_records()[0])
    older["profile"]["asset_id"] = "asset:test"
    older["valid_from"] = "2026-01-01T00:00:00Z"
    older["fetched_at"] = "2026-01-02T00:00:00Z"
    newer = copy.deepcopy(older)
    newer["valid_from"] = "2026-06-01T00:00:00Z"
    newer["fetched_at"] = "2026-06-02T00:00:00Z"
    repository = AssetIntrinsicRepository(
        reversed([parse_asset_intrinsic_record(older), parse_asset_intrinsic_record(newer)])
    )

    selected = repository.lookup("asset:test", utc(2026, 8, 1))
    assert selected is not None
    assert selected.valid_from == utc(2026, 6, 1)


def test_repository_rejects_ambiguous_duplicate_identity() -> None:
    record = parse_asset_intrinsic_record(raw_records()[0])
    with pytest.raises(ValueError, match="ambiguous duplicate"):
        AssetIntrinsicRepository([record, record])


def test_pit_view_surfaces_conflicted_but_omits_stale_expired_and_future_dimensions() -> None:
    raw = copy.deepcopy(raw_records()[0])
    raw["profile"]["asset_id"] = "asset:test"
    dimensions = raw["profile"]["dimensions"]
    dimensions[0]["status"] = "stale"
    dimensions[0]["value"] = None
    dimensions[1]["status"] = "conflicted"
    dimensions[1]["value"] = None
    dimensions[2]["valid_until"] = "2026-07-28T00:00:00Z"
    dimensions[3]["as_of"] = "2026-08-02T00:00:00Z"
    dimensions[3]["fetched_at"] = "2026-08-02T00:00:00Z"
    repository = AssetIntrinsicRepository([parse_asset_intrinsic_record(raw)])

    view = repository.pit_view("asset:test", utc(2026, 8, 1))

    assert view is not None
    assert [dimension.name for dimension in view.dimensions] == [
        IntrinsicDimensionName.CONTROL_DISPERSION,
        IntrinsicDimensionName.HOLDER_CONCENTRATION
    ]
    assert view.eligible_dimensions == ()


def test_repository_rejects_naive_as_of() -> None:
    repository = AssetIntrinsicRepository(load_asset_intrinsic_records(FIXTURE))
    with pytest.raises(ValueError, match="timezone-aware"):
        repository.lookup("asset:btc", datetime(2026, 8, 1))
    with pytest.raises(ValueError, match="timezone-aware"):
        repository.pit_view("asset:btc", datetime(2026, 8, 1))


def test_contract_schema_is_versioned_strict_and_conditional() -> None:
    schema = contract_schemas()["AssetIntrinsicProfile"]

    assert schema["properties"]["schema_version"]["const"] == "1.0.0"
    assert schema["additionalProperties"] is False
    dimension = schema["properties"]["dimensions"]["items"]
    assert dimension["additionalProperties"] is False
    assert dimension["properties"]["provenance"]["additionalProperties"] is False
    assert dimension["allOf"][0]["else"]["properties"]["value"] == {"type": "null"}


def test_jsonschema_accepts_fixtures_with_draft_202012_format_checker() -> None:
    validator = Draft202012Validator(
        contract_schemas()["AssetIntrinsicProfile"], format_checker=FormatChecker()
    )
    for record in raw_records():
        validator.validate(record["profile"])


@pytest.mark.parametrize(
    ("mutator", "expected_path"),
    [
        (lambda profile: profile.pop("asset_id"), []),
        (lambda profile: profile.__setitem__("extra", True), []),
        (lambda profile: profile.__setitem__("asset_id", 1), ["asset_id"]),
        (
            lambda profile: profile["dimensions"][0].__setitem__(
                "as_of", "2026-07-27T00:00:00"
            ),
            ["dimensions", 0, "as_of"],
        ),
        (
            lambda profile: profile["dimensions"][0].__setitem__("value", "1"),
            ["dimensions", 0],
        ),
    ],
)
def test_jsonschema_rejects_missing_extra_type_and_naive_timestamp(
    mutator, expected_path: list[object]
) -> None:
    profile = copy.deepcopy(raw_records()[0]["profile"])
    mutator(profile)
    validator = Draft202012Validator(
        contract_schemas()["AssetIntrinsicProfile"], format_checker=FormatChecker()
    )
    errors = list(validator.iter_errors(profile))
    assert errors
    assert any(list(error.absolute_path)[: len(expected_path)] == expected_path for error in errors)


def test_jsonschema_rejects_duplicate_and_therefore_missing_dimension_name() -> None:
    profile = copy.deepcopy(raw_records()[0]["profile"])
    profile["dimensions"][4]["name"] = profile["dimensions"][0]["name"]
    validator = Draft202012Validator(
        contract_schemas()["AssetIntrinsicProfile"], format_checker=FormatChecker()
    )

    errors = list(validator.iter_errors(profile))

    assert errors
    assert any(error.validator in {"contains", "maxContains"} for error in errors)
    with pytest.raises(ValueError, match="each intrinsic dimension exactly once"):
        parse_asset_intrinsic_profile(profile)


def test_evidence_fingerprint_tamper_fails_closed(tmp_path: Path) -> None:
    evidence_root = tmp_path / "repo"
    evidence_dir = evidence_root / "data" / "asset_intrinsic_evidence"
    evidence_dir.mkdir(parents=True)
    payload = copy.deepcopy(raw_records()[0])
    for dimension in payload["profile"]["dimensions"]:
        source = Path(__file__).parents[1] / dimension["provenance"]["evidence_path"]
        target = evidence_root / dimension["provenance"]["evidence_path"]
        target.write_bytes(source.read_bytes())
    records_file = evidence_root / "data" / "records.json"
    records_file.write_text(json.dumps([payload]), encoding="utf-8")

    first_path = evidence_root / payload["profile"]["dimensions"][0]["provenance"]["evidence_path"]
    first_path.write_bytes(first_path.read_bytes() + b"tamper")

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        load_asset_intrinsic_records(records_file, evidence_root=evidence_root)


def test_known_btc_evidence_is_exact_pinned_upstream_bytes() -> None:
    records = load_asset_intrinsic_records(FIXTURE)
    btc = next(record for record in records if record.profile.asset_id == "asset:btc")
    known = [dimension for dimension in btc.profile.dimensions if dimension.status.value == "known"]

    assert len(known) == 4
    for dimension in known:
        provenance = dimension.provenance
        assert provenance.evidence_kind == "upstream_excerpt"
        assert "d0f6d9953a15d7c7111d46dcb76ab2bb18e5dee3" in provenance.source_revision
        assert "lines " in provenance.source_coordinates
    # Issuance and supply are pinned single-revision excerpts of Bitcoin Core.
    pinned = [
        dimension
        for dimension in known
        if dimension.name
        in (IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, IntrinsicDimensionName.SUPPLY_VERIFIABILITY)
    ]
    assert len(pinned) == 2
    for dimension in pinned:
        assert all(
            "d0f6d9953a15d7c7111d46dcb76ab2bb18e5dee3" in url
            for url in dimension.provenance.source_urls
        )
    issuance = Path(__file__).parents[1] / pinned[0].provenance.evidence_path
    assert issuance.read_text(encoding="utf-8").startswith("CAmount GetBlockSubsidy(")
    assert "observation:" not in issuance.read_text(encoding="utf-8")
    # Control and governance span at least two independent source hosts so that
    # documentation statements are never the sole entity-control proof.
    multi_host = [
        dimension
        for dimension in known
        if dimension.name
        in (IntrinsicDimensionName.CONTROL_DISPERSION, IntrinsicDimensionName.GOVERNANCE_CAPTURE_RESISTANCE)
    ]
    assert len(multi_host) == 2
    for dimension in multi_host:
        families = {
            url.split("://", 1)[1].split("/", 1)[0]
            for url in dimension.provenance.source_urls
        }
        assert len(families) >= 2
        evidence_path = Path(__file__).parents[1] / dimension.provenance.evidence_path
        assert "observation:" not in evidence_path.read_text(encoding="utf-8")


def test_loader_rejects_oversized_records_before_json_decode(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"[" + b" " * MAX_RECORDS_FILE_BYTES + b"]")

    with pytest.raises(ValueError, match="records file exceeds maximum size"):
        load_asset_intrinsic_records(oversized, evidence_root=tmp_path)


def test_loader_rejects_excessive_record_count_before_item_parse(tmp_path: Path) -> None:
    excessive = tmp_path / "excessive.json"
    excessive.write_text(json.dumps([{}] * (MAX_RECORD_COUNT + 1)), encoding="utf-8")

    with pytest.raises(ValueError, match="record count exceeds maximum"):
        load_asset_intrinsic_records(excessive, evidence_root=tmp_path)


def test_loader_rejects_oversized_evidence_before_read(tmp_path: Path) -> None:
    evidence_root = tmp_path / "repo"
    evidence_dir = evidence_root / "data" / "asset_intrinsic_evidence"
    evidence_dir.mkdir(parents=True)
    payload = copy.deepcopy(raw_records()[0])
    for dimension in payload["profile"]["dimensions"]:
        source = Path(__file__).parents[1] / dimension["provenance"]["evidence_path"]
        target = evidence_root / dimension["provenance"]["evidence_path"]
        target.write_bytes(source.read_bytes())
    oversized_path = evidence_root / payload["profile"]["dimensions"][0]["provenance"]["evidence_path"]
    oversized_path.write_bytes(b"x" * (MAX_EVIDENCE_FILE_BYTES + 1))
    records_file = evidence_root / "data" / "records.json"
    records_file.write_text(json.dumps([payload]), encoding="utf-8")

    with pytest.raises(ValueError, match="evidence file exceeds maximum size"):
        load_asset_intrinsic_records(records_file, evidence_root=evidence_root)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("asset_id", "a" * 257, "asset_id exceeds maximum length"),
        ("source_url", "https://" + "a" * MAX_URL_LENGTH, "source URL exceeds maximum length"),
        (
            "evidence_path",
            "data/asset_intrinsic_evidence/nested/evidence.txt",
            "safe path under",
        ),
    ],
)
def test_runtime_rejects_critical_oversized_string_and_nested_path(
    field: str, value: str, expected: str
) -> None:
    profile = copy.deepcopy(raw_records()[0]["profile"])
    if field == "asset_id":
        profile["asset_id"] = value
    elif field == "source_url":
        profile["dimensions"][0]["provenance"]["source_urls"] = [value]
    else:
        profile["dimensions"][0]["provenance"]["evidence_path"] = value

    with pytest.raises(ValueError, match=expected):
        parse_asset_intrinsic_profile(profile)


def test_jsonschema_rejects_nested_evidence_path_like_runtime() -> None:
    profile = copy.deepcopy(raw_records()[0]["profile"])
    profile["dimensions"][0]["provenance"]["evidence_path"] = (
        "data/asset_intrinsic_evidence/nested/evidence.txt"
    )
    validator = Draft202012Validator(
        contract_schemas()["AssetIntrinsicProfile"], format_checker=FormatChecker()
    )
    assert list(validator.iter_errors(profile))


@pytest.mark.parametrize("missing_field", ["evidence_kind", "source_coordinates"])
def test_schema_and_runtime_both_reject_missing_provenance_field(
    missing_field: str,
) -> None:
    profile = copy.deepcopy(raw_records()[0]["profile"])
    del profile["dimensions"][0]["provenance"][missing_field]
    validator = Draft202012Validator(
        contract_schemas()["AssetIntrinsicProfile"], format_checker=FormatChecker()
    )

    schema_errors = list(validator.iter_errors(profile))

    assert schema_errors
    assert any(
        error.validator == "required" and missing_field in error.message
        for error in schema_errors
    )
    with pytest.raises(ValueError, match=rf"missing provenance fields: {missing_field}"):
        parse_asset_intrinsic_profile(profile)


def test_validation_cli_success_is_offline_and_error_exit_is_nonzero(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    command = [
        sys.executable,
        str(root / "scripts" / "validate_asset_intrinsic_records.py"),
        str(FIXTURE),
        "--as-of",
        "2026-07-27T00:00:00Z",
    ]
    success = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, check=False
    )
    assert success.returncode == 0
    assert json.loads(success.stdout)["network_used"] is False

    bad_file = tmp_path / "bad.json"
    bad_file.write_text('[{"unexpected": true}]', encoding="utf-8")
    failure = subprocess.run(
        [sys.executable, command[1], str(bad_file)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failure.returncode == 2
    assert "validation failed" in failure.stderr


def test_validation_cli_rejects_record_with_empty_pit_dimension_view(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    isolated_root = tmp_path / "repo"
    evidence_dir = isolated_root / "data" / "asset_intrinsic_evidence"
    evidence_dir.mkdir(parents=True)
    payload = copy.deepcopy(raw_records()[0])
    for dimension in payload["profile"]["dimensions"]:
        source = root / dimension["provenance"]["evidence_path"]
        target = isolated_root / dimension["provenance"]["evidence_path"]
        target.write_bytes(source.read_bytes())
        dimension["status"] = "stale"
        dimension["value"] = None
    records_file = isolated_root / "data" / "records.json"
    records_file.write_text(json.dumps([payload]), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "validate_asset_intrinsic_records.py"),
            str(records_file),
            "--as-of",
            "2026-07-27T00:00:00Z",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "no dimensions are PIT-visible" in result.stderr


def test_asset_context_contract_and_fixture_remain_unchanged() -> None:
    schema = contract_schemas()["AssetContext"]
    assert schema["properties"]["schema_version"]["const"] == "1.0.0"
    assert (Path(__file__).parents[1] / "data" / "asset_context_records.json").exists()


def test_load_rejects_non_array_or_non_object(tmp_path: Path) -> None:
    object_file = tmp_path / "object.json"
    object_file.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an array"):
        load_asset_intrinsic_records(object_file)

    scalar_file = tmp_path / "scalar.json"
    scalar_file.write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        load_asset_intrinsic_records(scalar_file)


# ---- PEP & Builder tests (issue #873) ----

PEP_ETH_DIR = Path(__file__).parents[1] / "data" / "asset_intrinsic_evidence" / "pep" / "asset:eth"
BUILDER_SCRIPT = Path(__file__).parents[1] / "scripts" / "build_issuance_supply_records.py"


def test_pep_manifest_schema_is_versioned_and_reject_unknown_protocol_family(tmp_path: Path) -> None:
    bad_manifest = {
        "manifest_version": "1.0.0",
        "asset_id": "asset:test",
        "protocol_family": "unknown_protocol_xyz",
        "source_revision": "test:v1",
        "source_urls": ["https://example.com/source"],
        "source_coordinates": "test coords",
        "evidence_files": {"test.txt": "a" * 64},
        "methodology": "test",
        "coverage": "test",
        "valid_from": "2026-07-29T00:00:00Z",
        "valid_until": None,
        "dimensions": {},
    }
    pep_dir = tmp_path / "asset:test"
    pep_dir.mkdir()
    manifest_path = pep_dir / "manifest.json"
    manifest_path.write_text(json.dumps(bad_manifest), encoding="utf-8")
    (pep_dir / "evidence").mkdir()
    (pep_dir / "evidence" / "test.txt").write_text("test", encoding="utf-8")

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, str(BUILDER_SCRIPT), str(pep_dir)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unknown protocol_family" in result.stderr


def test_builder_rejects_tampered_evidence_and_wrong_hash(tmp_path: Path) -> None:
    manifest = json.loads((PEP_ETH_DIR / "manifest.json").read_text())
    pep_dir = tmp_path / "asset:eth"
    pep_dir.mkdir()
    (pep_dir / "evidence").mkdir()
    (pep_dir / "evidence" / "eth-issuance-pos.txt").write_text("tampered content")
    manifest["evidence_files"]["eth-issuance-pos.txt"] = "a" * 64
    (pep_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, str(BUILDER_SCRIPT), str(pep_dir)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "fingerprint mismatch" in result.stderr


def test_builder_emits_exact_record_keys_and_sorted_json() -> None:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, str(BUILDER_SCRIPT), str(PEP_ETH_DIR)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    profile = json.loads(result.stdout)
    # Top-level keys must be sorted
    assert list(profile.keys()) == ["asset_id", "dimensions", "schema_version"]
    # Each dimension must have exact expected keys in sorted order
    expected_dim_keys = [
        "as_of", "fetched_at", "name", "provenance",
        "status", "valid_from", "valid_until", "value",
    ]
    for dim in profile["dimensions"]:
        assert list(dim.keys()) == expected_dim_keys
    # schema_version is fixed
    assert profile["schema_version"] == "1.0.0"


def test_second_protocol_fixture_is_offline_and_hash_verified() -> None:
    records = load_asset_intrinsic_records(FIXTURE)
    eth = next(record for record in records if record.profile.asset_id == "asset:eth")
    known = [dimension for dimension in eth.profile.dimensions if dimension.status.value == "known"]

    assert len(known) == 2
    for dimension in known:
        provenance = dimension.provenance
        assert provenance.evidence_kind == "upstream_excerpt"
        assert "ethereum" in provenance.source_revision.lower()
        assert all("https://" in url for url in provenance.source_urls)
        # Verify evidence file hash matches
        evidence_path = Path(__file__).parents[1] / provenance.evidence_path
        assert evidence_path.is_file()
        import hashlib
        actual_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        assert actual_hash == provenance.content_hash


def test_second_protocol_is_different_family_from_btc() -> None:
    records = load_asset_intrinsic_records(FIXTURE)
    btc = next(record for record in records if record.profile.asset_id == "asset:btc")
    eth = next(record for record in records if record.profile.asset_id == "asset:eth")

    # BTC evidence is from bitcoin/bitcoin (pow source code)
    # ETH evidence is from ethereum/consensus-specs (pos consensus spec)
    btc_source = btc.profile.dimensions[0].provenance.source_revision
    eth_source = eth.profile.dimensions[0].provenance.source_revision

    assert "bitcoin" in btc_source.lower() or "btc" in btc_source.lower()
    assert "ethereum" in eth_source.lower() or "eth" in eth_source.lower()
    assert btc_source != eth_source
