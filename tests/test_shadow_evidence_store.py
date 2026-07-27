from __future__ import annotations

import gc
import multiprocessing
import os
import sqlite3
import sys
import threading
import time
import weakref
import warnings
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import trustforge.agent.shadow_evidence_store as evidence_store_module
from trustforge.agent.shadow_contracts import (
    CONTRACT_VERSION,
    ShadowInput,
    ShadowObservation,
    ShadowReleaseIdentity,
    input_digest,
    load_policy,
    policy_digest,
    to_dict,
)
from trustforge.agent.shadow_evidence_store import (
    APPLICATION_ID,
    ShadowEvidenceStore,
    ShadowEvidenceStoreError,
)


def _identity():
    policy = load_policy()
    return ShadowReleaseIdentity(
        active_release="release:legacy@1.2.3",
        candidate_release="release:kernel@2.0.0-rc1",
        active_artifact_digest="sha256:" + "a" * 64,
        candidate_artifact_digest="sha256:" + "b" * 64,
        policy_digest=policy_digest(policy),
        contract_version=CONTRACT_VERSION,
    )


def _observations(identity=None):
    identity = identity or _identity()
    base = datetime(2026, 7, 28, tzinfo=timezone.utc)
    result = []
    for index in range(30):
        canonical_input = ShadowInput(
            request_id=f"request-{index}",
            coin=("BTC", "ETH", "SOL")[index % 3],
            question_type=("analysis", "hypothesis")[index % 2],
            pit_epoch=base.timestamp() + index,
            query=f"outlook-{index}",
        )
        result.append(
            ShadowObservation(
                release_identity=identity,
                canonical_input=canonical_input,
                input_digest=input_digest(to_dict(canonical_input)),
                observed_at=(base + timedelta(minutes=index)).isoformat(),
                status="success",
                parity_passed=True,
                confidence_delta=0.01,
                trust_delta=0.01,
                supporting_jaccard=0.9,
                elapsed_ms=100,
                provider_calls=0,
                cost_usd=0,
                claim_ids=(f"claim-{index}",),
            )
        )
    return result


def _store(path: Path, **kwargs):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    return ShadowEvidenceStore(path, **kwargs)


def _worker(path: str, observation: ShadowObservation, queue):
    try:
        store = ShadowEvidenceStore(path)
        queue.put(store.record_observation(store.observation_event_id(observation), observation))
    except Exception as exc:  # pragma: no cover - result asserted by parent
        queue.put(f"{type(exc).__name__}: {exc!r}: cause={exc.__cause__!r}")


def _inherited_store_worker(store: ShadowEvidenceStore, queue):
    try:
        queue.put(store.record_policy(load_policy()))
    except Exception as exc:  # pragma: no cover - result asserted by parent
        queue.put(f"{type(exc).__name__}: {exc}")


def test_restart_deterministic_replay_and_append_only(tmp_path):
    path = tmp_path / "private" / "shadow.sqlite3"
    store = _store(path)
    policy = load_policy()
    store.record_policy(policy)
    observations = _observations()
    for observation in reversed(observations):
        event_id = store.observation_event_id(observation)
        assert store.record_observation(event_id, observation) == event_id
    first = store.evaluate(_identity(), policy, now="2026-07-28T01:00:00+00:00")
    restarted = ShadowEvidenceStore(path)
    second = restarted.evaluate(_identity(), policy, now="2026-07-28T01:00:00+00:00")
    assert first == second
    duplicate_id = restarted.observation_event_id(observations[0])
    assert restarted.record_observation(duplicate_id, observations[0]) == duplicate_id
    connection = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM observations")
    connection.close()


def test_concurrent_multiprocess_duplicate_is_idempotent(tmp_path):
    path = tmp_path / "private" / "shadow.sqlite3"
    store = _store(path, busy_timeout_ms=5_000)
    store.record_policy(load_policy())
    observation = _observations()[0]
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    workers = [
        context.Process(target=_worker, args=(str(path), observation, queue))
        for _ in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    results = [queue.get(timeout=2) for _ in workers]
    event_id = store.observation_event_id(observation)
    assert results == [event_id] * 4
    connection = sqlite3.connect(path)
    stored_count = connection.execute("SELECT count(*) FROM observations").fetchone()[0]
    connection.close()
    assert stored_count == 1


def test_tamper_and_conflicting_duplicate_fail_closed(tmp_path):
    path = tmp_path / "private" / "shadow.sqlite3"
    store = _store(path)
    store.record_policy(load_policy())
    observation = _observations()[0]
    event_id = store.observation_event_id(observation)
    store.record_observation(event_id, observation)
    with pytest.raises(ShadowEvidenceStoreError, match="event id"):
        store.record_observation(event_id, replace(observation, elapsed_ms=101))
    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER immutable_observations_update")
    connection.execute("UPDATE observations SET payload=?", (b"{}",))
    connection.commit()
    connection.close()
    with pytest.raises(ShadowEvidenceStoreError):
        ShadowEvidenceStore(path).evaluate(
            _identity(), load_policy(), now="2026-07-28T01:00:00+00:00"
        )


def test_exact_release_tuple_prevents_mixed_aggregation(tmp_path):
    path = tmp_path / "private" / "shadow.sqlite3"
    store = _store(path)
    policy = load_policy()
    store.record_policy(policy)
    primary = _observations()
    other_identity = replace(_identity(), candidate_release="release:kernel@other")
    mixed = _observations(other_identity)[0]
    for observation in [*primary, mixed]:
        store.record_observation(store.observation_event_id(observation), observation)
    result = store.evaluate(_identity(), policy, now="2026-07-28T01:00:00+00:00")
    assert result.decision.aggregate.observation_count == 30


def test_stale_window_is_preserved_and_evaluated_fail_closed(tmp_path):
    path = tmp_path / "private" / "shadow.sqlite3"
    store = _store(path)
    policy = load_policy()
    store.record_policy(policy)
    for observation in _observations():
        store.record_observation(store.observation_event_id(observation), observation)
    with pytest.raises(ShadowEvidenceStoreError):
        store.evaluate(_identity(), policy, now="2026-07-30T01:00:00+00:00")


def test_corrupt_locked_full_unknown_schema_and_bounds_fail_closed(tmp_path):
    corrupt = tmp_path / "corrupt" / "db"
    corrupt.parent.mkdir(mode=0o700)
    corrupt.write_bytes(b"not sqlite")
    corrupt.chmod(0o600)
    with pytest.raises(ShadowEvidenceStoreError):
        ShadowEvidenceStore(corrupt)

    unknown = tmp_path / "unknown" / "db"
    unknown.parent.mkdir(mode=0o700)
    connection = sqlite3.connect(unknown)
    connection.execute("CREATE TABLE legacy(value)")
    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
    connection.execute("PRAGMA user_version=2")
    connection.close()
    unknown.chmod(0o600)
    with pytest.raises(ShadowEvidenceStoreError, match="unknown or legacy"):
        ShadowEvidenceStore(unknown)

    path = tmp_path / "bounded" / "db"
    store = _store(path, max_rows=1, max_query_rows=1, max_db_bytes=1_000_000)
    store.record_policy(load_policy())
    observation = _observations()[0]
    store.record_observation(store.observation_event_id(observation), observation)
    with pytest.raises(ShadowEvidenceStoreError, match="row limit"):
        second = _observations()[1]
        store.record_observation(store.observation_event_id(second), second)
    with pytest.raises(ShadowEvidenceStoreError, match="query"):
        store.evaluate(_identity(), load_policy(), now=observation.observed_at, limit=2)

    lock_connection = sqlite3.connect(path, isolation_level=None)
    lock_connection.execute("BEGIN IMMEDIATE")
    with pytest.raises(ShadowEvidenceStoreError):
        store.record_policy(load_policy())
    lock_connection.execute("ROLLBACK")
    lock_connection.close()

    full_path = tmp_path / "full" / "db"
    full_store = _store(full_path, max_db_bytes=1)
    with pytest.raises(ShadowEvidenceStoreError, match="size limit"):
        full_store.record_policy(load_policy())


def test_safe_path_env_permissions_and_explicit_retention_receipt(tmp_path, monkeypatch):
    relative = Path("relative.sqlite3")
    with pytest.raises(ShadowEvidenceStoreError, match="absolute"):
        ShadowEvidenceStore(relative)
    public_parent = tmp_path / "public"
    public_parent.mkdir(mode=0o755)
    public_parent.chmod(0o755)
    with pytest.raises(ShadowEvidenceStoreError, match="group/other"):
        ShadowEvidenceStore(public_parent / "db")
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ShadowEvidenceStoreError, match="symlink"):
        ShadowEvidenceStore(link / "db")

    path = tmp_path / "env" / "db"
    path.parent.mkdir(mode=0o700)
    monkeypatch.setenv("TRUSTFORGE_SHADOW_DB_PATH", str(path))
    store = ShadowEvidenceStore()
    policy = load_policy()
    store.record_policy(policy)
    observations = _observations()
    for observation in observations:
        store.record_observation(store.observation_event_id(observation), observation)
    evaluation = store.evaluate(
        _identity(), policy, now="2026-07-28T01:00:00+00:00"
    )
    cutoff = store.observation_event_id(observations[0])
    receipt = store.record_retention_receipt(
        identity=_identity(), archive_uri="s3://immutable/archive",
        before_event_id=cutoff, archive_digest="sha256:" + "b" * 64,
        observation_root_digest=evaluation.observation_root_digest,
    )
    assert receipt == store.record_retention_receipt(
        identity=_identity(), archive_uri="s3://immutable/archive", before_event_id=cutoff,
        archive_digest="sha256:" + "b" * 64,
        observation_root_digest=evaluation.observation_root_digest,
    )
    assert path.stat().st_mode & 0o077 == 0


def test_schema_migration_ledger_and_no_legacy_import(tmp_path):
    path = tmp_path / "private" / "shadow.sqlite3"
    _store(path)
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert connection.execute(
        "SELECT version, contract_version FROM schema_migrations"
    ).fetchall() == [(1, CONTRACT_VERSION)]
    assert connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"observations", "policies", "aggregates", "decisions"} <= tables
    connection.close()


def test_stale_history_is_excluded_but_exact_window_boundaries_are_included(tmp_path):
    path = tmp_path / "private" / "db"
    store = _store(path)
    policy = load_policy()
    store.record_policy(policy)
    fresh = _observations()
    stale_input = replace(
        fresh[0].canonical_input, request_id="stale",
        pit_epoch=datetime(2026, 7, 26, 23, tzinfo=timezone.utc).timestamp(),
    )
    stale = replace(
        fresh[0], canonical_input=stale_input,
        input_digest=input_digest(to_dict(stale_input)),
        observed_at="2026-07-26T23:59:59+00:00",
    )
    boundary_input = replace(
        fresh[0].canonical_input, request_id="boundary",
        pit_epoch=datetime(2026, 7, 27, 1, tzinfo=timezone.utc).timestamp(),
    )
    boundary = replace(
        fresh[0], canonical_input=boundary_input,
        input_digest=input_digest(to_dict(boundary_input)),
        observed_at="2026-07-27T01:00:00+00:00",
    )
    for observation in [stale, boundary, *fresh]:
        store.record_observation(store.observation_event_id(observation), observation)
    result = store.evaluate(
        _identity(), policy, now="2026-07-28T01:00:00+00:00", limit=31,
    )
    assert result.decision.action.value == "eligible_for_operator_review"
    assert result.decision.aggregate.observation_count == 31


def test_future_row_denormalized_tamper_and_policy_tamper_fail_closed(tmp_path):
    path = tmp_path / "private" / "db"
    store = _store(path)
    policy = load_policy()
    store.record_policy(policy)
    observation = _observations()[0]
    future = replace(observation, observed_at="2026-07-29T00:00:00+00:00")
    store.record_observation(store.observation_event_id(future), future)
    with pytest.raises(ShadowEvidenceStoreError, match="future"):
        store.evaluate(_identity(), policy, now="2026-07-28T01:00:00+00:00")

    connection = sqlite3.connect(path)
    trigger_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='immutable_observations_update'"
    ).fetchone()[0]
    connection.execute("DROP TRIGGER immutable_observations_update")
    connection.execute("UPDATE observations SET input_digest='sha256:' || ?", ("0" * 64,))
    connection.execute(trigger_sql)
    connection.commit()
    connection.close()
    reopened = ShadowEvidenceStore(path)
    with pytest.raises(ShadowEvidenceStoreError):
        reopened.evaluate(_identity(), policy, now="2026-07-30T01:00:00+00:00")

    policy_path = tmp_path / "policy" / "db"
    policy_store = _store(policy_path)
    policy_store.record_policy(policy)
    connection = sqlite3.connect(policy_path)
    trigger_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='immutable_policies_update'"
    ).fetchone()[0]
    connection.execute("DROP TRIGGER immutable_policies_update")
    connection.execute("UPDATE policies SET payload_digest='sha256:' || ?", ("0" * 64,))
    connection.execute(trigger_sql)
    connection.commit()
    connection.close()
    reopened = ShadowEvidenceStore(policy_path)
    with pytest.raises(ShadowEvidenceStoreError):
        reopened.record_observation(
            reopened.observation_event_id(observation), observation,
        )


@pytest.mark.parametrize("tamper", ["trigger", "column"])
def test_canonical_schema_rejects_same_name_noop_trigger_and_alteration(tmp_path, tamper):
    path = tmp_path / tamper / "db"
    _store(path)
    connection = sqlite3.connect(path)
    if tamper == "trigger":
        connection.execute("DROP TRIGGER immutable_decisions_update")
        connection.execute(
            "CREATE TRIGGER immutable_decisions_update BEFORE UPDATE ON decisions "
            "BEGIN SELECT 1; END"
        )
    else:
        connection.execute("ALTER TABLE decisions ADD COLUMN injected TEXT")
    connection.commit()
    connection.close()
    with pytest.raises(ShadowEvidenceStoreError, match="canonical schema"):
        ShadowEvidenceStore(path)


def test_database_or_parent_replacement_after_initialization_is_rejected(tmp_path):
    path = tmp_path / "private" / "db"
    store = _store(path)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(path.read_bytes())
    replacement.chmod(0o600)
    path.unlink()
    replacement.rename(path)
    with pytest.raises(
        ShadowEvidenceStoreError, match="identity changed|verified database inode",
    ):
        store.record_policy(load_policy())


def test_parent_directory_replacement_is_detected(tmp_path):
    path = tmp_path / "private" / "db"
    store = _store(path)
    original_parent = path.parent
    moved_parent = tmp_path / "moved-private"
    original_parent.rename(moved_parent)
    original_parent.mkdir(mode=0o700)
    replacement = original_parent / "db"
    replacement.write_bytes((moved_parent / "db").read_bytes())
    replacement.chmod(0o600)
    with pytest.raises(
        ShadowEvidenceStoreError, match="parent identity changed|lock is unsafe",
    ):
        store.record_policy(load_policy())


def test_parent_permission_change_after_initialization_is_rejected(tmp_path):
    path = tmp_path / "private" / "db"
    store = _store(path)
    path.parent.chmod(0o755)
    with pytest.raises(
        ShadowEvidenceStoreError, match="parent identity changed|lock is unsafe",
    ):
        store.record_policy(load_policy())


def test_preexisting_database_fd_is_not_mistaken_for_sqlite_attribution(tmp_path):
    path = tmp_path / "private" / "db"
    store = _store(path)
    held_fd = os.open(path, os.O_RDONLY)
    try:
        assert store.record_policy(load_policy()).startswith("sha256:")
    finally:
        os.close(held_fd)


def test_replaceable_lock_path_cannot_split_worker_lock_domain(tmp_path):
    path = tmp_path / "private" / "db"
    store = _store(path)
    store.record_policy(load_policy())
    fake_lock = Path(f"{path}.lock")
    fake_lock.write_text("attacker-controlled pathname is not used")
    fake_lock.chmod(0o600)
    observation = _observations()[0]
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    workers = [
        context.Process(target=_worker, args=(str(path), observation, queue))
        for _ in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    event_id = store.observation_event_id(observation)
    assert [queue.get(timeout=2) for _ in workers] == [event_id] * 4


def test_fork_inherited_store_reopens_distinct_lock_description(tmp_path):
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("POSIX fork context unavailable")
    path = tmp_path / "private" / "db"
    store = _store(path, busy_timeout_ms=100)
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    with store._process_lock():
        worker = context.Process(target=_inherited_store_worker, args=(store, queue))
        worker.start()
        worker.join(5)
        assert worker.exitcode == 0
        result = queue.get(timeout=2)
        assert "process lock unavailable" in result
    assert store.record_policy(load_policy()).startswith("sha256:")


def test_shared_store_threads_are_serialized_by_object_mutex(tmp_path, monkeypatch):
    path = tmp_path / "private" / "db"
    store = _store(path)
    original = store._transaction_locked
    state_lock = threading.Lock()
    active = 0
    maximum = 0

    def instrumented(operation):
        nonlocal active, maximum
        with state_lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        try:
            return original(operation)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(store, "_transaction_locked", instrumented)
    results: list[str] = []
    threads = [
        threading.Thread(target=lambda: results.append(store.record_policy(load_policy())))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert len(set(results)) == 1
    assert maximum == 1


def test_close_waits_for_operation_is_idempotent_and_post_close_fails(tmp_path, monkeypatch):
    path = tmp_path / "private" / "db"
    store = _store(path)
    original = store._transaction_locked
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    def blocking(operation):
        entered.set()
        assert release.wait(5)
        return original(operation)

    monkeypatch.setattr(store, "_transaction_locked", blocking)
    operation = threading.Thread(target=lambda: store.record_policy(load_policy()))
    operation.start()
    assert entered.wait(2)
    closer = threading.Thread(target=lambda: (store.close(), closed.set()))
    closer.start()
    assert not closed.wait(0.05)
    release.set()
    operation.join(5)
    closer.join(5)
    assert closed.is_set()
    store.close()
    with pytest.raises(ShadowEvidenceStoreError, match="store is closed"):
        store.record_policy(load_policy())


def test_weak_fork_registry_does_not_retain_unclosed_stores_or_fds(tmp_path):
    gc.collect()
    baseline_registry = len(evidence_store_module._FORK_STORES)
    baseline_fds = len(list(Path("/dev/fd").iterdir()))
    references = []
    stores = []
    for index in range(40):
        store = _store(tmp_path / f"store-{index}" / "db")
        references.append(weakref.ref(store))
        stores.append(store)
    del store
    stores.clear()
    gc.collect()
    assert all(reference() is None for reference in references)
    assert len(evidence_store_module._FORK_STORES) <= baseline_registry
    assert len(list(Path("/dev/fd").iterdir())) <= baseline_fds + 2


def test_many_explicit_closes_leave_no_fork_callbacks_or_fds(tmp_path):
    gc.collect()
    baseline_registry = len(evidence_store_module._FORK_STORES)
    baseline_fds = len(list(Path("/dev/fd").iterdir()))
    references = []
    for index in range(40):
        store = _store(tmp_path / f"closed-{index}" / "db")
        references.append(weakref.ref(store))
        store.close()
    del store
    gc.collect()
    assert all(reference() is None for reference in references)
    assert len(evidence_store_module._FORK_STORES) <= baseline_registry
    assert len(list(Path("/dev/fd").iterdir())) <= baseline_fds + 2


def test_reopen_fstat_failure_closes_new_descriptor(tmp_path, monkeypatch):
    store = _store(tmp_path / "private" / "db")
    os.close(store._directory_lock_fd)
    store._directory_lock_fd = -1
    baseline_fds = len(list(Path("/dev/fd").iterdir()))

    def failing_fstat(_descriptor):
        raise OSError("injected fstat failure")

    monkeypatch.setattr(os, "fstat", failing_fstat)
    with pytest.raises(ShadowEvidenceStoreError, match="cannot reopen"):
        store.record_policy(load_policy())
    assert len(list(Path("/dev/fd").iterdir())) <= baseline_fds


def test_open_bound_fstat_failure_closes_descriptor(tmp_path, monkeypatch):
    store = _store(tmp_path / "private" / "db")
    baseline_fds = len(list(Path("/dev/fd").iterdir()))
    real_fstat = os.fstat
    calls = 0

    def fail_second_fstat(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected bound-fd fstat failure")
        return real_fstat(descriptor)

    monkeypatch.setattr(os, "fstat", fail_second_fstat)
    with pytest.raises(ShadowEvidenceStoreError):
        store.record_policy(load_policy())
    assert len(list(Path("/dev/fd").iterdir())) <= baseline_fds


def test_prepare_path_fstat_failure_closes_directory_descriptor(tmp_path, monkeypatch):
    path = tmp_path / "private" / "db"
    path.parent.mkdir(mode=0o700)
    baseline_fds = len(list(Path("/dev/fd").iterdir()))

    def failing_fstat(_descriptor):
        raise OSError("injected initialization fstat failure")

    monkeypatch.setattr(os, "fstat", failing_fstat)
    with pytest.raises(ShadowEvidenceStoreError):
        ShadowEvidenceStore(path)
    assert len(list(Path("/dev/fd").iterdir())) <= baseline_fds


def test_constructor_failure_gc_has_no_unraisable_warning_or_fd_leak(monkeypatch):
    baseline_fds = len(list(Path("/dev/fd").iterdir()))
    unraisable = []
    monkeypatch.setattr(sys, "unraisablehook", unraisable.append)
    references = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("error")
        for _ in range(40):
            partial = ShadowEvidenceStore.__new__(ShadowEvidenceStore)
            references.append(weakref.ref(partial))
            with pytest.raises(ShadowEvidenceStoreError, match="absolute"):
                partial.__init__("relative.sqlite3")
            del partial
        gc.collect()
    assert unraisable == []
    assert caught == []
    assert all(reference() is None for reference in references)
    assert len(list(Path("/dev/fd").iterdir())) <= baseline_fds + 1


def test_pinned_reader_wal_growth_hits_combined_hard_cap(tmp_path):
    path = tmp_path / "private" / "db"
    store = _store(path, max_db_bytes=256 * 1024)
    policy = load_policy()
    store.record_policy(policy)
    reader = sqlite3.connect(path, isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM observations").fetchone()
    failed_closed = False
    template = _observations()[0]
    for index in range(100):
        canonical_input = replace(
            template.canonical_input,
            request_id=f"wal-{index}",
            query=f"wal-growth-{index}",
        )
        observation = replace(
            template,
            canonical_input=canonical_input,
            input_digest=input_digest(to_dict(canonical_input)),
            observed_at=(
                datetime(2026, 7, 28, tzinfo=timezone.utc) + timedelta(seconds=index)
            ).isoformat(),
        )
        try:
            store.record_observation(store.observation_event_id(observation), observation)
        except ShadowEvidenceStoreError as exc:
            assert "size limit" in str(exc)
            failed_closed = True
            break
    reader.execute("ROLLBACK")
    reader.close()
    assert failed_closed


def test_retention_receipt_validates_formats_and_binding(tmp_path):
    path = tmp_path / "private" / "db"
    store = _store(path)
    policy = load_policy()
    store.record_policy(policy)
    observations = _observations()
    for observation in observations:
        store.record_observation(store.observation_event_id(observation), observation)
    evaluation = store.evaluate(_identity(), policy, now="2026-07-28T01:00:00+00:00")
    with pytest.raises(ShadowEvidenceStoreError):
        store.record_retention_receipt(
            identity=_identity(), archive_uri="file:///tmp/archive",
            before_event_id=store.observation_event_id(observations[0]),
            archive_digest="bad", observation_root_digest=evaluation.observation_root_digest,
        )
    with pytest.raises(ShadowEvidenceStoreError, match="aggregate root"):
        store.record_retention_receipt(
            identity=_identity(), archive_uri="s3://bucket/archive",
            before_event_id=store.observation_event_id(observations[0]),
            archive_digest="sha256:" + "c" * 64,
            observation_root_digest="sha256:" + "d" * 64,
        )
