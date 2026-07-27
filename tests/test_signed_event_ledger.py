from __future__ import annotations

import grp
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.authenticated_ledger import AuthenticatedLedger, LedgerError
from trustforge.signed_event_ledger import SignedEventLedger, _write_all

CONTROL_SEED = b"c" * 32
ROUTER_SEED = b"r" * 32
CONTROL_KINDS = frozenset(
    {
        "deployment_initialized",
        "operator_stop",
        "activation_prepared",
        "activation_completed",
        "activation_failed",
    }
)
ROUTER_KINDS = frozenset(
    {
        "candidate_reservation",
        "candidate_result",
        "router_emergency_stop",
    }
)


def _public(seed: bytes) -> bytes:
    return (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )


def _ledger(
    tmp_path,
    *,
    seed=CONTROL_SEED,
    domain="release-control",
    kinds=CONTROL_KINDS,
    **overrides,
):
    return SignedEventLedger(
        directory=tmp_path / "ledger",
        verification_keys={
            "control-1": _public(CONTROL_SEED),
            "router-1": _public(ROUTER_SEED),
        },
        event_permissions={
            "release-control": CONTROL_KINDS,
            "release-router-outcome": ROUTER_KINDS,
        },
        domain_keys={
            "release-control": frozenset({"control-1"}),
            "release-router-outcome": frozenset({"router-1"}),
        },
        signing_key_id="control-1" if domain == "release-control" else "router-1",
        signing_private_key=seed,
        signing_domain=domain,
        ledger_role=domain,
        bootstrap=True,
        coordination_root=tmp_path,
        **overrides,
    )


def test_projection_uses_public_keys_only_and_cannot_append(tmp_path):
    writer = _ledger(tmp_path)
    writer.append({"kind": "deployment_initialized"})
    projection = SignedEventLedger(
        directory=tmp_path / "ledger",
        verification_keys={"control-1": _public(CONTROL_SEED)},
        event_permissions={"release-control": CONTROL_KINDS},
        domain_keys={"release-control": frozenset({"control-1"})},
        ledger_role="release-control",
        coordination_root=tmp_path,
    )
    assert projection.read()[0]["event"]["kind"] == "deployment_initialized"
    with pytest.raises(LedgerError, match="projection-only"):
        projection.append({"kind": "operator_stop"})


def test_epoch_stop_latch_is_one_way_signed_and_projection_verifiable(tmp_path):
    writer = _ledger(tmp_path)
    initialized = writer.append({"kind": "deployment_initialized"})
    epoch = "a" * 64
    writer.trip_epoch_stop(ledger_id=initialized["ledger_id"], canary_epoch=epoch)
    projection = SignedEventLedger(
        directory=tmp_path / "ledger",
        verification_keys={"control-1": _public(CONTROL_SEED)},
        event_permissions={"release-control": CONTROL_KINDS},
        domain_keys={"release-control": frozenset({"control-1"})},
        ledger_role="release-control",
        coordination_root=tmp_path,
    )
    assert projection.epoch_stopped(
        ledger_id=initialized["ledger_id"], canary_epoch=epoch
    )
    writer.trip_epoch_stop(ledger_id=initialized["ledger_id"], canary_epoch=epoch)
    path = tmp_path / "ledger" / f"epoch-stop-{epoch}.json"
    payload = json.loads(path.read_text())
    payload["ledger_id"] = "0" * 32
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerError, match="authentication"):
        projection.epoch_stopped(ledger_id=initialized["ledger_id"], canary_epoch=epoch)


@pytest.mark.parametrize(
    "forbidden", ["operator_stop", "activation_prepared", "activation_completed"]
)
def test_router_private_key_cannot_sign_control_events(tmp_path, forbidden):
    router = _ledger(
        tmp_path,
        seed=ROUTER_SEED,
        domain="release-router-outcome",
        kinds=ROUTER_KINDS,
    )
    with pytest.raises(LedgerError, match="not authorized"):
        router.append({"kind": forbidden})


def test_forged_router_signature_with_control_kind_fails_projection(tmp_path):
    router = _ledger(
        tmp_path,
        seed=ROUTER_SEED,
        domain="release-router-outcome",
        kinds=ROUTER_KINDS,
    )
    router.append(
        {
            "kind": "candidate_reservation",
            "deployment_ledger_id": "a" * 32,
            "reservation_id": "1" * 32,
        }
    )
    path = tmp_path / "ledger" / "events.jsonl"
    record = json.loads(path.read_text().strip())
    record["event"]["kind"] = "operator_stop"
    path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerError):
        router.read()


def test_legacy_hmac_v1_ledger_fails_closed_under_ed25519_projection(tmp_path):
    legacy = AuthenticatedLedger(
        keyring={"legacy": b"h" * 32},
        active_key_id="legacy",
        test_directory_override=tmp_path,
    )
    legacy.append({"kind": "operator_stop"})
    with pytest.raises(LedgerError, match="legacy"):
        SignedEventLedger(
            directory=tmp_path / "control",
            verification_keys={"control-1": _public(CONTROL_SEED)},
            event_permissions={"release-control": CONTROL_KINDS},
            domain_keys={"release-control": frozenset({"control-1"})},
            ledger_role="release-control",
            coordination_root=tmp_path,
        )


def test_fresh_bootstrap_is_explicit_signed_and_restart_verifiable(tmp_path):
    directory = tmp_path / "control"
    kwargs = {
        "directory": directory,
        "verification_keys": {"control-1": _public(CONTROL_SEED)},
        "event_permissions": {"release-control": CONTROL_KINDS},
        "domain_keys": {"release-control": frozenset({"control-1"})},
        "ledger_role": "release-control",
        "coordination_root": tmp_path,
    }
    with pytest.raises(LedgerError, match="explicit secure bootstrap"):
        SignedEventLedger(**kwargs)
    writer = SignedEventLedger(
        **kwargs,
        signing_key_id="control-1",
        signing_private_key=CONTROL_SEED,
        signing_domain="release-control",
        bootstrap=True,
    )
    writer.append({"kind": "deployment_initialized"})
    assert (directory / "bootstrap.json").stat().st_mode & 0o777 == 0o600
    assert SignedEventLedger(**kwargs).read()[0]["event"]["kind"] == (
        "deployment_initialized"
    )


def test_bootstrap_directory_cannot_escape_or_alias_coordination_root(tmp_path):
    with pytest.raises(LedgerError, match="direct child"):
        SignedEventLedger(
            directory=tmp_path / "parent" / ".." / "escaped",
            verification_keys={"control-1": _public(CONTROL_SEED)},
            event_permissions={"release-control": CONTROL_KINDS},
            domain_keys={"release-control": frozenset({"control-1"})},
            signing_key_id="control-1",
            signing_private_key=CONTROL_SEED,
            signing_domain="release-control",
            ledger_role="release-control",
            coordination_root=tmp_path,
            bootstrap=True,
        )


def test_write_all_detects_zero_progress_and_partial_event_fails_closed(
    tmp_path, monkeypatch
):
    ledger = _ledger(tmp_path)
    original_write = __import__("os").write
    calls = 0

    def partial_then_zero(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(fd, bytes(data[:5]))
        return 0

    monkeypatch.setattr("trustforge.signed_event_ledger.os.write", partial_then_zero)
    with pytest.raises(LedgerError, match="partial"):
        ledger.append({"kind": "deployment_initialized"})
    monkeypatch.undo()
    with pytest.raises(LedgerError, match="truncated"):
        ledger.read()


def test_write_all_retries_short_writes(tmp_path, monkeypatch):
    path = tmp_path / "short-write"
    fd = __import__("os").open(
        path, __import__("os").O_WRONLY | __import__("os").O_CREAT, 0o600
    )
    original_write = __import__("os").write

    def short(fd_, data):
        return original_write(fd_, bytes(data[: max(1, len(data) // 2)]))

    monkeypatch.setattr("trustforge.signed_event_ledger.os.write", short)
    try:
        _write_all(fd, b"complete-payload")
    finally:
        __import__("os").close(fd)
    assert path.read_bytes() == b"complete-payload"


def test_preprovisioned_coordination_lock_survives_restart_on_same_inode(tmp_path):
    lock_directory = tmp_path / "coordination"
    lock_directory.mkdir(mode=0o750)
    lock_directory.chmod(0o750)
    lock_path = lock_directory / "coordination.lock"
    lock_path.touch(mode=0o660)
    lock_path.chmod(0o660)
    expected_inode = lock_path.stat().st_ino
    lock_group = grp.getgrgid(os.getegid()).gr_name

    writer = _ledger(
        tmp_path,
        coordination_lock_path=lock_path,
        coordination_lock_mode=0o660,
        coordination_lock_owner_uid=os.geteuid(),
        coordination_lock_group=lock_group,
    )
    writer.append({"kind": "deployment_initialized"})
    restarted = _ledger(
        tmp_path,
        coordination_lock_path=lock_path,
        coordination_lock_mode=0o660,
        coordination_lock_owner_uid=os.geteuid(),
        coordination_lock_group=lock_group,
    )

    assert lock_path.stat().st_ino == expected_inode
    assert restarted.read()[0]["event"]["kind"] == "deployment_initialized"


def test_projection_uses_configured_owner_not_reader_euid(tmp_path, monkeypatch):
    writer = _ledger(tmp_path)
    writer.append({"kind": "deployment_initialized"})
    projection = SignedEventLedger(
        directory=tmp_path / "ledger",
        verification_keys={"control-1": _public(CONTROL_SEED)},
        event_permissions={"release-control": CONTROL_KINDS},
        domain_keys={"release-control": frozenset({"control-1"})},
        ledger_role="release-control",
        coordination_root=tmp_path,
        root_owner_uid=os.geteuid(),
        directory_owner_uid=os.geteuid(),
    )
    monkeypatch.setattr(
        "trustforge.signed_event_ledger.os.geteuid", lambda: os.getuid() + 10_000
    )
    assert projection.read()[0]["event"]["kind"] == "deployment_initialized"
    with pytest.raises(LedgerError, match="writer ownership"):
        writer.append({"kind": "operator_stop"})


def test_created_files_ignore_restrictive_umask_and_use_exact_mode(tmp_path):
    previous = os.umask(0o077)
    try:
        writer = _ledger(tmp_path)
        initialized = writer.append({"kind": "deployment_initialized"})
        writer.trip_epoch_stop(
            ledger_id=initialized["ledger_id"], canary_epoch="f" * 64
        )
    finally:
        os.umask(previous)
    for path in (tmp_path / "ledger").iterdir():
        if path.is_file():
            assert path.stat().st_mode & 0o777 == 0o600


def test_split_mode_files_are_exact_0640_under_umask_0077(tmp_path):
    group = grp.getgrgid(os.getegid()).gr_name
    tmp_path.chmod(0o750)
    directory = tmp_path / "ledger"
    directory.mkdir(mode=0o750)
    directory.chmod(0o750)
    previous = os.umask(0o077)
    try:
        writer = _ledger(
            tmp_path,
            root_group=group,
            root_mode=0o750,
            directory_group=group,
            directory_mode=0o750,
            file_mode=0o640,
        )
        initialized = writer.append({"kind": "deployment_initialized"})
        writer.trip_epoch_stop(
            ledger_id=initialized["ledger_id"], canary_epoch="e" * 64
        )
    finally:
        os.umask(previous)
    assert {
        path.stat().st_mode & 0o777 for path in directory.iterdir() if path.is_file()
    } == {0o640}


def test_bootstrap_rejects_any_partial_preprovisioned_content(tmp_path):
    directory = tmp_path / "ledger"
    directory.mkdir()
    directory.chmod(0o700)
    (directory / "unexpected.partial").touch()
    with pytest.raises(LedgerError, match="partially provisioned"):
        _ledger(tmp_path)
    assert not (directory / "bootstrap.json").exists()
