from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_version.py"
SPEC = importlib.util.spec_from_file_location("trustforge_release_version", SCRIPT)
assert SPEC and SPEC.loader
release_version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_version)


def _repo(root: Path, version: str = "0.18.1") -> None:
    (root / "src/trustforge").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "docs").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\ndynamic = ["version"]\n'
        '[tool.setuptools.dynamic]\nversion = {attr = "trustforge._version.VERSION"}\n',
        encoding="utf-8",
    )
    (root / "src/trustforge/__init__.py").write_text(
        "from ._version import VERSION as __version__\n",
        encoding="utf-8",
    )
    (root / "src/trustforge/_version.py").write_text(f'VERSION = "{version}"\n', encoding="utf-8")
    (root / "frontend/package.json").write_text(
        json.dumps({"name": "frontend", "version": version}) + "\n",
        encoding="utf-8",
    )
    (root / "frontend/package-lock.json").write_text(
        json.dumps({"name": "frontend", "version": version, "packages": {"": {"version": version}}}) + "\n",
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")


def test_highest_release_version_uses_semver_not_lexical_order() -> None:
    assert release_version.highest_release_version(["v0.9.9", "v0.10.0", "v0.2.99"]) == (0, 10, 0)


def test_list_release_tags_combines_local_and_remote(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    outputs = iter(
        [
            "v0.9.0\nv0.10.0\n",
            "abc\trefs/tags/v0.27.0\nabc\trefs/tags/v0.27.0^{}\nabc\trefs/tags/not-semver\n",
        ]
    )
    monkeypatch.setattr(release_version.subprocess, "check_output", lambda *args, **kwargs: next(outputs))
    assert release_version.list_release_tags(tmp_path) == ["v0.9.0", "v0.10.0", "v0.27.0"]


@pytest.mark.parametrize(
    ("level", "expected"),
    [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0")],
)
def test_bumped_version(level: str, expected: str) -> None:
    assert release_version.bumped_version((1, 2, 3), level) == expected


def test_update_version_files_synchronizes_every_source(tmp_path: Path) -> None:
    _repo(tmp_path)
    release_version.update_version_files("0.27.0", tmp_path)
    assert set(release_version.version_sources(tmp_path).values()) == {"0.27.0"}
    assert "## v0.27.0" in (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert (tmp_path / "docs/RELEASE-NOTES-v0.27.0.md").is_file()


def test_version_sources_exposes_derived_mismatch(tmp_path: Path) -> None:
    _repo(tmp_path)
    package_path = tmp_path / "frontend/package.json"
    package_path.write_text('{"name":"frontend","version":"9.9.9"}\n', encoding="utf-8")
    assert release_version.version_sources(tmp_path)["frontend/package.json"] == "9.9.9"
