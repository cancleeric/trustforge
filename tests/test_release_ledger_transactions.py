from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.migrate_release_ledgers import _allowed_entry, _recover
from scripts.provision_release_ledgers import _recover_provision


def test_provision_recovery_restores_preexisting_skeleton(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    stage = tmp_path / ".ledger.stage"
    backup = tmp_path / ".ledger.backup"
    journal = tmp_path / ".provision-transaction.json"
    backup.mkdir()
    (backup / "marker").write_text("old")
    stage.mkdir()
    (stage / "partial").write_text("new")
    journal.write_text(
        json.dumps(
            {"state": "old-backed-up", "stage": stage.name, "backup": backup.name}
        )
    )

    _recover_provision(root, stage, backup, journal)

    assert (root / "marker").read_text() == "old"
    assert not stage.exists()
    assert not backup.exists()
    assert not journal.exists()


def test_provision_recovery_replaces_uncommitted_published_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ledger"
    stage = tmp_path / ".ledger.stage"
    backup = tmp_path / ".ledger.backup"
    journal = tmp_path / ".provision-transaction.json"
    root.mkdir()
    (root / "new").write_text("not durable")
    backup.mkdir()
    (backup / "old").write_text("preprovisioned")
    journal.write_text(
        json.dumps(
            {"state": "old-backed-up", "stage": stage.name, "backup": backup.name}
        )
    )

    _recover_provision(root, stage, backup, journal)

    assert (root / "old").read_text() == "preprovisioned"
    assert not (root / "new").exists()


def test_provision_recovery_keeps_committed_target(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    stage = tmp_path / ".ledger.stage"
    backup = tmp_path / ".ledger.backup"
    journal = tmp_path / ".provision-transaction.json"
    root.mkdir()
    (root / "new").write_text("durable")
    backup.mkdir()
    (backup / "old").write_text("skeleton")
    journal.write_text(
        json.dumps(
            {"state": "committed", "stage": stage.name, "backup": backup.name}
        )
    )

    _recover_provision(root, stage, backup, journal)

    assert (root / "new").read_text() == "durable"
    assert not backup.exists()


def test_migration_recovery_restores_old_target_after_failed_publish(
    tmp_path: Path,
) -> None:
    target = tmp_path / "ledger"
    stage = tmp_path / "ledger.staging"
    backup = tmp_path / "ledger.rollback"
    journal = tmp_path / "ledger.migration.json"
    backup.mkdir()
    (backup / "authenticated-old").write_text("old")
    stage.mkdir()
    journal.write_text(
        json.dumps(
            {
                "schema": "trustforge.release-ledger-migration/v1",
                "state": "old-backed-up",
                "stage": stage.name,
                "backup": backup.name,
            }
        )
    )

    _recover(journal, target, stage, backup)

    assert (target / "authenticated-old").read_text() == "old"
    assert not stage.exists()
    assert not journal.exists()


def test_migration_recovery_rolls_back_published_but_not_durable_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "ledger"
    stage = tmp_path / "ledger.staging"
    backup = tmp_path / "ledger.rollback"
    journal = tmp_path / "ledger.migration.json"
    target.mkdir()
    (target / "new").write_text("not durable")
    backup.mkdir()
    (backup / "old").write_text("authenticated")
    journal.write_text(
        json.dumps(
            {
                "schema": "trustforge.release-ledger-migration/v1",
                "state": "published",
                "stage": stage.name,
                "backup": backup.name,
            }
        )
    )

    _recover(journal, target, stage, backup)

    assert (target / "old").read_text() == "authenticated"
    assert not (target / "new").exists()


def test_migration_allowlist_rejects_unexpected_state() -> None:
    assert _allowed_entry("bootstrap.json")
    assert _allowed_entry("epoch-stop-" + "a" * 64 + ".json")
    assert not _allowed_entry("private-key.json")
    assert not _allowed_entry(".coordination.lock")


def test_exclusive_migration_lock_fences_concurrent_writer(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.touch()
    descriptor = os.open(events, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,os,sys;"
                "fd=os.open(sys.argv[1],os.O_RDWR);"
                "fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)"
            ),
            str(events),
        ],
        stderr=subprocess.PIPE,
    )
    assert child.wait(timeout=5) != 0
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)
