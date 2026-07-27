from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.migrate_release_ledgers import _allowed_entry, _recover
from scripts.provision_release_ledgers import _recover_provision


def _write_canonical(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    path.chmod(0o600)


def _provision_payload(state: str, stage: Path, backup: Path) -> dict[str, object]:
    return {
        "schema": "trustforge.release-ledger-provision/v2",
        "state": state,
        "stage": stage.name,
        "backup": backup.name,
        "control_public": {"key_id": "control-bootstrap-1", "public_key": "00" * 32},
        "outcome_public": {
            "key_id": "router-outcome-bootstrap-1",
            "public_key": "11" * 32,
        },
    }


def test_provision_recovery_restores_preexisting_skeleton(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    stage = tmp_path / ".ledger.provision-123"
    backup = tmp_path / ".ledger.preprovisioned"
    journal = tmp_path / ".provision-transaction.json"
    backup.mkdir()
    (backup / "marker").write_text("old")
    stage.mkdir()
    (stage / "partial").write_text("new")
    _write_canonical(journal, _provision_payload("old-backed-up", stage, backup))

    _recover_provision(root, journal)

    assert (root / "marker").read_text() == "old"
    assert not stage.exists()
    assert not backup.exists()
    assert not journal.exists()


def test_provision_recovery_replaces_uncommitted_published_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ledger"
    stage = tmp_path / ".ledger.provision-123"
    backup = tmp_path / ".ledger.preprovisioned"
    journal = tmp_path / ".provision-transaction.json"
    root.mkdir()
    (root / "new").write_text("not durable")
    backup.mkdir()
    (backup / "old").write_text("preprovisioned")
    _write_canonical(journal, _provision_payload("old-backed-up", stage, backup))

    _recover_provision(root, journal)

    assert (root / "old").read_text() == "preprovisioned"
    assert not (root / "new").exists()


def test_provision_recovery_keeps_committed_target(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    stage = tmp_path / ".ledger.provision-123"
    backup = tmp_path / ".ledger.preprovisioned"
    journal = tmp_path / ".provision-transaction.json"
    root.mkdir()
    (root / "new").write_text("durable")
    backup.mkdir()
    (backup / "old").write_text("skeleton")
    _write_canonical(journal, _provision_payload("committed", stage, backup))

    _recover_provision(root, journal)

    assert (root / "new").read_text() == "durable"
    assert not backup.exists()
    assert journal.exists()


def test_provision_recovery_rejects_recorded_path_traversal(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    journal = tmp_path / ".provision-transaction.json"
    payload = _provision_payload(
        "staged",
        tmp_path / ".ledger.provision-123",
        tmp_path / ".ledger.preprovisioned",
    )
    payload["stage"] = "../attacker"
    _write_canonical(journal, payload)

    with pytest.raises(SystemExit, match="unsafe provisioning journal path"):
        _recover_provision(root, journal)


def test_migration_recovery_rejects_noncanonical_journal(tmp_path: Path) -> None:
    target = tmp_path / "ledger"
    journal = tmp_path / "ledger.migration.json"
    journal.write_text(
        '{"schema": "trustforge.release-ledger-migration/v1", '
        '"state":"staged","stage":"ledger.staging","backup":"ledger.rollback"}\n'
    )
    journal.chmod(0o600)

    with pytest.raises(SystemExit, match="unknown migration recovery journal"):
        _recover(
            journal,
            target,
            tmp_path / "ledger.staging",
            tmp_path / "ledger.rollback",
        )


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
    _write_canonical(
        journal,
        {
            "schema": "trustforge.release-ledger-migration/v1",
            "state": "old-backed-up",
            "stage": stage.name,
            "backup": backup.name,
        },
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
    _write_canonical(
        journal,
        {
            "schema": "trustforge.release-ledger-migration/v1",
            "state": "published",
            "stage": stage.name,
            "backup": backup.name,
        },
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
