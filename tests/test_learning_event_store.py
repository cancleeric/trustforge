import multiprocessing
import os
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


def _evidence(identity="evidence:1"):
    return make_learning_event(
        kind="evidentiary",
        tenant_id="tenant-a",
        entity_id=identity,
        revision=1,
        provenance=PROVENANCE,
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

    assert FileLearningEventStore(directory).replay() == []
    assert not directory.exists()


def test_file_store_persists_idempotently_and_replays_after_restart(tmp_path):
    event = _evidence()
    store = FileLearningEventStore(tmp_path)

    assert store.append(event) == "created"
    assert store.append(event) == "idempotent"
    assert FileLearningEventStore(tmp_path).replay() == [event]
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

    replayed = store.replay()
    expected = sorted(events, key=lambda event: store._path_for_identity(event.identity).name)
    assert replayed == expected
    assert FileLearningEventStore(tmp_path).snapshot() == tuple(
        serialize_learning_event(event) for event in expected
    )


def test_file_store_rejects_same_identity_with_different_bytes(tmp_path):
    store = FileLearningEventStore(tmp_path)
    original = _outcome(status="pending")
    rewritten = _outcome(status="labeled")

    assert store.append(original) == "created"
    with pytest.raises(LearningEventError, match="immutable"):
        store.append(rewritten)
    assert store.replay() == [original]


@pytest.mark.parametrize("replacement", [b"{", b"not-json"])
def test_file_store_replay_fails_closed_for_corrupt_or_truncated_event(tmp_path, replacement):
    event = _evidence()
    store = FileLearningEventStore(tmp_path)
    store.append(event)
    next(tmp_path.iterdir()).write_bytes(replacement)

    with pytest.raises(LearningEventError, match="corrupt"):
        store.replay()


def test_file_store_replay_fails_closed_for_oversize_event(tmp_path):
    event = _evidence()
    writer = FileLearningEventStore(tmp_path)
    writer.append(event)

    with pytest.raises(LearningEventError, match="unsafe or unreadable"):
        FileLearningEventStore(tmp_path, maximum_event_bytes=8).replay()


def test_file_store_replay_fails_closed_for_digest_mismatch(tmp_path):
    event = _evidence()
    store = FileLearningEventStore(tmp_path)
    store.append(event)
    path = next(tmp_path.iterdir())
    path.rename(tmp_path / ("0" * 64 + ".json"))

    with pytest.raises(LearningEventError, match="digest"):
        store.replay()


@pytest.mark.parametrize("entry_kind", ["symlink", "directory", "fifo"])
def test_file_store_replay_fails_closed_for_non_regular_entry(tmp_path, entry_kind):
    name = "0" * 64 + ".json"
    path = tmp_path / name
    if entry_kind == "symlink":
        target = tmp_path / "target"
        target.write_text("{}", encoding="utf-8")
        path.symlink_to(target)
    elif entry_kind == "directory":
        path.mkdir()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO is unavailable")
        os.mkfifo(path)

    with pytest.raises(LearningEventError, match="unsafe or unreadable"):
        FileLearningEventStore(tmp_path).replay()


def _append_in_process(directory: str, result_directory: str, start, index: int) -> None:
    start.wait()
    try:
        result = FileLearningEventStore(Path(directory)).append(_evidence())
    except BaseException as exc:  # pragma: no cover - surfaced in parent assertion
        result = f"error:{type(exc).__name__}:{exc}"
    (Path(result_directory) / str(index)).write_text(result, encoding="utf-8")


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
    assert FileLearningEventStore(event_directory).replay() == [_evidence()]


def test_file_store_fsync_failure_leaves_no_ghost_event(tmp_path, monkeypatch):
    def fail_write(*args, **kwargs):
        raise OSError("fsync failed")

    monkeypatch.setattr("trustforge.learning_event_store.write_atomic_at", fail_write)
    store = FileLearningEventStore(tmp_path)

    with pytest.raises(OSError, match="fsync failed"):
        store.append(_evidence())
    assert store.replay() == []


def test_file_store_rejects_empty_symlink_directory(tmp_path):
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(LearningEventError, match="opened safely"):
        FileLearningEventStore(linked_directory).replay()


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
