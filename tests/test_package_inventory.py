from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_package_inventory", ROOT / "scripts" / "build_package_inventory.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_repository_inventory_is_complete_and_deterministic() -> None:
    manifest = MODULE.load_manifest(ROOT / "qa" / "package-ownership.json")

    first = MODULE.build_inventory(ROOT, manifest)
    second = MODULE.build_inventory(ROOT, manifest)

    assert first == second
    assert len(first["entries"]) > 1_000
    assert all(count > 0 for count in first["counts"].values())
    paths = {entry["path"] for entry in first["entries"]}
    assert "src/trustforge_core/aggregation.py" in paths
    assert "src/trustforge/web.py" in paths
    assert "tests/test_web.py" in paths
    assert sum(first["disposition_counts"].values()) == len(first["entries"])
    assert first["mode"] == "inventory_shadow_only"
    assert first["selection_ready"] is False
    dispositions = {entry["path"]: entry["disposition"] for entry in first["entries"]}
    assert dispositions["frontend/package.json"] == "global_trigger"
    assert dispositions["frontend/package-lock.json"] == "global_trigger"
    assert dispositions["native/nf2-zero-capability-broker/Cargo.toml"] == "global_trigger"
    assert dispositions["tests/test_web.py"] == "owned"


def test_dependency_graph_rejects_cycles() -> None:
    packages = {
        "core": {"depends_on": ["web"]},
        "web": {"depends_on": ["core"]},
    }

    with pytest.raises(MODULE.InventoryError, match="dependency cycle"):
        MODULE.validate_dependency_graph(packages)


def test_unknown_path_fails_closed() -> None:
    rules = [MODULE.Rule(owner="core", priority=1, glob="src/core/**")]

    with pytest.raises(MODULE.InventoryError, match="unclassified path"):
        MODULE.classify("unexpected/new_module.py", rules)


def test_equal_priority_overlap_fails_closed() -> None:
    rules = [
        MODULE.Rule(owner="core", priority=10, glob="src/**/*.py"),
        MODULE.Rule(owner="web", priority=10, glob="src/trustforge/*.py"),
    ]

    with pytest.raises(MODULE.InventoryError, match="multiply-owned path"):
        MODULE.classify("src/trustforge/web.py", rules)


def test_higher_priority_rule_refines_fallback_owner() -> None:
    rules = [
        MODULE.Rule(owner="web", priority=10, glob="src/trustforge/**/*.py"),
        MODULE.Rule(owner="connectors", priority=20, glob="src/trustforge/*connector*.py"),
    ]

    owner, matched_rule = MODULE.classify(
        "src/trustforge/ecolink_connector.py", rules
    )

    assert owner == "connectors"
    assert matched_rule == "src/trustforge/*connector*.py"


def test_glob_star_does_not_cross_directories_but_double_star_does() -> None:
    assert MODULE.glob_matches("src/trustforge/web.py", "src/trustforge/*.py")
    assert not MODULE.glob_matches(
        "src/trustforge/nested/web.py", "src/trustforge/*.py"
    )
    assert MODULE.glob_matches(
        "src/trustforge/nested/web.py", "src/trustforge/**/*.py"
    )


def test_cross_layer_test_can_have_multiple_owners() -> None:
    rules = [
        MODULE.Rule(owner="web", priority=10, glob="tests/test_*.py"),
        MODULE.Rule(owner="platform", priority=55, glob="tests/test_*policy*.py"),
        MODULE.Rule(owner="agent", priority=70, glob="tests/test_*runtime*.py"),
    ]

    owners, _ = MODULE.classify_test("tests/test_policy_runtime.py", rules)

    assert owners == ["agent", "platform"]


def test_empty_inventory_globs_fail_closed() -> None:
    manifest = MODULE.load_manifest(ROOT / "qa" / "package-ownership.json")
    manifest["inventory_globs"] = []

    with pytest.raises(MODULE.InventoryError, match="must not be empty"):
        MODULE.build_inventory(ROOT, manifest)


def test_test_imports_add_consumer_owners(tmp_path: Path) -> None:
    test_file = tmp_path / "test_neutral_name.py"
    test_file.write_text(
        "from trustforge import web\nfrom trustforge_core import aggregation\n",
        encoding="utf-8",
    )

    owners, evidence = MODULE.infer_test_owners(
        tmp_path,
        test_file.name,
        {"trustforge.web": "web", "trustforge_core.aggregation": "core"},
    )

    assert owners == {"core", "web"}
    assert evidence == {"import:trustforge.web", "import:trustforge_core.aggregation"}


def test_malformed_dependency_and_rule_are_rejected() -> None:
    with pytest.raises(MODULE.InventoryError, match="list of package names"):
        MODULE.validate_dependency_graph({"core": {"depends_on": [False]}})

    manifest = {
        "packages": {"core": {"depends_on": []}},
        "rules": [{"owner": "core", "priority": True, "glob": "src/**"}],
    }
    with pytest.raises(MODULE.InventoryError, match="every rule requires"):
        MODULE._rules(manifest)
