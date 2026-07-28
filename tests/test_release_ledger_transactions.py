from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.migrate_release_ledgers import _allowed_entry, _publish_swap, _recover
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


def test_fresh_publishing_recovery_removes_uncommitted_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ledger"
    stage = tmp_path / ".ledger.provision-123"
    backup = tmp_path / ".ledger.preprovisioned"
    journal = tmp_path / ".provision-transaction.json"
    root.mkdir()
    (root / "uncommitted").write_text("new")
    _write_canonical(journal, _provision_payload("publishing", stage, backup))

    _recover_provision(root, journal)

    assert not root.exists()
    assert not journal.exists()


def test_sigkill_then_restart_recovers_fresh_publishing_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ledger"
    stage = tmp_path / ".ledger.provision-777"
    backup = tmp_path / ".ledger.preprovisioned"
    journal = tmp_path / ".provision-transaction.json"
    payload = _provision_payload("publishing", stage, backup)
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,os,pathlib,signal,sys;"
                "root=pathlib.Path(sys.argv[1]);journal=pathlib.Path(sys.argv[2]);"
                "root.mkdir();(root/'partial').write_text('new');"
                "journal.write_text(json.dumps(json.loads(sys.argv[3]),"
                "sort_keys=True,separators=(',',':'))+'\\n');"
                "journal.chmod(0o600);"
                "os.kill(os.getpid(),signal.SIGKILL)"
            ),
            str(root),
            str(journal),
            json.dumps(payload),
        ],
    )
    assert child.returncode < 0

    restarted = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import pathlib,sys;"
                "from scripts.provision_release_ledgers import _recover_provision;"
                "_recover_provision(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]))"
            ),
            str(root),
            str(journal),
        ],
        timeout=10,
    )

    assert restarted.returncode == 0
    assert not root.exists()
    assert not journal.exists()


@pytest.mark.parametrize(
    ("state", "root_exists", "stage_exists", "backup_exists", "root_survives"),
    [
        ("staged", True, True, False, True),
        ("publishing", True, False, False, False),
        ("old-backed-up", False, True, True, True),
    ],
)
def test_provision_sigkill_restart_precommit_matrix(
    tmp_path: Path,
    state: str,
    root_exists: bool,
    stage_exists: bool,
    backup_exists: bool,
    root_survives: bool,
) -> None:
    root = tmp_path / "ledger"
    stage = tmp_path / ".ledger.provision-888"
    backup = tmp_path / ".ledger.preprovisioned"
    journal = tmp_path / ".provision-transaction.json"
    payload = _provision_payload(state, stage, backup)
    setup = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,os,pathlib,signal,sys;"
                "root,stage,backup,journal=map(pathlib.Path,sys.argv[1:5]);"
                "flags=json.loads(sys.argv[5]);"
                "\nfor path,value,key in ((root,'old','root'),(stage,'new','stage'),"
                "(backup,'old','backup')):\n"
                "  if flags[key]:path.mkdir();(path/'value').write_text(value)\n"
                "journal.write_text(json.dumps(json.loads(sys.argv[6]),"
                "sort_keys=True,separators=(',',':'))+'\\n');journal.chmod(0o600);"
                "os.kill(os.getpid(),signal.SIGKILL)"
            ),
            str(root),
            str(stage),
            str(backup),
            str(journal),
            json.dumps(
                {"root": root_exists, "stage": stage_exists, "backup": backup_exists}
            ),
            json.dumps(payload),
        ],
    )
    assert setup.returncode < 0
    recovered = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import pathlib,sys;"
                "from scripts.provision_release_ledgers import _recover_provision;"
                "_recover_provision(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]))"
            ),
            str(root),
            str(journal),
        ],
        timeout=10,
    )

    assert recovered.returncode == 0
    assert root.exists() is root_survives
    assert not stage.exists()
    assert not backup.exists()
    assert not journal.exists()


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


@pytest.mark.parametrize(
    ("state", "target_exists", "stage_exists", "backup_exists", "expected"),
    [
        ("staged", True, True, False, "old"),
        ("old-backed-up", False, True, True, "old"),
        ("published", True, False, True, "old"),
        ("committed", True, False, True, "new"),
    ],
)
def test_migration_sigkill_restart_state_matrix(
    tmp_path: Path,
    state: str,
    target_exists: bool,
    stage_exists: bool,
    backup_exists: bool,
    expected: str,
) -> None:
    target = tmp_path / "ledger"
    stage = tmp_path / "ledger.staging"
    backup = tmp_path / "ledger.rollback"
    journal = tmp_path / "ledger.migration.json"
    payload = {
        "schema": "trustforge.release-ledger-migration/v1",
        "state": state,
        "stage": stage.name,
        "backup": backup.name,
    }
    setup = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,os,pathlib,signal,sys;"
                "target,stage,backup,journal=map(pathlib.Path,sys.argv[1:5]);"
                "flags=json.loads(sys.argv[5]);"
                "\nfor path,value,create in ((target,flags['target_value'],flags['target']),"
                "(stage,'stage',flags['stage']),(backup,'old',flags['backup'])):\n"
                "  if create:path.mkdir();(path/'value').write_text(value)\n"
                "journal.write_text(json.dumps(json.loads(sys.argv[6]),"
                "sort_keys=True,separators=(',',':'))+'\\n');journal.chmod(0o600);"
                "os.kill(os.getpid(),signal.SIGKILL)"
            ),
            str(target),
            str(stage),
            str(backup),
            str(journal),
            json.dumps(
                {
                    "target": target_exists,
                    "target_value": "new"
                    if state in {"published", "committed"}
                    else "old",
                    "stage": stage_exists,
                    "backup": backup_exists,
                }
            ),
            json.dumps(payload),
        ],
    )
    assert setup.returncode < 0

    recovered = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import pathlib,sys;"
                "from scripts.migrate_release_ledgers import _recover;"
                "_recover(pathlib.Path(sys.argv[4]),pathlib.Path(sys.argv[1]),"
                "pathlib.Path(sys.argv[2]),pathlib.Path(sys.argv[3]))"
            ),
            str(target),
            str(stage),
            str(backup),
            str(journal),
        ],
        timeout=10,
    )

    assert recovered.returncode == 0
    assert (target / "value").read_text() == expected
    assert not stage.exists()
    assert not backup.exists()
    assert not journal.exists()


def test_same_root_publish_performs_real_mode_and_receipt_swap(
    tmp_path: Path,
) -> None:
    target = tmp_path / "ledger"
    stage = tmp_path / "ledger.staging"
    backup = tmp_path / "ledger.rollback"
    journal = tmp_path / "ledger.migration.json"
    target.mkdir(mode=0o700)
    (target / "control").mkdir(mode=0o700)
    stage.mkdir(mode=0o750)
    (stage / "control").mkdir(mode=0o750)
    (stage / "router-outcomes").mkdir(mode=0o750)
    (stage / "provision-receipt.json").write_text("authenticated receipt\n")

    _publish_swap(stage, target, backup, journal)

    assert stat.S_IMODE(target.stat().st_mode) == 0o750
    assert stat.S_IMODE((target / "control").stat().st_mode) == 0o750
    assert stat.S_IMODE((target / "router-outcomes").stat().st_mode) == 0o750
    assert (target / "provision-receipt.json").read_text() == "authenticated receipt\n"
    assert target.stat().st_uid == os.geteuid()
    assert not backup.exists()
    assert not journal.exists()


@pytest.mark.parametrize("failure_call", [1, 2, 3, 4])
def test_publish_fsync_failures_recover_old_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_call: int
) -> None:
    target = tmp_path / "ledger"
    stage = tmp_path / "ledger.staging"
    backup = tmp_path / "ledger.rollback"
    journal = tmp_path / "ledger.migration.json"
    target.mkdir()
    (target / "value").write_text("old")
    stage.mkdir()
    (stage / "value").write_text("new")
    real_fsync = os.fsync
    calls = 0

    def failing_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("injected fsync failure")
        real_fsync(fd)

    monkeypatch.setattr("scripts.migrate_release_ledgers.os.fsync", failing_fsync)
    with pytest.raises(OSError, match="injected fsync"):
        _publish_swap(stage, target, backup, journal)
    monkeypatch.setattr("scripts.migrate_release_ledgers.os.fsync", real_fsync)
    if journal.exists():
        _recover(journal, target, stage, backup)

    assert (target / "value").read_text() == "old"
    assert not backup.exists()


@pytest.mark.parametrize(
    ("state", "seed_present"),
    [
        ("control-consuming", True),
        ("control-consuming", False),
        ("outcome-consuming", True),
        ("outcome-consuming", False),
    ],
)
def test_seed_consuming_crash_retains_committed_journal(
    tmp_path: Path, state: str, seed_present: bool
) -> None:
    root = tmp_path / "ledger"
    stage = tmp_path / ".ledger.provision-999"
    backup = tmp_path / ".ledger.preprovisioned"
    journal = tmp_path / ".provision-transaction.json"
    root.mkdir()
    if seed_present:
        seed = tmp_path / f"{state}.seed"
        seed.write_bytes(b"x" * 32)
        seed.chmod(0o400)
    _write_canonical(journal, _provision_payload(state, stage, backup))

    recovered = _recover_provision(root, journal)

    assert recovered is not None
    assert recovered["state"] == state
    assert journal.exists()
    assert root.exists()


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
