import json
import zipfile

import pytest

from scripts.package_submission import package_submission, validate_artifact_dir


def _write_valid_artifacts(path):
    path.mkdir()
    (path / "report.md").write_text("# Report\\nnon offline\\n", encoding="utf-8")
    (path / "evidence.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    (path / "execution_log.jsonl").write_text('{"tool":"bedrock.invoke"}\\n', encoding="utf-8")


def test_validate_submission_artifacts_accepts_complete_directory(tmp_path):
    artifact_dir = tmp_path / "bedrock-live"
    _write_valid_artifacts(artifact_dir)

    assert validate_artifact_dir(artifact_dir) == []


def test_validate_submission_artifacts_rejects_offline_marker(tmp_path):
    artifact_dir = tmp_path / "bedrock-live"
    _write_valid_artifacts(artifact_dir)
    (artifact_dir / "report.md").write_text("[OFFLINE]\\n", encoding="utf-8")

    assert "report.md contains [OFFLINE] marker" in validate_artifact_dir(artifact_dir)


def test_validate_submission_artifacts_rejects_possible_secret(tmp_path):
    artifact_dir = tmp_path / "bedrock-live"
    _write_valid_artifacts(artifact_dir)
    (artifact_dir / "evidence.json").write_text(
        json.dumps({"api_key": "abcdefghijklmnopqrstuvwxyz123456"}),
        encoding="utf-8",
    )

    assert any("possible secret" in error for error in validate_artifact_dir(artifact_dir))


def test_package_submission_writes_expected_zip_entries(tmp_path):
    artifact_dir = tmp_path / "bedrock-live"
    _write_valid_artifacts(artifact_dir)
    out = tmp_path / "submission.zip"

    package_submission(artifact_dir, out, [])

    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == [
            "artifacts/evidence.json",
            "artifacts/execution_log.jsonl",
            "artifacts/report.md",
        ]


def test_package_submission_fails_on_missing_required_file(tmp_path):
    artifact_dir = tmp_path / "bedrock-live"
    artifact_dir.mkdir()

    with pytest.raises(SystemExit):
        package_submission(artifact_dir, tmp_path / "submission.zip", [])
