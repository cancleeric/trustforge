from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_package_inventory import load_manifest  # noqa: E402
from select_package_tests import (  # noqa: E402
    SelectionError,
    parse_name_status,
    require_clean_worktree,
    reverse_dependents,
    select,
)


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(ROOT / "qa" / "package-ownership.json")


def test_reverse_dependents_follows_declared_dag(manifest):
    packages = manifest["packages"]
    assert reverse_dependents(packages, {"connectors"}) == {
        "connectors",
        "agent",
        "web",
        "frontend",
    }
    assert reverse_dependents(packages, {"native"}) == {"native"}


@pytest.mark.parametrize(
    ("path", "seed", "affected"),
    [
        ("src/trustforge/trust/scoring.py", "core", {"core", "platform", "connectors", "agent", "web", "frontend"}),
        ("src/trustforge/schema.py", "platform", {"core", "platform", "connectors", "agent", "web", "frontend"}),
        ("src/trustforge/bedrock.py", "connectors", {"core", "platform", "connectors", "agent", "web", "frontend"}),
        ("src/trustforge/analysis_flow.py", "agent", {"agent", "web", "frontend"}),
        ("src/trustforge/web.py", "web", {"agent", "web", "frontend"}),
        ("frontend/src/main.tsx", "frontend", {"frontend"}),
        ("native/trustforge-native-sys/src/lib.rs", "native", {"native"}),
    ],
)
def test_owned_changes_expand_to_downstream_packages(manifest, path, seed, affected):
    receipt = select(ROOT, manifest, [path])
    assert receipt["seed_packages"] == [seed]
    assert set(receipt["affected_packages"]) == affected
    assert receipt["full_suite_required"] is False


def test_global_trigger_fails_closed_to_full_suite(manifest):
    receipt = select(ROOT, manifest, ["pyproject.toml"])
    assert receipt["full_suite_required"] is True
    assert receipt["full_suite_reasons"] == ["global_trigger:pyproject.toml"]
    assert receipt["execution_plan"]["mode"] == "full_suite"
    assert receipt["execution_plan"]["lanes"] == ["backend", "frontend", "native"]
    assert receipt["execution_plan"]["backend_test_count"] > 300


def test_unclassified_path_fails_closed_to_full_suite(manifest):
    receipt = select(ROOT, manifest, ["unexpected/new-policy.txt"])
    assert receipt["full_suite_required"] is True
    assert receipt["full_suite_reasons"] == ["unclassified:unexpected/new-policy.txt"]
    assert receipt["execution_plan"]["mode"] == "full_suite"


def test_docs_only_change_selects_no_lane(manifest):
    receipt = select(ROOT, manifest, ["docs/architecture.md"])
    assert receipt["full_suite_required"] is False
    assert receipt["shadow_candidate_lanes"] == []
    assert receipt["shadow_candidate_backend_tests"] == []


def test_direct_test_change_is_selected(manifest):
    path = "tests/test_package_inventory.py"
    receipt = select(ROOT, manifest, [path])
    assert path in receipt["shadow_candidate_backend_tests"]
    assert "backend" in receipt["shadow_candidate_lanes"]


def test_temporary_bridge_adds_reverse_consumer(manifest):
    receipt = select(ROOT, manifest, ["src/trustforge/modelhub_backend.py"])
    assert "platform" in receipt["affected_packages"]
    assert "connectors" in receipt["affected_packages"]


def test_architecture_contracts_are_always_selected(manifest):
    receipt = select(ROOT, manifest, ["frontend/src/main.tsx"])
    assert "tests/test_architecture_import_boundaries.py" in receipt["shadow_candidate_backend_tests"]
    assert "tests/test_package_inventory.py" in receipt["shadow_candidate_backend_tests"]
    assert "tests/test_package_test_selection.py" in receipt["shadow_candidate_backend_tests"]


def test_deleted_test_fails_closed(manifest):
    path = "tests/test_removed_connector.py"
    receipt = select(ROOT, manifest, [path], deleted_paths={path})
    assert receipt["full_suite_required"] is True
    assert receipt["full_suite_reasons"] == [f"deleted_test:{path}"]


def test_name_status_parser_preserves_both_sides_of_rename_and_special_paths():
    paths, deleted = parse_name_status(
        b"R100\0src/trustforge/trust/old.py\0src/trustforge/agent/new.py\0"
        b"M\0docs/line\nname.md\0D\0tests/test_old.py\0"
    )
    assert "src/trustforge/trust/old.py" in paths
    assert "src/trustforge/agent/new.py" in paths
    assert "docs/line\nname.md" in paths
    assert deleted == ["src/trustforge/trust/old.py", "tests/test_old.py"]


def test_base_selection_rejects_dirty_worktree(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "dirty.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(SelectionError, match="clean worktree"):
        require_clean_worktree(tmp_path)


def test_cli_emits_shadow_receipt():
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "select_package_tests.py"),
            "--changed-file",
            "src/trustforge/bedrock.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["mode"] == "shadow_only"
    assert receipt["selection_ready"] is False
    assert receipt["seed_packages"] == ["connectors"]


def test_cli_requires_a_change_source():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "select_package_tests.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "provide --base" in result.stderr
