from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from trustforge.evidence_transaction_store import (
    CANONICAL_NAME,
    COMMIT_SCHEMA,
    STATE_NAME,
    STAGING_SCHEMA,
    TOMBSTONE_NAME,
    EvidenceTransactionError,
    EvidenceTransactionStore,
)


def _store(tmp_path: Path, hook=None) -> EvidenceTransactionStore:
    tmp_path.chmod(0o700)
    return EvidenceTransactionStore(tmp_path, expected_uid=os.getuid(), fault_hook=hook)


def _publish_worker(directory: str, payload: bytes, barrier, queue) -> None:
    barrier.wait(timeout=10)
    try:
        result = EvidenceTransactionStore(
            Path(directory), expected_uid=os.getuid()
        )._publish(payload)
        queue.put(("eligible", result.digest))
    except Exception as exc:  # pragma: no cover - asserted in parent process
        queue.put(("error", str(exc)))


class _ReplaceStateHook:
    def __init__(self, directory: str, entered, release) -> None:
        self.directory = Path(directory)
        self.entered = entered
        self.release = release
        self.calls = 0

    def __call__(self, point: str) -> None:
        if point != "coordination:verify":
            return
        self.calls += 1
        if self.calls != 2:
            return
        state = self.directory / STATE_NAME
        state.rename(self.directory / ".attacker-replaced-state")
        replacement = os.open(state, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(replacement)
        self.entered.set()
        assert self.release.wait(timeout=10)


def _replace_state_worker(directory: str, entered, release, queue) -> None:
    hook = _ReplaceStateHook(directory, entered, release)
    try:
        EvidenceTransactionStore(
            Path(directory), expected_uid=os.getuid(), fault_hook=hook
        )._publish(b"outer")
        queue.put(("eligible", "outer"))
    except Exception as exc:  # pragma: no cover - asserted in parent process
        queue.put(("error", str(exc)))


def test_success_is_single_root_equivalent_0600_canonical_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = store._publish(b'{"decision":"PASS"}\n')
    assert result.eligible
    assert result.payload == b'{"decision":"PASS"}\n'
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        STATE_NAME,
        CANONICAL_NAME,
    ]
    metadata = (tmp_path / CANONICAL_NAME).stat()
    assert metadata.st_uid == os.getuid()
    assert metadata.st_nlink == 1
    assert metadata.st_mode & 0o777 == 0o600
    state_metadata = (tmp_path / STATE_NAME).stat()
    assert state_metadata.st_uid == os.getuid()
    assert state_metadata.st_nlink == 1
    assert state_metadata.st_mode & 0o777 == 0o600


def test_staging_wire_format_is_intrinsically_ineligible(tmp_path: Path) -> None:
    stage = {
        "schema": STAGING_SCHEMA,
        "state": "INELIGIBLE",
        "transaction_id": "x",
        "evidence_digest": "sha256:" + "0" * 64,
        "opaque_payload_b64": "eyJkZWNpc2lvbiI6IlBBU1MifQ==",
    }
    path = tmp_path / ".evidence-x.stage"
    path.write_text(json.dumps(stage, sort_keys=True, separators=(",", ":")) + "\n")
    path.chmod(0o600)
    result = _store(tmp_path).eligibility()
    assert not result.eligible
    assert result.payload is None


def test_residual_prepared_name_is_an_eligibility_guard(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store._publish(b"PASS").eligible
    prepared = tmp_path / ".evidence-stale.prepared"
    prepared.write_text("{}\n")
    prepared.chmod(0o600)
    assert not store.eligibility().eligible


def test_exact_retry_idempotent_and_different_evidence_immutable(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = store._publish(b"same")
    before = (tmp_path / CANONICAL_NAME).read_bytes()
    assert store._publish(b"same") == first
    with pytest.raises(EvidenceTransactionError):
        store._publish(b"different")
    assert (tmp_path / CANONICAL_NAME).read_bytes() == before
    assert store.eligibility().eligible
    assert not (tmp_path / TOMBSTONE_NAME).exists()


@pytest.mark.parametrize(
    "point",
    [
        "staging:open",
        "staging:write",
        "staging:fsync",
        "staging:dir-fsync",
        "prepared:open",
        "prepared:write",
        "prepared:fsync",
        "prepared:dir-fsync",
        "canonical:link",
        "canonical:dir-fsync",
        "prepared:unlink",
        "prepared:cleanup-dir-fsync",
        "staging:unlink",
        "cleanup:dir-fsync",
    ],
)
def test_every_primary_fault_returns_non_success_without_eligible_pass(
    tmp_path: Path, point: str
) -> None:
    def fail(candidate: str) -> None:
        if candidate == point:
            raise OSError(f"permanent {point}")

    store = _store(tmp_path, fail)
    with pytest.raises(EvidenceTransactionError):
        store._publish(b"PASS")
    assert not _store(tmp_path).eligibility().eligible


@pytest.mark.parametrize(
    "point",
    [
        "state:begin:write",
        "state:begin:fsync",
        "state:commit:write",
        "state:commit:fsync",
        "state:abort:write",
        "state:abort:fsync",
    ],
)
def test_terminal_state_faults_fail_closed_across_restart(
    tmp_path: Path, point: str
) -> None:
    def fail(candidate: str) -> None:
        if candidate == point:
            raise OSError(f"permanent {point}")
        if point.startswith("state:abort") and candidate == "cleanup:dir-fsync":
            raise OSError("force abort path")

    with pytest.raises(EvidenceTransactionError):
        _store(tmp_path, fail)._publish(b"PASS")
    assert not _store(tmp_path).eligibility().eligible
    assert not _store(tmp_path).recover().eligible


@pytest.mark.parametrize(
    "point",
    [
        "staging:write",
        "prepared:write",
        "canonical:link",
        "prepared:unlink",
        "staging:unlink",
        "cleanup:dir-fsync",
    ],
)
def test_transient_fault_is_failed_then_exact_retry_remains_fail_closed(
    tmp_path: Path, point: str
) -> None:
    calls = 0

    def fail_once(candidate: str) -> None:
        nonlocal calls
        if candidate == point and calls == 0:
            calls += 1
            raise OSError(f"transient {point}")

    with pytest.raises(EvidenceTransactionError):
        _store(tmp_path, fail_once)._publish(b"PASS")
    assert not _store(tmp_path).eligibility().eligible
    if point in {"staging:write", "prepared:write", "canonical:link"}:
        assert _store(tmp_path)._publish(b"PASS").eligible
    else:
        with pytest.raises(EvidenceTransactionError, match="tombstoned"):
            _store(tmp_path)._publish(b"PASS")


def test_short_write_is_retried(monkeypatch, tmp_path: Path) -> None:
    real_write = os.write
    shortened = False

    def short_write(fd: int, raw) -> int:
        nonlocal shortened
        if not shortened and len(raw) > 1:
            shortened = True
            return real_write(fd, raw[:1])
        return real_write(fd, raw)

    monkeypatch.setattr(os, "write", short_write)
    assert _store(tmp_path)._publish(b"PASS").eligible
    assert shortened


def test_zero_progress_write_tombstones(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(os, "write", lambda _fd, _raw: 0)
    with pytest.raises(EvidenceTransactionError):
        _store(tmp_path)._publish(b"PASS")
    # Tombstone writing also cannot progress; importantly no canonical PASS exists.
    assert not (tmp_path / CANONICAL_NAME).exists()


def test_permanent_pending_unlink_failure_tombstones_visible_marker(
    tmp_path: Path,
) -> None:
    def fail(point: str) -> None:
        if point in {"staging:unlink", "failure:staging-unlink"}:
            raise OSError("permanent unlink")

    with pytest.raises(EvidenceTransactionError):
        _store(tmp_path, fail)._publish(b"PASS")
    assert (tmp_path / CANONICAL_NAME).exists()
    assert (tmp_path / TOMBSTONE_NAME).exists()
    assert not _store(tmp_path).eligibility().eligible


def test_tombstone_failure_rolls_back_canonical_marker(tmp_path: Path) -> None:
    def fail(point: str) -> None:
        if point == "canonical:dir-fsync" or point.startswith("tombstone:"):
            raise OSError("injected")

    with pytest.raises(EvidenceTransactionError):
        _store(tmp_path, fail)._publish(b"PASS")
    assert not (tmp_path / CANONICAL_NAME).exists()
    assert not _store(tmp_path).eligibility().eligible


@pytest.mark.parametrize(
    "tombstone_point",
    ["tombstone:open", "tombstone:write", "tombstone:fsync", "tombstone:dir-fsync"],
)
def test_permanent_tombstone_and_rollback_failure_preserves_ineligible_guard(
    tmp_path: Path, tombstone_point: str
) -> None:
    def fail(point: str) -> None:
        if point in {
            "canonical:dir-fsync",
            tombstone_point,
            "failure:canonical-unlink",
        }:
            raise OSError("permanent combined failure")

    with pytest.raises(EvidenceTransactionError):
        _store(tmp_path, fail)._publish(b"PASS")
    assert (tmp_path / CANONICAL_NAME).exists()
    assert any(path.name.endswith(".stage") for path in tmp_path.iterdir())
    assert not _store(tmp_path).eligibility().eligible
    assert not _store(tmp_path).recover().eligible


@pytest.mark.parametrize(
    ("tombstone_point", "rollback_point"),
    [
        (tombstone, rollback)
        for tombstone in (
            "tombstone:open",
            "tombstone:write",
            "tombstone:fsync",
            "tombstone:dir-fsync",
        )
        for rollback in (
            "failure:canonical-unlink",
            "failure:canonical-dir-fsync",
        )
    ],
)
def test_post_staging_unlink_fault_matrix_stays_nonpass_after_restart(
    tmp_path: Path, tombstone_point: str, rollback_point: str
) -> None:
    """Harper P0: durable BEGIN must veto PASS after the staging guard is gone."""

    def fail(point: str) -> None:
        if point in {
            "cleanup:dir-fsync",
            tombstone_point,
            rollback_point,
            "state:abort:write",
        }:
            raise OSError("permanent compound failure")

    with pytest.raises(EvidenceTransactionError):
        _store(tmp_path, fail)._publish(b"PASS")
    assert not any(path.name.endswith(".stage") for path in tmp_path.iterdir())
    assert not _store(tmp_path).eligibility().eligible
    assert not _store(tmp_path).recover().eligible


@pytest.mark.parametrize("same_payload", [True, False])
def test_multiprocess_publish_is_serialized_and_loser_cannot_revoke_winner(
    tmp_path: Path, same_payload: bool
) -> None:
    tmp_path.chmod(0o700)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    payloads = (b"winner", b"winner" if same_payload else b"loser")
    processes = [
        context.Process(
            target=_publish_worker,
            args=(str(tmp_path), payload, barrier, queue),
        )
        for payload in payloads
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    outcomes = [queue.get(timeout=5) for _ in processes]
    eligible = _store(tmp_path).eligibility()
    assert eligible.eligible
    assert not (tmp_path / TOMBSTONE_NAME).exists()
    if same_payload:
        assert [outcome[0] for outcome in outcomes] == ["eligible", "eligible"]
        assert eligible.payload == b"winner"
    else:
        assert sorted(outcome[0] for outcome in outcomes) == ["eligible", "error"]
        assert eligible.payload in payloads


def test_state_inode_replace_blocks_nested_writer_and_cannot_revoke_winner(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release = context.Event()
    queue = context.Queue()
    outer = context.Process(
        target=_replace_state_worker,
        args=(str(tmp_path), entered, release, queue),
    )
    outer.start()
    assert entered.wait(timeout=10)
    winner_barrier = context.Barrier(1)
    winner = context.Process(
        target=_publish_worker,
        args=(str(tmp_path), b"winner", winner_barrier, queue),
    )
    winner.start()
    winner.join(timeout=0.3)
    assert winner.is_alive(), "directory coordination lock did not serialize writer"
    release.set()
    outer.join(timeout=10)
    winner.join(timeout=10)
    assert outer.exitcode == 0
    assert winner.exitcode == 0
    outcomes = [queue.get(timeout=5), queue.get(timeout=5)]
    assert sorted(outcome[0] for outcome in outcomes) == ["eligible", "error"]
    result = _store(tmp_path).eligibility()
    assert result.eligible
    assert result.payload == b"winner"
    assert not (tmp_path / TOMBSTONE_NAME).exists()


def test_crash_after_staging_then_restart_never_promotes(tmp_path: Path) -> None:
    class Crash(BaseException):
        pass

    def crash(point: str) -> None:
        if point == "prepared:open":
            raise Crash

    with pytest.raises(Crash):
        _store(tmp_path, crash)._publish(b"PASS")
    assert not _store(tmp_path).eligibility().eligible
    recovered = _store(tmp_path).recover()
    assert not recovered.eligible
    assert (tmp_path / TOMBSTONE_NAME).exists()


def test_crash_after_canonical_before_cleanup_is_tombstoned_on_recovery(
    tmp_path: Path,
) -> None:
    class Crash(BaseException):
        pass

    def crash(point: str) -> None:
        if point == "staging:unlink":
            raise Crash

    with pytest.raises(Crash):
        _store(tmp_path, crash)._publish(b"PASS")
    # A stale transaction guard revokes eligibility even before recovery.
    assert not _store(tmp_path).eligibility().eligible
    recovered = _store(tmp_path).recover()
    assert not recovered.eligible
    assert (tmp_path / TOMBSTONE_NAME).exists()


def test_recovery_cleanup_failure_remains_nonpass(tmp_path: Path) -> None:
    class Crash(BaseException):
        pass

    def crash(point: str) -> None:
        if point == "prepared:open":
            raise Crash

    with pytest.raises(Crash):
        _store(tmp_path, crash)._publish(b"PASS")

    def fail(point: str) -> None:
        if point == "recovery:staging-unlink":
            raise OSError("permanent recovery cleanup")

    assert not _store(tmp_path, fail).recover().eligible
    assert any(path.name.endswith(".stage") for path in tmp_path.iterdir())


def test_malformed_or_multilink_canonical_is_never_eligible(tmp_path: Path) -> None:
    canonical = tmp_path / CANONICAL_NAME
    canonical.write_text(
        json.dumps(
            {
                "schema": COMMIT_SCHEMA,
                "state": "ELIGIBLE",
                "transaction_id": "x",
                "evidence_digest": "sha256:" + "0" * 64,
                "payload_b64": "UEFTUw==",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    canonical.chmod(0o600)
    os.link(canonical, tmp_path / "second-name")
    assert not _store(tmp_path).eligibility().eligible


def test_unsafe_directory_is_rejected(tmp_path: Path) -> None:
    tmp_path.chmod(0o777)
    with pytest.raises(EvidenceTransactionError, match="directory metadata"):
        EvidenceTransactionStore(tmp_path, expected_uid=os.getuid())._publish(b"PASS")


def test_symlink_canonical_and_tombstone_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "attacker"
    target.write_text("{}")
    os.symlink(target, tmp_path / CANONICAL_NAME)
    assert not _store(tmp_path).eligibility().eligible
    (tmp_path / CANONICAL_NAME).unlink()
    os.symlink(target, tmp_path / TOMBSTONE_NAME)
    assert not _store(tmp_path).eligibility().eligible
