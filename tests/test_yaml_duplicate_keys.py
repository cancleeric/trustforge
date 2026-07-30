from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_yaml_duplicate_keys import DuplicateKeyError, load_unique_yaml


def test_duplicate_key_gate_rejects_nested_mapping(tmp_path: Path) -> None:
    source = tmp_path / "nested.yaml"
    source.write_text("root:\n  child:\n    repeated: one\n    repeated: two\n")

    with pytest.raises(DuplicateKeyError, match=r"nested\.yaml:4:5.*line 3"):
        load_unique_yaml(source)


def test_duplicate_key_gate_accepts_repeated_keys_in_different_mappings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "valid.yaml"
    source.write_text("left:\n  name: one\nright:\n  name: two\n")

    assert load_unique_yaml(source) == {
        "left": {"name": "one"},
        "right": {"name": "two"},
    }


def test_repository_openapi_has_unique_keys() -> None:
    root = Path(__file__).resolve().parents[1]
    document = load_unique_yaml(root / "docs" / "api" / "openapi.yaml")

    assert "/api/multi-angle" in document["paths"]


def test_duplicate_key_gate_accepts_cloudformation_intrinsic_tags(
    tmp_path: Path,
) -> None:
    source = tmp_path / "template.yaml"
    source.write_text(
        "value: !Ref Table\n"
        "choice: !If [Enabled, !Ref Current, !Ref Previous]\n"
    )

    assert load_unique_yaml(source) == {
        "value": "Table",
        "choice": ["Enabled", "Current", "Previous"],
    }


def test_duplicate_key_gate_rejects_duplicate_inside_tagged_mapping(
    tmp_path: Path,
) -> None:
    source = tmp_path / "tagged.yaml"
    source.write_text("value: !Custom\n  repeated: one\n  repeated: two\n")

    with pytest.raises(DuplicateKeyError, match=r"tagged\.yaml:3:3.*line 2"):
        load_unique_yaml(source)
