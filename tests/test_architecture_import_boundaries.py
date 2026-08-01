"""Five-layer package dependency boundary checks (#1253)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "check_package_boundaries", SCRIPTS / "check_package_boundaries.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _packages() -> dict[str, dict[str, list[str]]]:
    return {
        "core": {"depends_on": []},
        "platform": {"depends_on": ["core"]},
        "connectors": {"depends_on": ["core", "platform"]},
        "agent": {"depends_on": ["core", "platform", "connectors"]},
        "web": {"depends_on": ["core", "platform", "connectors", "agent"]},
    }


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        ("core", "platform"),
        ("platform", "connectors"),
        ("connectors", "agent"),
        ("agent", "web"),
        ("core", "web"),
    ],
)
def test_each_reverse_dependency_is_rejected(lower: str, higher: str) -> None:
    owners = {"pkg.lower": lower, "pkg.higher": higher}
    edge = MODULE.ImportEdge("pkg.lower", "pkg.higher", 1)

    violations, used = MODULE.forbidden_edges([edge], owners, _packages(), [])

    assert violations == [edge]
    assert used == set()


def test_same_layer_and_declared_lower_dependency_are_allowed() -> None:
    owners = {
        "pkg.agent_a": "agent",
        "pkg.agent_b": "agent",
        "pkg.connector": "connectors",
    }
    edges = [
        MODULE.ImportEdge("pkg.agent_a", "pkg.agent_b", 1),
        MODULE.ImportEdge("pkg.agent_a", "pkg.connector", 2),
    ]

    violations, _ = MODULE.forbidden_edges(edges, owners, _packages(), [])

    assert violations == []


def test_issue_backed_bridge_allows_only_its_exact_importer() -> None:
    owners = {"pkg.core": "core", "pkg.web": "web", "pkg.other": "core"}
    bridge = MODULE.Bridge("pkg.core", "pkg.web", "#1253")
    allowed = MODULE.ImportEdge("pkg.core", "pkg.web.child", 1, symbol_fallback=True)
    forbidden = MODULE.ImportEdge("pkg.other", "pkg.web", 2)

    violations, used = MODULE.forbidden_edges(
        [allowed, forbidden], owners, _packages(), [bridge]
    )

    assert violations == [forbidden]
    assert used == {bridge}


def test_repository_obeys_manifest_dependency_dag() -> None:
    manifest = MODULE.load_manifest(ROOT / "qa" / "package-ownership.json")

    result = MODULE.check(ROOT, manifest)

    assert result["modules"] > 200
    assert result["imports"] > 500
    assert result["temporary_bridges"] > 0
    assert all(result["package_module_counts"][name] > 0 for name in _packages())


def test_stale_and_invalid_bridges_fail_closed() -> None:
    owners = {"pkg.core": "core", "pkg.web": "web"}
    manifest = {"temporary_bridges": [{"importer": "pkg.core", "imported": "pkg.web", "issue": "not-an-issue"}]}

    with pytest.raises(MODULE.BoundaryError, match="invalid issue"):
        MODULE.load_bridges(manifest, owners)


def test_unknown_importer_fails_closed() -> None:
    edge = MODULE.ImportEdge("pkg.unknown", "pkg.core", 1)

    with pytest.raises(MODULE.BoundaryError, match="unknown importing"):
        MODULE.forbidden_edges([edge], {"pkg.core": "core"}, _packages(), [])


def test_unknown_internal_import_fails_closed() -> None:
    edge = MODULE.ImportEdge("trustforge.web", "trustforge.missing", 1)

    with pytest.raises(MODULE.BoundaryError, match="unknown imported"):
        MODULE.forbidden_edges(
            [edge], {"trustforge": "platform", "trustforge.web": "web"}, _packages(), []
        )


def test_relative_import_resolution_distinguishes_package_init() -> None:
    assert MODULE.resolve_from_import(
        "trustforge.agent", 1, "orchestrator", importer_is_package=True
    ) == "trustforge.agent.orchestrator"
    assert MODULE.resolve_from_import(
        "trustforge.agent.runtime", 1, "contracts"
    ) == "trustforge.agent.contracts"


def test_from_import_alias_is_scanned_as_possible_submodule(tmp_path: Path) -> None:
    src_root = tmp_path / "src"
    module = src_root / "trustforge" / "agent" / "runtime.py"
    module.parent.mkdir(parents=True)
    module.write_text("from trustforge import web\n", encoding="utf-8")

    edges = MODULE.import_edges(module, src_root)

    assert MODULE.ImportEdge(
        "trustforge.agent.runtime", "trustforge.web", 1, symbol_fallback=True
    ) in edges


@pytest.mark.parametrize(
    "source",
    [
        'import importlib\nimportlib.import_module("trustforge.web")\n',
        'import importlib as il\nil.import_module("trustforge.web")\n',
        'from importlib import import_module as load\nload("trustforge.web")\n',
        '__import__("trustforge.web")\n',
    ],
)
def test_literal_dynamic_internal_imports_are_scanned(
    tmp_path: Path, source: str
) -> None:
    src_root = tmp_path / "src"
    module = src_root / "trustforge" / "agent" / "runtime.py"
    module.parent.mkdir(parents=True)
    module.write_text(source, encoding="utf-8")

    edges = MODULE.import_edges(module, src_root)

    assert MODULE.ImportEdge("trustforge.agent.runtime", "trustforge.web", 2) in edges or MODULE.ImportEdge(
        "trustforge.agent.runtime", "trustforge.web", 1
    ) in edges


def test_relative_literal_dynamic_import_is_resolved(tmp_path: Path) -> None:
    src_root = tmp_path / "src"
    module = src_root / "trustforge" / "agent" / "runtime.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        'from importlib import import_module\nimport_module(".web", package=__package__)\n',
        encoding="utf-8",
    )

    edges = MODULE.import_edges(module, src_root)

    assert MODULE.ImportEdge(
        "trustforge.agent.runtime", "trustforge.agent.web", 2
    ) in edges
