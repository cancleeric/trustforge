from __future__ import annotations

import json
import tarfile
import zipfile
from xml.etree import ElementTree
from pathlib import Path

import pytest

from scripts.package_finale_submission import package_submission, validate_live_artifacts


REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_team_11_final_competition_artifacts_are_authentic_and_parseable():
    expected = {
        "TrustForge_賽前提案報告.docx",
        "TrustForge_決賽6分鐘簡報.html",
        "TrustForge_決賽4分鐘備詢.docx",
    }
    outputs = REPO_ROOT / "outputs"
    assert expected <= {path.name for path in outputs.iterdir() if path.is_file()}

    final_deck = outputs / "TrustForge_決賽6分鐘簡報.html"
    canonical_deck = (
        REPO_ROOT
        / "docs/competition/slide-deck/TrustForge_正式提案簡報_6分鐘.html"
    )
    html = final_deck.read_text(encoding="utf-8")
    assert final_deck.read_bytes() == canonical_deck.read_bytes()
    assert "正式提案簡報" in html
    assert html.count('<section class="slide') == 6
    assert "逐字講稿" not in html
    assert "App Runner" not in html
    assert "EC2 + nginx" in html
    assert "https://" not in html and "http://" not in html
    assert "\ufffd" not in html and "\x00" not in html

    for name in expected - {final_deck.name}:
        with zipfile.ZipFile(outputs / name) as archive:
            assert archive.testzip() is None
            xml_names = [
                member
                for member in archive.namelist()
                if member.endswith((".xml", ".rels"))
            ]
            assert xml_names
            for member in xml_names:
                ElementTree.fromstring(archive.read(member))
