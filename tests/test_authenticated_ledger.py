from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from trustforge.authenticated_ledger import (
    AuthenticatedLedger,
    LedgerError,
    LedgerLimitError,
    NonceAlreadyConsumed,
)

KEY = b"k" * 32


def _ledger(tmp_path: Path, **kwargs) -> AuthenticatedLedger:
    return AuthenticatedLedger(
        keyring={"current": KEY},
        active_key_id="current",
        test_directory_override=tmp_path / "ledger",
        **kwargs,
    )


def _append_in_process(directory: str, start: int, count: int) -> None:
    ledger = AuthenticatedLedger(
        keyring={"current": KEY},
        active_key_id="current",
        test_directory_override=directory,
    )
    for index in range(start, start + count):
        ledger.append({"kind": "worker", "nonce": f"p-{index}"})


def test_append_restart_and_permissions(tmp_path):
    ledger = _ledger(tmp_path)
    first = ledger.append({"kind": "authorize", "nonce": "one"})
    second = _ledger(tmp_path).append({"kind": "audit", "nonce": "two"})
    assert first["sequence"] == 1
    assert second["previous_hash"] == first["event_hash"]
    assert [r["event"]["nonce"] for r in _ledger(tmp_path).read()] == ["one", "two"]
    assert (tmp_path / "ledger").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "ledger" / "events.jsonl").stat().st_mode & 0o777 == 0o600


def test_conditional_append_rejects_stale_head_atomically(tmp_path):
    ledger = _ledger(tmp_path)
    first = ledger.append({"kind": "first"})
    ledger.append({"kind": "second"}, expected_head=first["event_hash"])
    with pytest.raises(LedgerError, match="head changed"):
        ledger.append({"kind": "stale"}, expected_head=first["event_hash"])
    assert [item["event"]["kind"] for item in ledger.read()] == ["first", "second"]


def test_emergency_stop_latch_is_authenticated_durable_and_one_way(tmp_path):
    ledger = _ledger(tmp_path)
    first = ledger.append({"kind": "initialize"})
    ledger_id = first["ledger_id"]
    ledger.trip_emergency_stop(
        ledger_id=ledger_id,
        reason="candidate_outcome_unrecordable",
    )
    assert _ledger(tmp_path).emergency_stopped(ledger_id=ledger_id) is True
    latch = tmp_path / "ledger" / "emergency-stop.json"
    forged = latch.read_bytes().replace(
        b"candidate_outcome_unrecordable", b"operator_emergency_stop"
    )
    latch.write_bytes(forged)
    with pytest.raises(LedgerError, match="authentication"):
        ledger.emergency_stopped(ledger_id=ledger_id)


def test_nonce_is_global_and_key_rotation_verifies_old_records(tmp_path):
    _ledger(tmp_path).append({"kind": "a", "nonce": "same"})
    rotated = AuthenticatedLedger(
        keyring={"current": KEY, "new": b"n" * 32},
        active_key_id="new",
        test_directory_override=tmp_path / "ledger",
    )
    with pytest.raises(NonceAlreadyConsumed):
        rotated.append({"kind": "different", "nonce": "same"})
    rotated.append({"kind": "b", "nonce": "fresh"})
    records = rotated.read()
    assert [record["key_id"] for record in records] == ["current", "new"]


@pytest.mark.parametrize("mutation", ["content", "truncate", "tail-delete", "reorder", "corrupt"])
def test_tampering_fails_closed(tmp_path, mutation):
    ledger = _ledger(tmp_path)
    ledger.append({"kind": "a", "nonce": "1"})
    ledger.append({"kind": "b", "nonce": "2"})
    path = tmp_path / "ledger" / "events.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    if mutation == "content":
        lines[0] = lines[0].replace(b'"kind":"a"', b'"kind":"x"')
    elif mutation == "truncate":
        lines[-1] = lines[-1][:-2]
    elif mutation == "tail-delete":
        lines.pop()
    elif mutation == "reorder":
        lines.reverse()
    else:
        lines[0] = b"{garbage}\n"
    path.write_bytes(b"".join(lines))
    with pytest.raises(LedgerError):
        ledger.read()
    with pytest.raises(LedgerError):
        ledger.append({"kind": "c", "nonce": "3"})


def test_symlink_and_unsafe_file_mode_are_rejected(tmp_path):
    directory = tmp_path / "ledger"
    directory.mkdir(mode=0o700)
    target = tmp_path / "target"
    target.write_text("")
    (directory / "events.jsonl").symlink_to(target)
    with pytest.raises(LedgerError):
        _ledger(tmp_path).read()
    (directory / "events.jsonl").unlink()
    (directory / "events.jsonl").write_text("")
    os.chmod(directory / "events.jsonl", 0o644)
    with pytest.raises(LedgerError, match="permissions"):
        _ledger(tmp_path).read()


def test_bounds_apply_to_event_count_event_and_file(tmp_path):
    with pytest.raises(LedgerLimitError):
        _ledger(tmp_path, max_event_bytes=8).append({"too": "large"})
    count_limited = _ledger(tmp_path / "count", max_events=1)
    count_limited.append({"n": 1})
    with pytest.raises(LedgerLimitError):
        count_limited.append({"n": 2})
    with pytest.raises(LedgerLimitError):
        _ledger(tmp_path / "file", max_file_bytes=10).append({"n": 1})


def test_crash_between_ledger_and_head_fsync_fails_closed(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    ledger.append({"nonce": "before"})

    def crash_before_checkpoint(*_args):
        raise OSError("simulated power loss")

    monkeypatch.setattr(ledger, "_write_head", crash_before_checkpoint)
    with pytest.raises(OSError, match="power loss"):
        ledger.append({"nonce": "uncheckpointed"})
    with pytest.raises(LedgerError, match="head"):
        _ledger(tmp_path).read()


def test_existing_broad_directory_is_not_silently_repaired(tmp_path):
    directory = tmp_path / "ledger"
    directory.mkdir(mode=0o755)
    os.chmod(directory, 0o755)
    with pytest.raises(LedgerError, match="permissions"):
        AuthenticatedLedger(
            keyring={"current": KEY},
            active_key_id="current",
            test_directory_override=directory,
        )
    assert directory.stat().st_mode & 0o777 == 0o755


@pytest.mark.subprocess
def test_multiprocess_append_is_serialized(tmp_path):
    directory = str(tmp_path / "ledger")
    processes = [
        multiprocessing.Process(target=_append_in_process, args=(directory, i * 12, 12))
        for i in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    records = AuthenticatedLedger(
        keyring={"current": KEY},
        active_key_id="current",
        test_directory_override=directory,
    ).read()
    assert len(records) == 48
    assert [record["sequence"] for record in records] == list(range(1, 49))
    assert len({record["event"]["nonce"] for record in records}) == 48
