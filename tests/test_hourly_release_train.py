from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import hourly_release_train as train


def test_backup_receipt_requires_archive_and_restore_verification(tmp_path, monkeypatch):
    train.OUT = tmp_path
    archive = tmp_path / "backup.tar.gz"
    archive.write_bytes(b"backup")

    def fake_run(*args, **kwargs):
        receipt = Path(kwargs["env"]["TRUSTFORGE_BACKUP_RECEIPT"])
        receipt.write_text(json.dumps({"archive": str(archive), "restore_verified": True}))

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
