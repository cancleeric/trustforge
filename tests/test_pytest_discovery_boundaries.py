from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path


def test_agent_worktree_directories_are_not_collected(tmp_path: Path) -> None:
    """The repository pytest config excludes duplicate agent worktrees."""

    repository_root = Path(__file__).resolve().parents[1]
    with (repository_root / "pyproject.toml").open("rb") as config_file:
        configured_exclusions = tomllib.load(config_file)["tool"]["pytest"][
            "ini_options"
        ]["norecursedirs"]
    assert ".Codex" in configured_exclusions
    assert ".codex" in configured_exclusions
    assert ".*" in configured_exclusions
    assert "node_modules" in configured_exclusions

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_normal.py").write_text(
        "def test_normal_collection():\n    pass\n",
        encoding="utf-8",
    )
    for metadata_dir in (".Codex", ".codex", ".scratch", "node_modules"):
        duplicate_tests = tmp_path / metadata_dir / "worktree" / "tests"
        duplicate_tests.mkdir(parents=True)
        (duplicate_tests / f"test_duplicate_{metadata_dir[1:]}.py").write_text(
            "def test_duplicate_collection():\n    pass\n",
            encoding="utf-8",
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--no-cov",
            "--rootdir",
            str(tmp_path),
            "-c",
            str(repository_root / "pyproject.toml"),
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_normal.py::test_normal_collection" in result.stdout
    assert "test_duplicate_collection" not in result.stdout
    assert "1 test collected" in result.stdout
