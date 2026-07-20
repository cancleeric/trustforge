from __future__ import annotations

import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.package_finale_submission import package_submission, validate_live_artifacts


def _write_live_artifacts(path: Path) -> None:
    path.mkdir()
    (path / "report.md").write_text("# Live report\nNo offline marker.\n", encoding="utf-8")
    (path / "evidence.json").write_text(json.dumps({"coin": "BTC"}), encoding="utf-8")
    (path / "execution_log.jsonl").write_text('{"event":"bedrock.invoke","status":200}\n', encoding="utf-8")


def test_rejects_offline_report(tmp_path):
    source = tmp_path / "live"
    _write_live_artifacts(source)
    (source / "report.md").write_text("[OFFLINE]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="OFFLINE"):
        validate_live_artifacts(source)


def test_rejects_log_without_bedrock_trace(tmp_path):
    source = tmp_path / "live"
    _write_live_artifacts(source)
    (source / "execution_log.jsonl").write_text('{"event":"start"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Bedrock"):
        validate_live_artifacts(source)


def test_packages_artifacts_and_repo_snapshot(tmp_path):
    source = tmp_path / "live"
    _write_live_artifacts(source)

    result = package_submission(
        source,
        tmp_path / "submission",
        "https://example.com/demo",
        require_clean_tree=False,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["demo_url"] == "https://example.com/demo"
    assert manifest["checks"]["secret_scan"] == "pass"
    assert (result.package_dir / "repo.tar.gz").is_file()

    with tarfile.open(result.package_dir / "repo.tar.gz", "r:gz") as archive:
        assert "pyproject.toml" in archive.getnames()

    with zipfile.ZipFile(result.zip_path) as archive:
        names = set(archive.namelist())
    assert "finale-submission/report.md" in names
    assert "finale-submission/evidence.json" in names
    assert "finale-submission/execution_log.jsonl" in names
    assert "finale-submission/repo.tar.gz" in names
    assert "finale-submission/manifest.json" in names
