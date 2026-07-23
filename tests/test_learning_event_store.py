import multiprocessing
import os
import stat
import time
from pathlib import Path

import pytest

from trustforge.learning_event_contract import (
    LearningEventError,
    canonical_integrity_checksum,
    make_learning_event,
    serialize_learning_event,
)
from trustforge.learning_event_store import (
    FileLearningEventStore,
    LearningEventAppendLog,
    default_learning_event_directory,
    plan_learning_event_migration,
)


TIMES = {
    "event_time": "2026-07-01T00:00:00Z",
    "available_time": "2026-07-01T01:00:00Z",
    "as_of_time": "2026-07-01T01:00:00Z",
}
SOURCE_RECORD = {"record_id": "storage-fixture"}
PROVENANCE = {
    "source": "fixture",
    "collector": "storage-test",
    "observed_at": "2026-07-01T01:00:00.000000Z",
    "tenant_id": "tenant-a",
    "source_record": SOURCE_RECORD,
    "version": "fixture.v1",
    "checksum": canonical_integrity_checksum(SOURCE_RECORD),
}


def _outcome(revision=1, status="pending"):
    return make_learning_event(
        kind="delayed_outcome",
        tenant_id="tenant-a",
        entity_id="outcome:an-1:T+1",
        revision=revision,
        provenance=PROVENANCE,
        payload={"outcome_id": "out-1", "analysis_id": "an-1", "horizon": "T+1", "status": status},
        **TIMES,
    )


def _evidence(identity="evidence:1", tenant_id="tenant-a"):
    provenance = dict(PROVENANCE)
    provenance["tenant_id"] = tenant_id
    return make_learning_event(
        kind="evidentiary",
        tenant_id=tenant_id,
        entity_id=identity,
        revision=1,
        provenance=provenance,
        payload={"evidence_id": "ev-1", "claim": "btc up", "source_url": "https://example.test"},
        **TIMES,
    )


def test_append_log_is_idempotent_and_replay_stable():
    log = LearningEventAppendLog()
    event = _evidence()

    assert log.append(event) == "created"
    assert log.append(event) == "idempotent"
    assert log.replay() == [event]
    assert log.snapshot() == (serialize_learning_event(event),)


def test_append_log_rejects_in_place_rewrite_but_allows_revision_identity():
    log = LearningEventAppendLog()
    original = _outcome(status="pending")
    rewritten = _outcome(status="labeled")
    revision = _outcome(revision=2, status="labeled")

    assert log.append(original) == "created"
    with pytest.raises(LearningEventError, match="immutable"):
        log.append(rewritten)
    assert log.append(revision) == "created"


def test_migration_plan_dry_run_validates_without_writes_and_replays_duplicates():
    event = serialize_learning_event(_evidence())
    report = plan_learning_event_migration([event, event], dry_run=True)

    assert report["status"] == "ready"
    assert report["dry_run"] is True
    assert report["will_write"] is False
    assert [item["result"] for item in report["results"]] == ["created", "idempotent"]


def test_migration_plan_unknown_schema_fails_closed():
    raw = serialize_learning_event(_evidence()).replace("learning-event.v1", "learning-event.v999")

    report = plan_learning_event_migration([raw], dry_run=True)

    assert report["status"] == "blocked"
    assert report["will_write"] is False
    assert "schema version" in report["reason"]


def test_outcome_cannot_be_migrated_as_evidence():
    raw = serialize_learning_event(_outcome()).replace("delayed_outcome", "evidentiary")

    report = plan_learning_event_migration([raw], dry_run=True)

    assert report["status"] == "blocked"
    assert report["will_write"] is False


def test_file_store_defaults_to_portable_trustforge_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_HOME", str(tmp_path))

    assert default_learning_event_directory() == tmp_path / "out" / "learning_events"


def test_file_store_missing_nested_directory_replays_empty_without_creating_it(tmp_path):
    directory = tmp_path / "not-created" / "events"

    assert FileLearningEventStore(directory).replay(trusted_tenant_id="tenant-a") == []
    assert not directory.exists()


def test_file_store_persists_idempotently_and_replays_after_restart(tmp_path):
    event = _evidence()
    store = FileLearningEventStore(tmp_path)

    assert store.append(event) == "created"
    assert store.append(event) == "idempotent"
    assert FileLearningEventStore(tmp_path).replay(trusted_tenant_id="tenant-a") == [event]
    names = [path.name for path in tmp_path.iterdir()]
    assert len(names) == 1
    assert names[0].endswith(".json")
    assert "tenant-a" not in names[0]
    assert "evidence" not in names[0]


def test_file_store_replay_is_deterministic_by_identity_digest(tmp_path):
    events = [_evidence("evidence:3"), _evidence("evidence:1"), _evidence("evidence:2")]
    store = FileLearningEventStore(tmp_path)
    for event in events:
        store.append(event)

    replayed = store.replay(trusted_tenant_id="tenant-a")
    expected = sorted(events, key=lambda event: store._path_for_identity(event.identity).name)
    assert replayed == expected
    assert FileLearningEventStore(tmp_path).snapshot(trusted_tenant_id="tenant-a") == tuple(
        serialize_learning_event(event) for event in expected
    )


def test_file_store_rejects_same_identity_with_different_bytes(tmp_path):
    store = FileLearningEventStore(tmp_path)
    original = _outcome(status="pending")
    rewritten = _outcome(status="labeled")

    assert store.append(original) == "created"
    with pytest.raises(LearningEventError, match="immutable"):
        store.append(rewritten)
    assert store.replay(trusted_tenant_id="tenant-a") == [original]


@pytest.mark.parametrize("replacement", [b"{", b"not-json"])
def test_file_store_replay_fails_closed_for_corrupt_or_truncated_event(tmp_path, replacement):
    event = _evidence()
    store = FileLearningEventStore(tmp_path)
    store.append(event)
    next(tmp_path.iterdir()).write_bytes(replacement)

    with pytest.raises(LearningEventError, match="corrupt"):
        store.replay(trusted_tenant_id="tenant-a")


def test_file_store_replay_fails_closed_for_oversize_event(tmp_path):
    event = _evidence()
    writer = FileLearningEventStore(tmp_path)
    writer.append(event)

    with pytest.raises(LearningEventError, match="unsafe or unreadable"):
        FileLearningEventStore(tmp_path, maximum_event_bytes=8).replay(
            trusted_tenant_id="tenant-a"
        )


def test_file_store_replay_fails_closed_for_digest_mismatch(tmp_path):
    event = _evidence()
    store = FileLearningEventStore(tmp_path)
    store.append(event)
    path = next(tmp_path.iterdir())
    path.rename(tmp_path / ("0" * 64 + ".json"))

    with pytest.raises(LearningEventError, match="digest"):
        store.replay(trusted_tenant_id="tenant-a")


@pytest.mark.parametrize("entry_kind", ["symlink", "directory", "fifo"])
def test_file_store_replay_fails_closed_for_non_regular_entry(tmp_path, entry_kind):
    name = "0" * 64 + ".json"
    path = tmp_path / name
    if entry_kind == "symlink":
        store = FileLearningEventStore(tmp_path)
        store.staging_directory.mkdir()
        target = store.staging_directory / "target"
        target.write_text("{}", encoding="utf-8")
        path.symlink_to(target)
    elif entry_kind == "directory":
        path.mkdir()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO is unavailable")
        os.mkfifo(path)

    with pytest.raises(LearningEventError, match="unsafe or unreadable"):
        FileLearningEventStore(tmp_path).replay(trusted_tenant_id="tenant-a")


def _append_in_process(directory: str, result_directory: str, start, index: int) -> None:
    start.wait()
    try:
        result = FileLearningEventStore(Path(directory)).append(_evidence())
    except BaseException as exc:  # pragma: no cover - surfaced in parent assertion
        result = f"error:{type(exc).__name__}:{exc}"
    (Path(result_directory) / str(index)).write_text(result, encoding="utf-8")


def _winner_fails_destination_fsync(
    directory: str,
    result_path: str,
    linked,
    release,
) -> None:
    import trustforge.safe_fs as safe_fs

    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    destination_info = destination.stat()
    real_fsync = safe_fs.os.fsync
    failed = False

    def block_then_fail(descriptor):
        nonlocal failed
        info = os.fstat(descriptor)
        if (
            not failed
            and stat.S_ISDIR(info.st_mode)
            and (info.st_dev, info.st_ino)
            == (destination_info.st_dev, destination_info.st_ino)
        ):
            failed = True
            linked.set()
            if not release.wait(10):
                raise RuntimeError("test release timed out")
            raise OSError("injected destination fsync failure")
        return real_fsync(descriptor)

    safe_fs.os.fsync = block_then_fail
    try:
        result = FileLearningEventStore(destination).append(_evidence())
    except BaseException as exc:
        result = f"error:{type(exc).__name__}:{exc}"
    Path(result_path).write_text(result, encoding="utf-8")


def _replay_in_process(directory: str, result_path: str) -> None:
    try:
        events = FileLearningEventStore(Path(directory)).replay(
            trusted_tenant_id="tenant-a"
        )
        result = f"replay:{len(events)}"
    except BaseException as exc:
        result = f"error:{type(exc).__name__}:{exc}"
    Path(result_path).write_text(result, encoding="utf-8")


def _append_once_in_process(directory: str, result_path: str) -> None:
    try:
        result = FileLearningEventStore(Path(directory)).append(_evidence())
    except BaseException as exc:
        result = f"error:{type(exc).__name__}:{exc}"
    Path(result_path).write_text(result, encoding="utf-8")


def _hold_exclusive_lock(directory: str, ready, release) -> None:
    store = FileLearningEventStore(Path(directory))
    with store._store_lock(exclusive=True):
        ready.set()
        if not release.wait(10):
            raise RuntimeError("test release timed out")


def _acquire_lock_then_crash(directory: str, ready) -> None:
    store = FileLearningEventStore(Path(directory))
    with store._store_lock(exclusive=True):
        ready.set()
        os._exit(0)


def test_file_store_multi_process_race_publishes_exactly_once(tmp_path):
    context = multiprocessing.get_context("spawn")
    event_directory = tmp_path / "events"
    result_directory = tmp_path / "results"
    result_directory.mkdir()
    start = context.Event()
    processes = [
        context.Process(
            target=_append_in_process,
            args=(str(event_directory), str(result_directory), start, index),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in processes)
    results = [(result_directory / str(index)).read_text(encoding="utf-8") for index in range(2)]
    assert sorted(results) == ["created", "idempotent"]
    assert FileLearningEventStore(event_directory).replay(
        trusted_tenant_id="tenant-a"
    ) == [_evidence()]


def test_cross_process_lock_prevents_visible_uncommitted_ghost_success(tmp_path):
    context = multiprocessing.get_context("spawn")
    event_directory = tmp_path / "events"
    results = tmp_path / "results"
    results.mkdir()
    linked = context.Event()
    release = context.Event()
    winner = context.Process(
        target=_winner_fails_destination_fsync,
        args=(str(event_directory), str(results / "winner"), linked, release),
    )
    winner.start()
    assert linked.wait(10), "winner never reached visible pre-commit link"

    loser = context.Process(
        target=_append_once_in_process,
        args=(str(event_directory), str(results / "loser")),
    )
    reader = context.Process(
        target=_replay_in_process,
        args=(str(event_directory), str(results / "reader")),
    )
    loser.start()
    reader.start()
    time.sleep(0.2)
    assert not (results / "loser").exists()
    assert not (results / "reader").exists()

    release.set()
    for process in (winner, loser, reader):
        process.join(timeout=10)
        assert process.exitcode == 0

    assert (results / "winner").read_text(encoding="utf-8").startswith("error:OSError:")
    assert (results / "loser").read_text(encoding="utf-8") == "created"
    assert (results / "reader").read_text(encoding="utf-8") in {"replay:0", "replay:1"}
    assert FileLearningEventStore(event_directory).replay(
        trusted_tenant_id="tenant-a"
    ) == [_evidence()]


def test_cross_process_lock_timeout_fails_closed(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_exclusive_lock,
        args=(str(tmp_path), ready, release),
    )
    holder.start()
    assert ready.wait(10)

    try:
        with pytest.raises(LearningEventError, match="lock timed out"):
            FileLearningEventStore(tmp_path, lock_timeout_seconds=0.05).replay(
                trusted_tenant_id="tenant-a"
            )
    finally:
        release.set()
        holder.join(timeout=10)
    assert holder.exitcode == 0


def test_process_crash_releases_store_lock_without_stale_owner(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(
        target=_acquire_lock_then_crash,
        args=(str(tmp_path), ready),
    )
    process.start()
    assert ready.wait(10)
    process.join(timeout=10)
    assert process.exitcode == 0

    assert FileLearningEventStore(tmp_path, lock_timeout_seconds=0.2).replay(
        trusted_tenant_id="tenant-a"
    ) == []


def test_shared_store_locks_can_coexist(tmp_path):
    first = FileLearningEventStore(tmp_path)
    second = FileLearningEventStore(tmp_path)

    with first._store_lock(exclusive=False):
        with second._store_lock(exclusive=False):
            pass


def test_file_store_fsync_failure_leaves_no_ghost_event(tmp_path, monkeypatch):
    def fail_write(*args, **kwargs):
        raise OSError("fsync failed")

    monkeypatch.setattr(
        "trustforge.learning_event_store.write_immutable_cross_directory_at",
        fail_write,
    )
    store = FileLearningEventStore(tmp_path)

    with pytest.raises(OSError, match="fsync failed"):
        store.append(_evidence())
    assert store.replay(trusted_tenant_id="tenant-a") == []


def test_file_store_rejects_empty_symlink_directory(tmp_path):
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(LearningEventError, match="opened safely"):
        FileLearningEventStore(linked_directory).replay(trusted_tenant_id="tenant-a")


def test_file_store_append_stays_on_pinned_parent_when_path_is_swapped(tmp_path, monkeypatch):
    output = tmp_path / "events"
    moved = tmp_path / "pinned-events"
    attacker = tmp_path / "attacker"
    output.mkdir()
    attacker.mkdir()
    real_open = os.open
    swapped = False

    def swap_before_temp_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and dir_fd is not None and isinstance(path, str) and path.endswith(".tmp"):
            os.rename(output, moved)
            os.symlink(attacker, output, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("trustforge.safe_fs.os.open", swap_before_temp_open)
    assert FileLearningEventStore(output).append(_evidence()) == "created"
    assert swapped
    assert len(list(moved.iterdir())) == 1
    assert list(attacker.iterdir()) == []


def test_file_store_requires_explicit_nonempty_trusted_tenant(tmp_path):
    store = FileLearningEventStore(tmp_path)

    with pytest.raises(TypeError):
        store.replay()
    with pytest.raises(TypeError):
        store.snapshot()
    with pytest.raises(LearningEventError, match="trusted_tenant_id"):
        store.replay(trusted_tenant_id=" ")


def test_file_store_replay_and_snapshot_are_tenant_scoped(tmp_path):
    store = FileLearningEventStore(tmp_path)
    tenant_a = _evidence("evidence:a", "tenant-a")
    tenant_b = _evidence("evidence:b", "tenant-b")
    store.append(tenant_b)
    store.append(tenant_a)

    assert store.replay(trusted_tenant_id="tenant-a") == [tenant_a]
    assert store.replay(trusted_tenant_id="tenant-b") == [tenant_b]
    assert store.snapshot(trusted_tenant_id="tenant-a") == (
        serialize_learning_event(tenant_a),
    )


def test_other_tenant_corruption_still_fails_closed(tmp_path):
    store = FileLearningEventStore(tmp_path)
    tenant_b = _evidence("evidence:b", "tenant-b")
    store.append(tenant_b)
    store._path_for_identity(tenant_b.identity).write_bytes(b"{")

    with pytest.raises(LearningEventError, match="corrupt"):
        store.replay(trusted_tenant_id="tenant-a")


def test_file_store_event_count_limit_stops_before_unbounded_collection(tmp_path, monkeypatch):
    writer = FileLearningEventStore(tmp_path)
    writer.append(_evidence("evidence:1"))
    writer.append(_evidence("evidence:2"))
    reads = 0

    def reject_read(*args, **kwargs):
        nonlocal reads
        reads += 1
        raise AssertionError("count limit must fail before reading event bytes")

    monkeypatch.setattr("trustforge.learning_event_store.read_regular_file_at", reject_read)
    with pytest.raises(LearningEventError, match="event count"):
        FileLearningEventStore(tmp_path, maximum_event_count=1).replay(
            trusted_tenant_id="tenant-a"
        )
    assert reads == 0


def test_file_store_total_size_limit_fails_before_event_reads(tmp_path, monkeypatch):
    writer = FileLearningEventStore(tmp_path)
    writer.append(_evidence())
    reads = 0

    def reject_read(*args, **kwargs):
        nonlocal reads
        reads += 1
        raise AssertionError("total limit must fail before reading event bytes")

    monkeypatch.setattr("trustforge.learning_event_store.read_regular_file_at", reject_read)
    with pytest.raises(LearningEventError, match="total size"):
        FileLearningEventStore(tmp_path, maximum_total_bytes=1).replay(
            trusted_tenant_id="tenant-a"
        )
    assert reads == 0


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("maximum_event_bytes", 0),
        ("maximum_event_count", 0),
        ("maximum_total_bytes", 0),
        ("maximum_event_count", True),
    ],
)
def test_file_store_rejects_invalid_resource_limits(tmp_path, option, value):
    with pytest.raises(ValueError, match=option):
        FileLearningEventStore(tmp_path, **{option: value})


def test_staging_entries_are_isolated_but_event_namespace_tmp_fails_closed(tmp_path):
    store = FileLearningEventStore(tmp_path)
    event = _evidence()
    store.append(event)
    (store.staging_directory / ".attacker.tmp").write_bytes(b"attacker")

    assert store.replay(trusted_tenant_id="tenant-a") == [event]

    (tmp_path / ".attacker.tmp").write_bytes(b"attacker")
    with pytest.raises(LearningEventError, match="unexpected entry"):
        store.replay(trusted_tenant_id="tenant-a")


def test_exclusive_append_cleans_bounded_valid_crash_staging_entry(tmp_path):
    store = FileLearningEventStore(tmp_path)
    store.staging_directory.mkdir()
    event = _evidence()
    name = store._path_for_identity(event.identity).name
    leftover = store.staging_directory / f".{name}.{'a' * 24}.tmp"
    leftover.write_bytes(b"crash")

    assert store.append(event) == "created"
    assert list(store.staging_directory.iterdir()) == []


def test_exclusive_append_reconciles_two_link_crash_leftover(tmp_path):
    store = FileLearningEventStore(tmp_path / "events")
    store.directory.mkdir()
    store.staging_directory.mkdir()
    event = _evidence()
    name = store._path_for_identity(event.identity).name
    leftover = store.staging_directory / f".{name}.{'a' * 24}.tmp"
    leftover.write_bytes(serialize_learning_event(event).encode("utf-8"))
    os.link(leftover, store.directory / name)

    assert store.append(event) == "created"
    assert list(store.staging_directory.iterdir()) == []
    assert store.replay(trusted_tenant_id="tenant-a") == [event]


def test_staging_cleanup_limit_fails_closed(tmp_path):
    store = FileLearningEventStore(tmp_path, maximum_staging_entries=1)
    store.staging_directory.mkdir()
    name = store._path_for_identity(_evidence().identity).name
    for token in ("a" * 24, "b" * 24):
        (store.staging_directory / f".{name}.{token}.tmp").write_bytes(b"crash")

    with pytest.raises(LearningEventError, match="cleanup limit"):
        store.append(_evidence())


@pytest.mark.parametrize("lock_kind", ["symlink", "hardlink"])
def test_store_rejects_unsafe_lock_file(tmp_path, lock_kind):
    store = FileLearningEventStore(tmp_path)
    store.control_directory.mkdir()
    target = store.control_directory / "target"
    target.write_bytes(b"lock")
    lock_path = store.control_directory / "store.lock"
    if lock_kind == "symlink":
        lock_path.symlink_to(target)
    else:
        os.link(target, lock_path)

    with pytest.raises(LearningEventError, match="lock file is unsafe"):
        store.replay(trusted_tenant_id="tenant-a")


def test_idempotent_append_requires_destination_directory_fsync(tmp_path, monkeypatch):
    store = FileLearningEventStore(tmp_path)
    event = _evidence()
    assert store.append(event) == "created"
    destination_info = store.directory.stat()
    real_fsync = os.fsync

    def fail_destination_fsync(descriptor):
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == (destination_info.st_dev, destination_info.st_ino):
            raise OSError("destination fsync failed")
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.learning_event_store.os.fsync", fail_destination_fsync)
    with pytest.raises(LearningEventError, match="could not be made durable"):
        store.append(event)
