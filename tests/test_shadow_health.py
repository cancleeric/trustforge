from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import fcntl

import trustforge.agent.shadow_health as health_module
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
from trustforge.agent.shadow_evidence_store import ShadowEvidenceStore
from trustforge.cli import main


NOW = "2026-07-28T01:00:00+00:00"


def _hold_directory_lock(path, ready, release):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        ready.set()
        release.wait(10)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _identity() -> ShadowReleaseIdentity:
    policy = load_policy()
    return ShadowReleaseIdentity(
        active_release="release:legacy@1.2.3",
        candidate_release="release:kernel@2.0.0-rc1",
        active_artifact_digest="sha256:" + "a" * 64,
        candidate_artifact_digest="sha256:" + "b" * 64,
        policy_digest=policy_digest(policy),
        contract_version=CONTRACT_VERSION,
    )


def _observations(
    identity: ShadowReleaseIdentity,
) -> list[ShadowObservation]:
    base = datetime(2026, 7, 28, tzinfo=timezone.utc)
    observations = []
    for index in range(30):
        canonical_input = ShadowInput(
            request_id=f"request-{index}",
            coin=("BTC", "ETH", "SOL")[index % 3],
            question_type=("analysis", "hypothesis")[index % 2],
            pit_epoch=base.timestamp() + index,
            query=f"query-{index}",
        )
        observations.append(ShadowObservation(
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
        ))
    return observations


def _prepare(monkeypatch, tmp_path, observations=None):
    path = tmp_path / "shadow-private" / "evidence.sqlite3"
    path.parent.mkdir(mode=0o700)
    path.parent.chmod(0o700)
    identity = _identity()
    store = ShadowEvidenceStore(path)
    store.record_policy(load_policy())
    for observation in observations or _observations(identity):
        store.record_observation(
            store.observation_event_id(observation), observation,
        )
    store.close()
    monkeypatch.setenv("TRUSTFORGE_SHADOW_DB_PATH", str(path))
    monkeypatch.setattr(
        health_module,
        "measured_release_identity",
        lambda policy: SimpleNamespace(identity=identity),
    )
    return path, identity


def _source_state(path):
    result = {}
    for candidate in sorted(path.parent.glob(f"{path.name}*")):
        value = candidate.lstat()
        digest = hashlib.sha256()
        with candidate.open("rb") as stream:
            while chunk := stream.read(65_536):
                digest.update(chunk)
        result[candidate.name] = {
            "content_sha256": digest.hexdigest(),
            "inode": value.st_ino,
            "mode": value.st_mode,
            "size": value.st_size,
            "mtime_ns": value.st_mtime_ns,
            "ctime_ns": value.st_ctime_ns,
        }
    return result


def test_eligible_report_is_deterministic_and_read_only(monkeypatch, tmp_path):
    path, _ = _prepare(monkeypatch, tmp_path)
    assert not path.with_name(f"{path.name}-wal").exists()
    filesystem_before = _source_state(path)
    connection = sqlite3.connect(path)
    before = {
        table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("observations", "observation_completions", "aggregates", "decisions")
    }
    connection.close()

    first = health_module.build_shadow_health_report(now=NOW)
    second = health_module.build_shadow_health_report(now=NOW)

    assert first == second
    assert first["action"] == "eligible_for_operator_review"
    assert first["automatic_activation"] is False
    assert first["requires_manual_review"] is True
    assert first["metrics"]["observations"] == 30
    assert first["metrics"]["coins"] == 3
    assert first["metrics"]["question_types"] == 2
    assert first["metrics"]["minimum_per_cell"] == 5
    assert first["metrics"]["provider_calls"] == 0
    assert first["metrics"]["cost_usd"] == 0
    assert first["checks"] == {
        "schema": True,
        "policy": True,
        "release_manifest": True,
        "runtime_attestation": True,
        "completion_evidence": True,
    }
    assert first["evidence"]["observation_root_digest"].startswith("sha256:")
    assert first["evidence"]["decision_event_id"].startswith("sha256:")

    connection = sqlite3.connect(path)
    after = {
        table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in before
    }
    connection.close()
    assert after == before
    assert _source_state(path) == filesystem_before
    assert not path.with_name(f"{path.name}-wal").exists()


def test_live_wal_and_missing_source_shm_are_snapshotted_without_mutation(
    monkeypatch, tmp_path,
):
    path, identity = _prepare(monkeypatch, tmp_path)
    keeper = sqlite3.connect(path)
    keeper.execute("PRAGMA wal_autocheckpoint=0")
    keeper.execute("BEGIN")
    keeper.execute("SELECT count(*) FROM observations").fetchone()
    original = _observations(identity)[0]
    canonical_input = replace(
        original.canonical_input,
        request_id="wal-request",
        pit_epoch=datetime(2026, 7, 28, 0, 50, tzinfo=timezone.utc).timestamp(),
    )
    extra = replace(
        original,
        canonical_input=canonical_input,
        input_digest=input_digest(to_dict(canonical_input)),
        observed_at="2026-07-28T00:50:00+00:00",
    )
    writer = ShadowEvidenceStore(path)
    writer.record_observation(writer.observation_event_id(extra), extra)
    writer.close()
    assert path.with_name(f"{path.name}-wal").exists()
    assert path.with_name(f"{path.name}-shm").exists()

    live_before = _source_state(path)
    live_report = health_module.build_shadow_health_report(now=NOW)
    assert live_report["action"] == "eligible_for_operator_review"
    assert live_report["metrics"]["observations"] == 31
    assert _source_state(path) == live_before

    copied_parent = tmp_path / "missing-shm"
    copied_parent.mkdir(mode=0o700)
    copied_parent.chmod(0o700)
    copied = copied_parent / "evidence.sqlite3"
    copied.write_bytes(path.read_bytes())
    copied.chmod(0o600)
    copied_wal = copied.with_name(f"{copied.name}-wal")
    copied_wal.write_bytes(path.with_name(f"{path.name}-wal").read_bytes())
    copied_wal.chmod(0o600)
    assert not copied.with_name(f"{copied.name}-shm").exists()
    monkeypatch.setenv("TRUSTFORGE_SHADOW_DB_PATH", str(copied))
    copied_before = _source_state(copied)
    copied_report = health_module.build_shadow_health_report(now=NOW)
    assert copied_report["action"] == "eligible_for_operator_review"
    assert copied_report["metrics"]["observations"] == 31
    assert _source_state(copied) == copied_before
    keeper.close()


def test_oversized_shm_fails_before_snapshot_and_preserves_source(
    monkeypatch, tmp_path,
):
    path, _ = _prepare(monkeypatch, tmp_path)
    source_size = path.stat().st_size
    shm = path.with_name(f"{path.name}-shm")
    with shm.open("wb") as stream:
        stream.truncate(2_048)
    shm.chmod(0o600)
    original_store = health_module.ShadowEvidenceStore
    monkeypatch.setattr(
        health_module,
        "ShadowEvidenceStore",
        lambda **kwargs: original_store(
            **kwargs, max_db_bytes=source_size + 1_024,
        ),
    )
    before = _source_state(path)
    report = health_module.build_shadow_health_report(now=NOW)
    assert report["action"] == "stop"
    assert report["blockers"] == ["evidence_store_unavailable"]
    assert _source_state(path) == before


def test_exact_v1_health_fails_without_migration_or_source_change(
    monkeypatch, tmp_path,
):
    path = tmp_path / "v1-private" / "evidence.sqlite3"
    path.parent.mkdir(mode=0o700)
    connection = sqlite3.connect(path)
    connection.executescript(evidence_store_module._SCHEMA_V1)
    connection.execute(
        f"PRAGMA application_id={evidence_store_module.APPLICATION_ID}"
    )
    connection.execute("PRAGMA user_version=1")
    connection.execute(
        "INSERT INTO schema_migrations(version, contract_version, applied_at) "
        "VALUES (1, ?, '2026-07-28T00:00:00Z')",
        (CONTRACT_VERSION,),
    )
    connection.executescript(
        evidence_store_module._IMMUTABILITY_TRIGGERS_V1
    )
    connection.commit()
    connection.close()
    path.chmod(0o600)
    monkeypatch.setenv("TRUSTFORGE_SHADOW_DB_PATH", str(path))
    monkeypatch.setattr(
        health_module,
        "measured_release_identity",
        lambda policy: SimpleNamespace(identity=_identity()),
    )
    before = _source_state(path)
    report = health_module.build_shadow_health_report(now=NOW)
    assert report["action"] == "stop"
    assert report["blockers"] == ["evidence_store_unavailable"]
    assert _source_state(path) == before
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    connection.close()
    assert "observation_completions" not in tables


def test_process_lock_contention_fails_without_source_or_temp_mutation(
    monkeypatch, tmp_path,
):
    path, _ = _prepare(monkeypatch, tmp_path)
    temp_root = tmp_path / "health-temp"
    temp_root.mkdir(mode=0o700)
    monkeypatch.setattr(
        evidence_store_module.tempfile, "tempdir", str(temp_root),
    )
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_directory_lock,
        args=(str(path.parent), ready, release),
    )
    holder.start()
    assert ready.wait(5)
    before = _source_state(path)
    try:
        report = health_module.build_shadow_health_report(now=NOW)
    finally:
        release.set()
        holder.join(5)
    assert holder.exitcode == 0
    assert report["action"] == "stop"
    assert report["blockers"] == ["evidence_store_unavailable"]
    assert _source_state(path) == before
    assert list(temp_root.iterdir()) == []


def test_incomplete_and_stale_windows_are_never_eligible(monkeypatch, tmp_path):
    identity = _identity()
    one = _observations(identity)[0]
    path, _ = _prepare(monkeypatch, tmp_path, [one])
    incomplete = health_module.build_shadow_health_report(now=NOW)
    assert incomplete["action"] == "continue_observation"
    assert "insufficient_observations" in incomplete["blockers"]

    stale = health_module.build_shadow_health_report(
        now="2026-07-30T01:00:00+00:00",
    )
    assert stale["action"] == "stop"
    assert "missing_stale_or_future_observation" in stale["blockers"]
    assert stale["metrics"]["observations"] == 0
    assert path.exists()


def test_orphan_and_tamper_fail_closed(monkeypatch, tmp_path):
    path, identity = _prepare(monkeypatch, tmp_path)
    store = ShadowEvidenceStore(path)
    original = _observations(identity)[0]
    canonical_input = replace(
        original.canonical_input,
        request_id="orphan-request",
        pit_epoch=datetime(2026, 7, 28, 0, 45, tzinfo=timezone.utc).timestamp(),
    )
    extra = replace(
        original,
        canonical_input=canonical_input,
        input_digest=input_digest(to_dict(canonical_input)),
        observed_at="2026-07-28T00:45:00+00:00",
    )
    event_id = store.observation_event_id(extra)
    store.record_policy_and_observation(
        load_policy(), event_id, extra, commit_guard=lambda: True,
    )
    store.close()

    orphan = health_module.build_shadow_health_report(now=NOW)
    assert orphan["action"] == "stop"
    assert orphan["checks"]["completion_evidence"] is False
    assert "terminal_corrupt" in orphan["blockers"]

    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER immutable_observations_update")
    connection.commit()
    connection.close()
    tampered = health_module.build_shadow_health_report(now=NOW)
    assert tampered["action"] == "stop"
    assert tampered["blockers"] == ["evidence_store_unavailable"]


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("eligible_for_operator_review", 0),
        ("continue_observation", 2),
        ("stop", 3),
    ],
)
def test_cli_exit_codes_are_machine_readable(
    monkeypatch, capsys, action, expected,
):
    monkeypatch.setattr(
        health_module,
        "build_shadow_health_report",
        lambda **kwargs: {"action": action},
    )
    assert main(["shadow-health", "--at", NOW]) == expected
    assert json.loads(capsys.readouterr().out) == {"action": action}


def test_missing_attestation_fails_closed_without_details(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_SHADOW_DEDICATED_RUNTIME", raising=False)
    report = health_module.build_shadow_health_report(now=NOW)
    assert report["action"] == "stop"
    assert report["identity"] is None
    assert report["blockers"] == ["identity_policy_or_attestation_invalid"]
