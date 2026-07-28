from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import hourly_release_train as train


def test_patch_bump_synchronizes_backend_and_frontend_versions(tmp_path):
    files = {
        "pyproject.toml": (
            '[project]\ndynamic = ["version"]\n'
            '[tool.setuptools.dynamic]\nversion = {attr = "trustforge._version.VERSION"}\n'
        ),
        "src/trustforge/__init__.py": (
            "from ._version import VERSION as __version__\n"
        ),
        "src/trustforge/_version.py": 'VERSION = "1.2.3"\n',
        "frontend/package.json": '{"version": "1.2.3"}\n',
        "frontend/package-lock.json": (
            '{"version": "1.2.3", "packages": {"": {"version": "1.2.3"}}}\n'
        ),
    }
    for relative, body in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")

    assert train.bump_patch_version(tmp_path) == "1.2.4"
    assert 'VERSION = "1.2.4"' in (
        tmp_path / "src/trustforge/_version.py"
    ).read_text(encoding="utf-8")
    assert '"version": "1.2.4"' in (
        tmp_path / "frontend/package.json"
    ).read_text(encoding="utf-8")


def test_backup_receipt_requires_archive_and_restore_verification(tmp_path, monkeypatch):
    train.OUT = tmp_path
    archive = tmp_path / "backup.tar.gz"
    archive.write_bytes(b"backup")

    def fake_run(*args, **kwargs):
        receipt = Path(kwargs["env"]["TRUSTFORGE_BACKUP_RECEIPT"])
        receipt.write_text(json.dumps({
            "schema": "trustforge.production-backup/v1",
            "run_id": "run",
            "archive": str(archive),
            "archive_sha256": train.hashlib.sha256(b"backup").hexdigest(),
            "restore_verified": True,
        }))

    monkeypatch.setattr(train.subprocess, "run", fake_run)
    assert train.require_backup_receipt("backup", "run") == tmp_path / "run-backup.json"


def test_backup_receipt_fails_closed_without_restore_verification(tmp_path, monkeypatch):
    train.OUT = tmp_path

    def fake_run(*args, **kwargs):
        receipt = Path(kwargs["env"]["TRUSTFORGE_BACKUP_RECEIPT"])
        receipt.write_text(json.dumps({"archive": str(tmp_path / "missing"), "restore_verified": False}))

    monkeypatch.setattr(train.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="verified restorable"):
        train.require_backup_receipt("backup", "run")


def test_lease_rejects_overlap(tmp_path):
    train.OUT = tmp_path
    with train.lease():
        with pytest.raises(RuntimeError, match="owns the lease"):
            with train.lease():
                pass


def test_lease_recovers_dead_owner(tmp_path, monkeypatch):
    train.OUT = tmp_path
    lock = tmp_path / "lease"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(json.dumps({"pid": 99999999, "birth": "old", "token": "old"}))

    def dead_owner(_pid, _signal):
        raise ProcessLookupError

    monkeypatch.setattr(train.os, "kill", dead_owner)
    with train.lease():
        assert (lock / "owner.json").is_file()
