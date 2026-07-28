from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trustforge.upgrade_adapters import PrincipalAuthority
from trustforge.upgrade_adapters import (
    HermesActivationHandler,
    HermesRollbackHandler,
    JournalCapacityError,
    LegacyJournalError,
    SandboxAttestationAuthority,
)
from trustforge.upgrade_ports import (
    AuthenticatedPrincipal,
    OperationDisplacedError,
    PointerChange,
    SandboxAttestation,
    UpgradeCandidate,
)
from trustforge.upgrade_queue import UpgradeQueue
from trustforge.safe_fs import SafePathError

TEST_TEMP_ROOT = Path(tempfile.gettempdir()).resolve()
SANDBOX_AUTHORITY = SandboxAttestationAuthority(
    TEST_TEMP_ROOT
    / f"trustforge-upgrade-queue-test-capabilities-{os.getpid()}.jsonl"
)


def principal(*actions: str, tenant: str = "t1", expired: bool = False):
    return AuthenticatedPrincipal(
        subject="named-operator",
        tenant_id=tenant,
        actions=frozenset(actions),
        expires_at=datetime.now(timezone.utc)
        + (timedelta(seconds=-1) if expired else timedelta(hours=1)),
    )


def attestation(
    proposal_id="p", revision="abc", *, passed=True,
    authority=SANDBOX_AUTHORITY,
    db_identity=None,
):
    db_identity = str(
        Path(db_identity or TEST_TEMP_ROOT / "trustforge-test-default.sqlite3")
        .resolve(strict=False)
    )
    details = {"candidate": {"family": "analysis", "revision": revision}, "tests": 24}
    encoded = json.dumps(details, sort_keys=True, separators=(",", ":")).encode()
    return authority.issue(
        db_identity=db_identity,
        proposal_id=proposal_id,
        candidate_family="analysis",
        candidate_revision=revision,
        run_id="sandbox-run-1",
        runner_version="trusted-runner/v1",
        artifact_hash=f"sha256:{revision}",
        details_checksum=hashlib.sha256(encoded).hexdigest(),
        passed=passed,
        completed_at=datetime.now(timezone.utc),
        details=details,
    )


class Catalog:
    def resolve(self, family, revision, artifact_hash):
        if artifact_hash != f"sha256:{revision}":
            raise ValueError("bad identity")
        return UpgradeCandidate(family, revision, artifact_hash, {"family": family}, f"outer-{family}")


class Activator:
    def __init__(self, *, fail=False, current="old"):
        self.fail = fail
        self.current = current
        self.calls = []

    def current_revision(self, _family):
        return self.current

    def activate(self, candidate, *, proposal_id, operation_id, expected_revision):
        self.calls.append(operation_id)
        if self.fail:
            raise RuntimeError("pointer failure")
        self.current = candidate.revision
        return PointerChange(candidate.family, candidate.revision, expected_revision, operation_id)


class Rollback:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def rollback(self, family, target_revision, *, reason, operation_id, expected_revision):
        self.calls.append(operation_id)
        if self.fail:
            raise RuntimeError("rollback failure")
        assert expected_revision == "abc"
        return PointerChange(family, target_revision, expected_revision, operation_id)


class LostResponseActivator(Activator):
    def __init__(self):
        super().__init__()
        self.receipts = {}
        self.lose_once = True

    def activate(self, candidate, *, proposal_id, operation_id, expected_revision):
        self.calls.append(operation_id)
        receipt = self.receipts.setdefault(
            operation_id,
            PointerChange(candidate.family, candidate.revision, "old", operation_id),
        )
        if self.lose_once:
            self.lose_once = False
            raise RuntimeError("lost response after pointer write")
        return receipt


def queue(tmp_path, *, activator=None, rollback=None):
    return UpgradeQueue(
        tmp_path / "upgrade.sqlite3",
        authority=PrincipalAuthority(),
        catalog=Catalog(),
        activation_handler=activator or Activator(),
        rollback_handler=rollback or Rollback(),
        sandbox_verifier=SANDBOX_AUTHORITY,
    )


def latest_instance(q, logical_id="p"):
    return next(
        row["proposal_id"]
        for row in q.status()["proposals"]
        if row["logical_id"] == logical_id
    )


def raw_five_table_snapshot(q):
    with sqlite3.connect(q.path) as db:
        return tuple(
            tuple(db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall())
            for table in (
                "upgrade_proposals",
                "upgrade_reviews",
                "upgrade_sandbox_runs",
                "upgrade_decisions",
                "upgrade_activations",
            )
        )


def record_review(q, logical_id, verdict):
    binding = q.resolve_review_instance(logical_id)
    q.record_reviews({"reviews": [{
        "proposal_id": binding["proposal_id"],
        "payload_sha256": binding["payload_sha256"],
        "verdict": verdict,
    }]})
    return binding["proposal_id"]


def prepare_approved(q):
    q.sync_diagnostic({"proposals": [{"id": "p", "area": "x", "tenant_id": "t1"}]})
    proposal_id = record_review(q, "p", "sandbox_ready")
    q.record_sandbox(attestation(proposal_id=proposal_id, db_identity=q.path))
    q.decide(proposal_id, "approve", "green", principal=principal("upgrade:approve"))
    return proposal_id


def test_queue_persists_exact_review_and_rejects_injected_approved(tmp_path):
    q = queue(tmp_path)
    q.sync_diagnostic({"proposals": [{"id": "p", "area": "x", "tenant_id": "t1"}]})
    before = q.status()
    with pytest.raises(ValueError, match="unknown automated"):
        record_review(q, "p", "approved")
    after = q.status()
    assert after["reviews"] == before["reviews"] == []
    assert after["proposals"][0]["state"] == "proposed"


def test_legacy_reviewer_reject_is_normalized_to_terminal_rejected(tmp_path):
    q = queue(tmp_path)
    q.sync_diagnostic({"proposals": [{"id": "p", "area": "x", "tenant_id": "t1"}]})
    record_review(q, "p", "reject")
    status = q.status()
    proposal_id = status["proposals"][0]["proposal_id"]
    assert status["proposals"][0]["state"] == "rejected"
    assert status["reviews"][0]["verdict"] == "rejected"
    with pytest.raises(ValueError, match="terminal"):
        q.record_reviews({"reviews": [{"proposal_id": proposal_id, "verdict": "sandbox_ready"}]})
    with pytest.raises(ValueError, match="sandbox requires"):
        q.record_sandbox(attestation(proposal_id=proposal_id, db_identity=q.path))


def test_terminal_review_overwrite_fails_before_review_row(tmp_path):
    q = queue(tmp_path)
    proposal_id = prepare_approved(q)
    count = len(q.status()["reviews"])
    with pytest.raises(ValueError, match="terminal"):
        q.record_reviews({"reviews": [{"proposal_id": proposal_id, "verdict": "reject"}]})
    status = q.status()
    assert status["proposals"][0]["state"] == "approved"
    assert len(status["reviews"]) == count


def test_trusted_sandbox_attestation_is_required_and_checksum_bound(tmp_path):
    q = queue(tmp_path)
    q.sync_diagnostic({"proposals": [{"id": "p", "area": "x", "tenant_id": "t1"}]})
    record_review(q, "p", "sandbox_ready")
    with pytest.raises(PermissionError, match="trusted"):
        q.record_sandbox("p")  # type: ignore[arg-type]
    forged = attestation(db_identity=q.path)
    object.__setattr__(forged, "details_checksum", "0" * 64)
    with pytest.raises(ValueError, match="checksum mismatch"):
        q.record_sandbox(forged)
    assert q.status()["sandbox_runs"] == []


@pytest.mark.parametrize("blocked_state", ["proposed", "insufficient"])
def test_valid_sandbox_attestation_cannot_bypass_review_state(
    tmp_path, blocked_state
):
    q = queue(tmp_path / blocked_state)
    q.sync_diagnostic({"proposals": [{
        "id": "p", "area": "x", "tenant_id": "t1",
    }]})
    if blocked_state == "insufficient":
        record_review(q, "p", "insufficient")
    proposal_id = latest_instance(q)

    def snapshot():
        with sqlite3.connect(q.path) as db:
            return tuple(
                tuple(db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall())
                for table in (
                    "upgrade_proposals",
                    "upgrade_reviews",
                    "upgrade_sandbox_runs",
                    "upgrade_decisions",
                    "upgrade_activations",
                )
            )

    before = snapshot()
    with pytest.raises(ValueError, match="sandbox requires"):
        q.record_sandbox(attestation(proposal_id=proposal_id, db_identity=q.path))
    assert snapshot() == before
    with pytest.raises(ValueError, match="passed sandbox"):
        q.decide(
            proposal_id, "approve", "green",
            principal=principal("upgrade:approve"),
        )
    assert q.status()["sandbox_runs"] == []
    assert q.status()["decisions"] == []


def test_sandbox_failed_can_retry_to_passed(tmp_path):
    q = queue(tmp_path)
    q.sync_diagnostic({"proposals": [{"id": "p", "area": "x"}]})
    proposal_id = record_review(q, "p", "sandbox_ready")
    failed = q.record_sandbox(
        attestation(proposal_id=proposal_id, passed=False, db_identity=q.path)
    )
    assert failed["state"] == "sandbox_failed"
    passed = q.record_sandbox(
        attestation(proposal_id=proposal_id, db_identity=q.path)
    )
    assert passed["state"] == "sandbox_passed"
    assert len(q.status()["sandbox_runs"]) == 2


def test_logical_sandbox_resolver_ignores_newer_proposed_instance(tmp_path):
    q = queue(tmp_path)
    round_one = {"id": "dynamic", "area": "x", "evidence": {"count": 1}}
    round_two = {"id": "dynamic", "area": "x", "evidence": {"count": 2}}
    q.sync_diagnostic({"proposals": [round_one]})
    eligible_id = record_review(q, "dynamic", "sandbox_ready")
    q.sync_diagnostic({"proposals": [round_two]})
    binding = q.resolve_latest_sandbox_instance("dynamic")
    assert binding["proposal_id"] == eligible_id
    states = {
        row["proposal_id"]: row["state"] for row in q.status()["proposals"]
    }
    assert list(states.values()).count("proposed") == 1


def test_authority_rejects_cross_tenant_expired_and_missing_action(tmp_path):
    q = queue(tmp_path)
    q.sync_diagnostic({"proposals": [{"id": "p", "area": "x", "tenant_id": "t1"}]})
    record_review(q, "p", "sandbox_ready")
    proposal_id = latest_instance(q)
    q.record_sandbox(attestation(proposal_id=proposal_id, db_identity=q.path))
    for denied in (
        principal("upgrade:approve", tenant="t2"),
        principal("upgrade:approve", expired=True),
        principal("upgrade:reject"),
    ):
        with pytest.raises(PermissionError):
            q.decide(proposal_id, "approve", "green", principal=denied)
    assert q.status()["decisions"] == []


def test_tenantless_proposal_requires_platform_principal_for_all_mutations(tmp_path):
    q = queue(tmp_path)
    q.sync_diagnostic({"proposals": [{"id": "global", "area": "x"}]})
    proposal_id = record_review(q, "global", "sandbox_ready")
    q.record_sandbox(attestation(proposal_id=proposal_id, db_identity=q.path))
    tenant_operator = principal(
        "upgrade:approve", "upgrade:activate", "upgrade:rollback",
        tenant="t1",
    )
    platform_operator = principal(
        "upgrade:approve", "upgrade:activate", "upgrade:rollback",
        tenant="",
    )
    before = raw_five_table_snapshot(q)
    with pytest.raises(PermissionError, match="cross-tenant"):
        q.decide(
            proposal_id, "approve", "green", principal=tenant_operator
        )
    assert raw_five_table_snapshot(q) == before
    q.decide(
        proposal_id, "approve", "green", principal=platform_operator
    )
    before = raw_five_table_snapshot(q)
    with pytest.raises(PermissionError, match="cross-tenant"):
        q.activate(proposal_id, "release", principal=tenant_operator)
    assert raw_five_table_snapshot(q) == before
    q.activate(proposal_id, "release", principal=platform_operator)
    before = raw_five_table_snapshot(q)
    with pytest.raises(PermissionError, match="cross-tenant"):
        q.rollback(
            proposal_id, "old", "incident", principal=tenant_operator
        )
    assert raw_five_table_snapshot(q) == before
    q.rollback(
        proposal_id, "old", "incident", principal=platform_operator
    )
    assert q.status()["proposals"][0]["state"] == "rolled_back"


def test_sandbox_capability_wrong_key_tamper_replay_and_missing_verifier(tmp_path):
    q = queue(tmp_path)
    q.sync_diagnostic({"proposals": [{"id": "p", "area": "x"}]})
    proposal_id = record_review(q, "p", "sandbox_ready")
    before = q.status()

    wrong = SandboxAttestationAuthority(
        tmp_path / "wrong-capabilities.jsonl"
    )
    with pytest.raises(PermissionError, match="authority mismatch"):
        q.record_sandbox(
            attestation(
                proposal_id=proposal_id, authority=wrong, db_identity=q.path
            )
        )
    assert q.status() == before
    legitimate = attestation(proposal_id=proposal_id, db_identity=q.path)
    with pytest.raises(PermissionError, match="payload is invalid"):
        q.record_sandbox(replace(legitimate, passed=False))
    assert q.status() == before

    q.record_sandbox(legitimate)
    after = q.status()
    assert q.record_sandbox(legitimate)["idempotent"] is True
    assert q.status() == after

    no_verifier = UpgradeQueue(tmp_path / "no-verifier.sqlite3")
    no_verifier.sync_diagnostic({"proposals": [{"id": "x", "area": "x"}]})
    x_id = no_verifier.resolve_latest_reviewable_instance("x")["proposal_id"]
    binding = no_verifier.resolve_exact_review_instance({"id": "x", "area": "x"})
    no_verifier.record_reviews({"reviews": [{
        "proposal_id": x_id,
        "payload_sha256": binding["payload_sha256"],
        "verdict": "sandbox_ready",
    }]})
    no_verifier_before = no_verifier.status()
    with pytest.raises(PermissionError, match="verifier"):
        no_verifier.record_sandbox(
            attestation(proposal_id=x_id, db_identity=no_verifier.path)
        )
    assert no_verifier.status() == no_verifier_before


def test_rejected_early_capability_cannot_be_reused_after_state_becomes_eligible(
    tmp_path,
):
    authority = SandboxAttestationAuthority(tmp_path / "capabilities.jsonl")
    q = UpgradeQueue(
        tmp_path / "queue.sqlite3",
        authority=PrincipalAuthority(),
        catalog=Catalog(),
        activation_handler=Activator(),
        rollback_handler=Rollback(),
        sandbox_verifier=authority,
    )
    q.sync_diagnostic({"proposals": [{"id": "p", "area": "x"}]})
    proposal_id = latest_instance(q)
    capability = attestation(
        proposal_id=proposal_id, authority=authority, db_identity=q.path
    )
    before = q.status()
    with pytest.raises(ValueError, match="sandbox requires"):
        q.record_sandbox(capability)
    assert q.status() == before
    record_review(q, "p", "sandbox_ready")
    with pytest.raises(PermissionError, match="rejected"):
        q.record_sandbox(capability)


def test_consumed_capability_is_idempotent_only_for_exact_database(tmp_path):
    authority = SandboxAttestationAuthority(tmp_path / "capabilities.jsonl")

    def configured(path):
        q = UpgradeQueue(
            path,
            authority=PrincipalAuthority(),
            catalog=Catalog(),
            activation_handler=Activator(),
            rollback_handler=Rollback(),
            sandbox_verifier=authority,
        )
        q.sync_diagnostic({"proposals": [{"id": "p", "area": "x"}]})
        return q, record_review(q, "p", "sandbox_ready")

    first, first_id = configured(tmp_path / "first.sqlite3")
    second, second_id = configured(tmp_path / "second.sqlite3")
    assert first_id == second_id
    capability = attestation(
        proposal_id=first_id, authority=authority, db_identity=first.path
    )
    original = first.record_sandbox(capability)
    platform = principal(
        "upgrade:approve", "upgrade:activate", tenant=""
    )
    first.decide(first_id, "approve", "green", principal=platform)
    first.activate(first_id, "release", principal=platform)
    retry = UpgradeQueue(
        first.path,
        authority=PrincipalAuthority(),
        catalog=Catalog(),
        activation_handler=Activator(),
        rollback_handler=Rollback(),
        sandbox_verifier=SandboxAttestationAuthority(authority.path),
    ).record_sandbox(capability)
    assert retry["idempotent"] is True
    assert retry["run_id"] == original["run_id"]
    assert retry["state"] == original["state"] == "sandbox_passed"
    before = raw_five_table_snapshot(second)
    with pytest.raises(PermissionError, match="database mismatch"):
        second.record_sandbox(capability)
    assert raw_five_table_snapshot(second) == before


def test_incomplete_journal_tail_is_repaired_but_bad_complete_frame_fails(tmp_path):
    path = tmp_path / "capabilities.jsonl"
    authority = SandboxAttestationAuthority(path)
    first = attestation(authority=authority)
    with path.open("ab") as stream:
        stream.write(b'{"record":')
    restarted = SandboxAttestationAuthority(path)
    assert restarted.resolve(first.proof) == first
    second = attestation(authority=restarted)
    assert restarted.resolve(second.proof) == second
    with path.open("ab") as stream:
        stream.write(b'{"record":{}}\n')
    with pytest.raises(SafePathError):
        SandboxAttestationAuthority(path).resolve(first.proof)


@pytest.mark.parametrize("target", ["journal", "lock"])
def test_capability_journal_and_lock_symlinks_fail_closed(tmp_path, target):
    path = tmp_path / "capabilities.jsonl"
    victim = tmp_path / "victim"
    victim.write_text("", encoding="utf-8")
    link = path if target == "journal" else path.with_name(path.name + ".lock")
    link.symlink_to(victim)
    with pytest.raises((SafePathError, OSError)):
        attestation(authority=SandboxAttestationAuthority(path))


def test_capability_journal_and_lock_unsafe_modes_fail_closed(tmp_path):
    path = tmp_path / "capabilities.jsonl"
    authority = SandboxAttestationAuthority(path)
    issued = attestation(authority=authority)
    path.chmod(0o644)
    with pytest.raises(SafePathError, match="permissions"):
        authority.resolve(issued.proof)
    path.chmod(0o600)
    lock = path.with_name(path.name + ".lock")
    lock.chmod(0o644)
    with pytest.raises(SafePathError, match="permissions"):
        authority.resolve(issued.proof)


def test_two_queue_workers_persist_one_capability_row(tmp_path):
    authority = SandboxAttestationAuthority(tmp_path / "capabilities.jsonl")
    path = tmp_path / "queue.sqlite3"
    first = UpgradeQueue(path, sandbox_verifier=authority)
    first.sync_diagnostic({"proposals": [{"id": "p", "area": "x"}]})
    proposal_id = record_review(first, "p", "sandbox_ready")
    capability = attestation(
        proposal_id=proposal_id, authority=authority, db_identity=path
    )

    def consume_once():
        return UpgradeQueue(
            path, sandbox_verifier=SandboxAttestationAuthority(authority.path)
        ).record_sandbox(capability)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: consume_once(), range(2)))
    assert sorted(bool(result.get("idempotent")) for result in results) == [
        False, True,
    ]
    assert len(first.status()["sandbox_runs"]) == 1


def test_oversized_capability_frame_rejected_without_touching_journal(tmp_path):
    path = tmp_path / "capabilities.jsonl"
    authority = SandboxAttestationAuthority(path)
    baseline = attestation(authority=authority)
    before = path.read_bytes()
    details = {
        "candidate": {"family": "analysis", "revision": "abc"},
        "oversized": "x" * authority._MAX_FRAME_BYTES,
    }
    encoded = json.dumps(details, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(SafePathError, match="frame exceeds"):
        authority.issue(
            db_identity=str(
                (tmp_path / "queue.sqlite3").resolve(strict=False)
            ),
            proposal_id="p",
            candidate_family="analysis",
            candidate_revision="abc",
            run_id="oversized",
            runner_version="runner/v1",
            artifact_hash="sha256:abc",
            details_checksum=hashlib.sha256(encoded).hexdigest(),
            passed=True,
            completed_at=datetime.now(timezone.utc),
            details=details,
        )
    assert path.read_bytes() == before
    assert authority.resolve(baseline.proof) == baseline


def test_journal_exact_limit_succeeds_then_overflow_is_byte_identical(tmp_path):
    path = tmp_path / "capabilities.jsonl"
    authority = SandboxAttestationAuthority(path)
    record = {"probe": "near-limit"}
    envelope = {
        "record": record,
        "checksum": hashlib.sha256(
            json.dumps(
                record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
    }
    frame = (
        json.dumps(
            envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        + b"\n"
    )
    authority._MAX_BYTES = len(frame)
    with authority._locked() as parent_fd:
        authority._append(parent_fd, record)
    at_limit = path.read_bytes()
    assert len(at_limit) == authority._MAX_BYTES
    with authority._locked() as parent_fd:
        with pytest.raises(SafePathError, match="journal exceeds"):
            authority._append(parent_fd, record)
    assert path.read_bytes() == at_limit


def test_compaction_retention_boundary_restart_and_wrong_db(tmp_path):
    now = [datetime.now(timezone.utc)]
    clock = lambda: now[0]
    path = tmp_path / "capabilities.jsonl"
    authority = SandboxAttestationAuthority(path, clock=clock)
    q = UpgradeQueue(tmp_path / "queue.sqlite3", sandbox_verifier=authority)
    q.sync_diagnostic({"proposals": [{"id": "exact", "area": "x"}]})
    exact_id = record_review(q, "exact", "sandbox_ready")
    exact = attestation(
        proposal_id=exact_id, authority=authority, db_identity=q.path
    )
    q.record_sandbox(exact)

    undisposed = attestation(proposal_id="undisposed", authority=authority)
    rejected = attestation(
        proposal_id="rejected", authority=authority, db_identity=q.path
    )
    orphan = attestation(
        proposal_id="orphan", authority=authority, db_identity=q.path
    )
    wrong_path = tmp_path / "other.sqlite3"
    wrong_db = attestation(
        proposal_id="wrong-db", authority=authority, db_identity=wrong_path
    )
    binding = "a" * 64
    db_identity = str(q.path.resolve(strict=False))
    authority.reject(
        rejected, operation_binding=binding, db_identity=db_identity
    )
    with authority.consume(
        orphan, already_persisted=False, operation_binding=binding,
        db_identity=db_identity,
    ):
        pass
    with authority.consume(
        wrong_db, already_persisted=False, operation_binding=binding,
        db_identity=str(wrong_path.resolve(strict=False)),
    ):
        pass

    now[0] += timedelta(hours=24, seconds=299)
    assert q.compact_sandbox_journal() == 0
    for capability in (exact, undisposed, rejected, orphan, wrong_db):
        assert SandboxAttestationAuthority(path, clock=clock).resolve(
            capability.proof
        ) == capability

    now[0] += timedelta(seconds=2)
    q.compact_sandbox_journal()
    restarted = SandboxAttestationAuthority(path, clock=clock)
    for expired in (exact, undisposed, rejected):
        with pytest.raises(PermissionError, match="not issued"):
            restarted.resolve(expired.proof)
    assert restarted.resolve(orphan.proof) == orphan
    assert restarted.resolve(wrong_db.proof) == wrong_db


def test_compaction_clock_rollback_is_byte_identical(tmp_path):
    now = [datetime.now(timezone.utc)]
    path = tmp_path / "capabilities.jsonl"
    authority = SandboxAttestationAuthority(path, clock=lambda: now[0])
    attestation(authority=authority)
    before = path.read_bytes()
    now[0] -= timedelta(seconds=1)
    with pytest.raises(SafePathError, match="clock moved backwards"):
        authority.compact(
            db_identity=str((tmp_path / "queue.sqlite3").resolve(strict=False)),
            exact_capabilities={},
        )
    assert path.read_bytes() == before


def test_issue_reserves_full_lifecycle_then_compacts_at_high_water(tmp_path):
    now = [datetime.now(timezone.utc)]
    path = tmp_path / "capabilities.jsonl"
    authority = SandboxAttestationAuthority(path, clock=lambda: now[0])
    attestation(authority=authority)
    before = path.read_bytes()
    authority._MAX_BYTES = len(before) + authority._MAX_DISPOSITION_BYTES + 100
    with pytest.raises(JournalCapacityError):
        attestation(authority=authority)
    assert path.read_bytes() == before
    now[0] += authority._RETENTION + timedelta(seconds=1)
    authority.compact(
        db_identity=str((tmp_path / "queue.sqlite3").resolve(strict=False)),
        exact_capabilities={},
    )
    issued = attestation(authority=authority)
    assert authority.resolve(issued.proof) == issued


def test_compaction_replace_failure_preserves_old_journal(
    tmp_path, monkeypatch
):
    now = [datetime.now(timezone.utc)]
    path = tmp_path / "capabilities.jsonl"
    authority = SandboxAttestationAuthority(path, clock=lambda: now[0])
    issued = attestation(authority=authority)
    before = path.read_bytes()
    now[0] += authority._RETENTION + timedelta(seconds=1)
    monkeypatch.setattr(
        "trustforge.upgrade_adapters.os.replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("crash")),
    )
    with pytest.raises(OSError, match="crash"):
        authority.compact(
            db_identity=str((tmp_path / "queue.sqlite3").resolve(strict=False)),
            exact_capabilities={},
        )
    assert path.read_bytes() == before
    assert authority.resolve(issued.proof) == issued


def test_issue_rejects_long_or_wrong_database_identity_before_write(tmp_path):
    path = tmp_path / "capabilities.jsonl"
    authority = SandboxAttestationAuthority(path)
    with pytest.raises(SafePathError, match="database identity"):
        attestation(
            authority=authority,
            db_identity="/" + ("x" * 4097),
        )
    assert not path.exists()
    relative = "relative.sqlite3"
    with pytest.raises(SafePathError, match="database identity"):
        authority.issue(
            db_identity=relative,
            proposal_id="p",
            candidate_family="analysis",
            candidate_revision="abc",
            run_id="run",
            runner_version="runner/v1",
            artifact_hash="sha256:abc",
            details_checksum=hashlib.sha256(b"{}").hexdigest(),
            passed=True,
            completed_at=datetime.now(timezone.utc),
            details={},
        )
    assert not path.exists()


def test_near_capacity_parallel_dispositions_preserve_each_others_headroom(
    tmp_path,
):
    path = tmp_path / "capabilities.jsonl"
    authority = SandboxAttestationAuthority(path)
    db_identity = str((tmp_path / "queue.sqlite3").resolve(strict=False))
    first = attestation(
        proposal_id="a", authority=authority, db_identity=db_identity
    )
    second = attestation(
        proposal_id="b", authority=authority, db_identity=db_identity
    )
    with authority._locked() as parent_fd:
        entries = authority._read(parent_fd)
    authority._MAX_BYTES = len(path.read_bytes()) + sum(
        authority._disposition_reservation(entry["issued"])
        for entry in entries.values()
    )

    def consume_first():
        with authority.consume(
            first, already_persisted=False, operation_binding="a" * 64,
            db_identity=db_identity,
        ):
            pass

    def reject_second():
        authority.reject(
            second, operation_binding="b" * 64, db_identity=db_identity
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), (consume_first, reject_second)))
    restarted = SandboxAttestationAuthority(path)
    assert restarted.resolve(first.proof) == first
    assert restarted.resolve(second.proof) == second


@pytest.mark.parametrize("field", [
    "proposal_id", "passed", "artifact_hash", "run_id",
    "details_checksum", "operation_binding", "journal_id", "db_identity",
    "candidate_family", "candidate_revision", "runner_version",
    "completed_at", "details_content",
])
def test_compaction_aborts_on_each_db_journal_identity_conflict(
    tmp_path, field
):
    now = [datetime.now(timezone.utc)]
    authority = SandboxAttestationAuthority(
        tmp_path / "capabilities.jsonl", clock=lambda: now[0]
    )
    q = UpgradeQueue(tmp_path / "queue.sqlite3", sandbox_verifier=authority)
    q.sync_diagnostic({"proposals": [{"id": "p", "area": "x"}]})
    proposal_id = record_review(q, "p", "sandbox_ready")
    issued = attestation(
        proposal_id=proposal_id, authority=authority, db_identity=q.path
    )
    q.record_sandbox(issued)
    before = authority.path.read_bytes()
    with sqlite3.connect(q.path) as db:
        if field in {"proposal_id", "passed", "artifact_hash"}:
            value = {
                "proposal_id": "tampered",
                "passed": 0,
                "artifact_hash": "sha256:tampered",
            }[field]
            db.execute(
                f"UPDATE upgrade_sandbox_runs SET {field}=?",
                (value,),
            )
        else:
            row = db.execute(
                "SELECT payload_json FROM upgrade_sandbox_runs"
            ).fetchone()
            payload = json.loads(row[0])
            if field == "candidate_family":
                payload["candidate"]["family"] = "tampered"
            elif field == "candidate_revision":
                payload["candidate"]["revision"] = "tampered"
            elif field == "details_content":
                payload["tests"] = 999
            else:
                payload["attestation"][field] = "tampered"
            db.execute(
                "UPDATE upgrade_sandbox_runs SET payload_json=?",
                (json.dumps(payload, sort_keys=True),),
            )
    now[0] += authority._RETENTION + timedelta(seconds=1)
    with pytest.raises((ValueError, SafePathError)):
        q.compact_sandbox_journal()
    assert authority.path.read_bytes() == before


def test_legacy_v2_journal_is_preserved_with_explicit_compatibility_error(
    tmp_path,
):
    path = tmp_path / "capabilities.jsonl"
    record = {
        "schema": "trustforge.sandbox-capability/v2",
        "state": "issued",
        "capability_id": "a" * 64,
        "journal_id": hashlib.sha256(
            str(path.resolve(strict=False)).encode()
        ).hexdigest(),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "attestation": {},
    }
    envelope = {
        "record": record,
        "checksum": hashlib.sha256(
            json.dumps(
                record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
    }
    path.write_text(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    before = path.read_bytes()
    with pytest.raises(LegacyJournalError, match="v2"):
        SandboxAttestationAuthority(path).resolve("a" * 64)
    assert path.read_bytes() == before


def test_compaction_skips_known_legacy_db_row_and_reclaims_exact_v3(tmp_path):
    now = [datetime.now(timezone.utc)]
    authority = SandboxAttestationAuthority(
        tmp_path / "capabilities.jsonl", clock=lambda: now[0]
    )
    q = UpgradeQueue(tmp_path / "queue.sqlite3", sandbox_verifier=authority)
    q.sync_diagnostic({"proposals": [{"id": "p", "area": "x"}]})
    proposal_id = record_review(q, "p", "sandbox_ready")
    issued = attestation(
        proposal_id=proposal_id, authority=authority, db_identity=q.path
    )
    q.record_sandbox(issued)
    legacy = {
        "candidate": {"family": "analysis", "revision": "legacy"},
        "attestation": {
            "run_id": "legacy-run",
            "runner_version": "legacy/v1",
            "details_checksum": "0" * 64,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    with sqlite3.connect(q.path) as db:
        db.execute(
            "INSERT INTO upgrade_sandbox_runs "
            "(proposal_id,passed,artifact_hash,payload_json,created_at) "
            "VALUES (?,?,?,?,?)",
            ("legacy", 1, "sha256:legacy", json.dumps(legacy), 0.0),
        )
    now[0] += authority._RETENTION + timedelta(seconds=1)
    q.compact_sandbox_journal()
    with pytest.raises(PermissionError, match="not issued"):
        authority.resolve(issued.proof)


@pytest.mark.parametrize("legacy_attestation", [
    pytest.param("missing", id="missing"),
    pytest.param(None, id="null"),
    pytest.param([], id="list"),
])
def test_compaction_rejects_unverifiable_legacy_shapes_byte_identically(
    tmp_path, legacy_attestation
):
    authority = SandboxAttestationAuthority(tmp_path / "capabilities.jsonl")
    q = UpgradeQueue(tmp_path / "queue.sqlite3", sandbox_verifier=authority)
    issued = attestation(authority=authority, db_identity=q.path)
    payload = {"candidate": {"family": "analysis", "revision": "legacy"}}
    if legacy_attestation != "missing":
        payload["attestation"] = legacy_attestation
    with sqlite3.connect(q.path) as db:
        db.execute(
            "INSERT INTO upgrade_sandbox_runs "
            "(proposal_id,passed,artifact_hash,payload_json,created_at) "
            "VALUES (?,?,?,?,?)",
            ("legacy", 1, "sha256:legacy", json.dumps(payload), 0.0),
        )
    before = authority.path.read_bytes()
    with pytest.raises(ValueError, match="malformed"):
        q.compact_sandbox_journal()
    assert authority.path.read_bytes() == before


def test_compaction_rejects_partial_v3_downgrade_with_journal_unchanged(
    tmp_path,
):
    authority = SandboxAttestationAuthority(tmp_path / "capabilities.jsonl")
    q = UpgradeQueue(tmp_path / "queue.sqlite3", sandbox_verifier=authority)
    issued = attestation(authority=authority, db_identity=q.path)
    before = authority.path.read_bytes()
    partial = {
        "candidate": {"family": "analysis", "revision": "abc"},
        "attestation": {"capability_id": issued.proof},
    }
    with sqlite3.connect(q.path) as db:
        db.execute(
            "INSERT INTO upgrade_sandbox_runs "
            "(proposal_id,passed,artifact_hash,payload_json,created_at) "
            "VALUES (?,?,?,?,?)",
            ("partial", 1, "sha256:abc", json.dumps(partial), 0.0),
        )
    with pytest.raises(ValueError, match="malformed"):
        q.compact_sandbox_journal()
    assert authority.path.read_bytes() == before


def test_high_water_compact_with_legacy_row_then_issue_and_consume_e2e(
    tmp_path,
):
    now = [datetime.now(timezone.utc)]
    authority = SandboxAttestationAuthority(
        tmp_path / "capabilities.jsonl", clock=lambda: now[0]
    )
    q = UpgradeQueue(tmp_path / "queue.sqlite3", sandbox_verifier=authority)
    expired = attestation(
        proposal_id="x" * 71, authority=authority, db_identity=q.path
    )
    with authority._locked() as parent_fd:
        issued_record = authority._read(parent_fd)[expired.proof]["issued"]
    authority._MAX_BYTES = (
        len(authority.path.read_bytes())
        + authority._disposition_reservation(issued_record)
    )
    with sqlite3.connect(q.path) as db:
        db.execute(
            "INSERT INTO upgrade_sandbox_runs "
            "(proposal_id,passed,artifact_hash,payload_json,created_at) "
            "VALUES (?,?,?,?,?)",
            (
                "legacy", 1, "sha256:legacy",
                json.dumps({
                    "candidate": {"family": "analysis", "revision": "legacy"},
                    "attestation": {
                        "run_id": "legacy", "runner_version": "legacy/v1",
                        "details_checksum": "0" * 64,
                        "completed_at": now[0].isoformat(),
                    },
                }),
                0.0,
            ),
        )
    with pytest.raises(JournalCapacityError):
        attestation(authority=authority, db_identity=q.path)
    now[0] += authority._RETENTION + timedelta(seconds=1)
    q.compact_sandbox_journal()
    q.sync_diagnostic({"proposals": [{"id": "fresh", "area": "x"}]})
    proposal_id = record_review(q, "fresh", "sandbox_ready")
    fresh = attestation(
        proposal_id=proposal_id, authority=authority, db_identity=q.path
    )
    assert q.record_sandbox(fresh)["state"] == "sandbox_passed"
    assert len(q.status()["sandbox_runs"]) == 2

def test_caller_actor_qa_is_not_an_authority_input(tmp_path):
    q = queue(tmp_path)
    with pytest.raises(TypeError):
        q.decide("p", "approve", "qa", "reason")  # type: ignore[misc]


def test_fake_activation_failure_leaves_state_and_rows_unchanged(tmp_path):
    fake = Activator(fail=True)
    q = queue(tmp_path, activator=fake)
    proposal_id = prepare_approved(q)
    with pytest.raises(RuntimeError, match="pointer failure"):
        q.activate(proposal_id, "release", principal=principal("upgrade:activate"))
    status = q.status()
    assert status["proposals"][0]["state"] == "approved"
    assert status["activations"] == []


def test_activation_retry_after_lost_handler_response_reuses_operation_id(tmp_path):
    fake = LostResponseActivator()
    q = queue(tmp_path, activator=fake)
    proposal_id = prepare_approved(q)
    with pytest.raises(RuntimeError, match="lost response"):
        q.activate(proposal_id, "release", principal=principal("upgrade:activate"))
    assert q.status()["proposals"][0]["state"] == "approved"
    result = q.activate(proposal_id, "release retry", principal=principal("upgrade:activate"))
    assert result["state"] == "activated"
    assert len(set(fake.calls)) == 1
    assert len(q.status()["activations"]) == 1


def test_activation_and_rollback_are_idempotent_with_stable_operation_ids(tmp_path):
    activate, rollback = Activator(), Rollback()
    q = queue(tmp_path, activator=activate, rollback=rollback)
    proposal_id = prepare_approved(q)
    first = q.activate(proposal_id, "release", principal=principal("upgrade:activate"))
    second = q.activate(proposal_id, "retry", principal=principal("upgrade:activate"))
    assert first == second
    assert len(q.status()["activations"]) == 1
    rolled = q.rollback(proposal_id, "old", "incident", principal=principal("upgrade:rollback"))
    retried = q.rollback(proposal_id, "old", "retry", principal=principal("upgrade:rollback"))
    assert rolled == retried
    assert len(q.status()["activations"]) == 2


def test_rollback_only_allows_recorded_previous_revision(tmp_path):
    q = queue(tmp_path)
    proposal_id = prepare_approved(q)
    q.activate(proposal_id, "release", principal=principal("upgrade:activate"))
    with pytest.raises(ValueError, match="previous revision"):
        q.rollback(proposal_id, "attacker-hash", "bad", principal=principal("upgrade:rollback"))
    assert q.status()["proposals"][0]["state"] == "activated"


def test_fake_rollback_failure_leaves_pointer_state_record_unchanged(tmp_path):
    fake = Rollback(fail=True)
    q = queue(tmp_path, rollback=fake)
    proposal_id = prepare_approved(q)
    q.activate(proposal_id, "release", principal=principal("upgrade:activate"))
    with pytest.raises(RuntimeError, match="rollback failure"):
        q.rollback(proposal_id, "old", "incident", principal=principal("upgrade:rollback"))
    status = q.status()
    assert status["proposals"][0]["state"] == "activated"
    assert len(status["activations"]) == 1


def _seed_old_pointer(log_path, artifact):
    from trustforge.skill_changes import approve, stage
    from trustforge.skills import canonical_json, artifact_hash, skill_id_for

    revision = artifact_hash(artifact)
    module_id = skill_id_for(str(artifact["family"]))
    stage(module_id, canonical_json(artifact), "baseline", log_path=log_path)
    approve(module_id, revision, {"seed": True}, log_path=log_path)
    return revision, module_id


def _candidate(artifact):
    from trustforge.skills import artifact_hash, skill_id_for

    revision = artifact_hash(artifact)
    family = str(artifact["family"])
    return UpgradeCandidate(
        family, revision, f"sha256:{revision}", artifact, skill_id_for(family)
    )


def test_real_hermes_journal_recovers_crash_after_pointer_before_completed(tmp_path):
    log_path = tmp_path / "changes.jsonl"
    receipt_path = tmp_path / "receipts.jsonl"
    old, _ = _seed_old_pointer(log_path, {"family": "analysis", "rules": ["old"]})
    candidate = _candidate({"family": "analysis", "rules": ["new"]})
    lost = {"once": True}

    def lose_after_pointer(_receipt):
        if lost["once"]:
            lost["once"] = False
            raise RuntimeError("lost response")

    first = HermesActivationHandler(
        log_path, receipt_path=receipt_path, after_pointer=lose_after_pointer
    )
    with pytest.raises(RuntimeError, match="lost response"):
        first.activate(
            candidate, proposal_id="p", operation_id="op-activate",
            expected_revision=old,
        )
    conflicting = _candidate({"family": "analysis", "rules": ["conflict"]})
    with pytest.raises(RuntimeError, match="payload conflict"):
        HermesActivationHandler(log_path, receipt_path=receipt_path).activate(
            conflicting, proposal_id="p", operation_id="op-activate",
            expected_revision=old,
        )
    retried = HermesActivationHandler(log_path, receipt_path=receipt_path).activate(
        candidate, proposal_id="p", operation_id="op-activate",
        expected_revision=old,
    )
    assert retried.previous_revision == old
    assert retried.revision == candidate.revision
    rollback_lost = {"once": True}

    def lose_rollback(_receipt):
        if rollback_lost["once"]:
            rollback_lost["once"] = False
            raise RuntimeError("rollback lost response")

    rollback_handler = HermesRollbackHandler(
        log_path, receipt_path=receipt_path, after_pointer=lose_rollback
    )
    with pytest.raises(RuntimeError, match="rollback lost response"):
        rollback_handler.rollback(
            "analysis", old, reason="original incident",
            operation_id="op-rollback", expected_revision=candidate.revision,
        )
    # reason is non-semantic for operation identity; recovery uses the reason
    # durably captured by the prepared record.
    rolled = HermesRollbackHandler(log_path, receipt_path=receipt_path).rollback(
        "analysis", old, reason="different retry wording",
        operation_id="op-rollback", expected_revision=candidate.revision,
    )
    assert rolled.previous_revision == candidate.revision
    assert rolled.revision == old


def test_real_hermes_handlers_serialize_cas_across_instances(tmp_path):
    log_path = tmp_path / "changes.jsonl"
    receipt_path = tmp_path / "receipts.jsonl"
    old, _ = _seed_old_pointer(log_path, {"family": "analysis", "rules": ["old"]})
    candidates = [
        _candidate({"family": "analysis", "rules": ["one"]}),
        _candidate({"family": "analysis", "rules": ["two"]}),
    ]

    def run(index):
        return HermesActivationHandler(log_path, receipt_path=receipt_path).activate(
            candidates[index], proposal_id=f"p{index}",
            operation_id=f"op-{index}", expected_revision=old,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, index) for index in range(2)]
    successes, failures = [], []
    for future in futures:
        try:
            successes.append(future.result())
        except RuntimeError as exc:
            failures.append(exc)
    assert len(successes) == 1
    assert len(failures) == 1
    assert "compare-and-swap" in str(failures[0])


def test_queue_first_activation_uses_existing_hermes_pointer_snapshot(tmp_path):
    from trustforge.skills import artifact_hash, skill_id_for

    log_path = tmp_path / "changes.jsonl"
    receipt_path = tmp_path / "receipts.jsonl"
    old, _ = _seed_old_pointer(log_path, {"family": "analysis", "rules": ["old"]})
    artifact = {"family": "analysis", "rules": ["new"]}
    revision = artifact_hash(artifact)

    class InlineCatalog:
        def resolve(self, family, supplied_revision, artifact_hash_value):
            assert supplied_revision == revision
            return UpgradeCandidate(
                family, supplied_revision, artifact_hash_value,
                artifact, skill_id_for(family),
            )

    crashed = {"once": True}

    def crash_after_pointer(_receipt):
        if crashed["once"]:
            crashed["once"] = False
            raise RuntimeError("queue lost handler response")

    q = UpgradeQueue(
        tmp_path / "queue.sqlite3",
        authority=PrincipalAuthority(),
        catalog=InlineCatalog(),
        activation_handler=HermesActivationHandler(
            log_path, receipt_path=receipt_path, after_pointer=crash_after_pointer
        ),
        rollback_handler=HermesRollbackHandler(
            log_path, receipt_path=receipt_path
        ),
        sandbox_verifier=SANDBOX_AUTHORITY,
    )
    q.sync_diagnostic({"proposals": [{"id": "p", "area": "x", "tenant_id": "t1"}]})
    record_review(q, "p", "sandbox_ready")
    proposal_id = latest_instance(q)
    q.record_sandbox(attestation(
        proposal_id=proposal_id, revision=revision, db_identity=q.path
    ))
    q.decide(proposal_id, "approve", "green", principal=principal("upgrade:approve"))
    with pytest.raises(RuntimeError, match="lost handler response"):
        q.activate(proposal_id, "release", principal=principal("upgrade:activate"))
    assert q.status()["proposals"][0]["state"] == "approved"
    q.activation_handler = HermesActivationHandler(
        log_path, receipt_path=receipt_path
    )
    result = q.activate(proposal_id, "release retry", principal=principal("upgrade:activate"))
    assert result["previous_revision"] == old
    assert result["revision"] == revision


def test_real_hermes_same_operation_different_payload_conflicts(tmp_path):
    log_path = tmp_path / "changes.jsonl"
    receipt_path = tmp_path / "receipts.jsonl"
    old, _ = _seed_old_pointer(log_path, {"family": "analysis", "rules": ["old"]})
    handler = HermesActivationHandler(log_path, receipt_path=receipt_path)
    first = _candidate({"family": "analysis", "rules": ["one"]})
    second = _candidate({"family": "analysis", "rules": ["two"]})
    handler.activate(first, proposal_id="p", operation_id="same", expected_revision=old)
    with pytest.raises(RuntimeError, match="payload conflict"):
        handler.activate(second, proposal_id="p", operation_id="same", expected_revision=old)


def test_real_hermes_corrupt_receipt_fails_closed_before_pointer_change(tmp_path):
    from trustforge.skill_changes import active_revision

    log_path = tmp_path / "changes.jsonl"
    receipt_path = tmp_path / "receipts.jsonl"
    old, module_id = _seed_old_pointer(log_path, {"family": "analysis", "rules": ["old"]})
    receipt_path.write_text("{corrupt\n", encoding="utf-8")
    candidate = _candidate({"family": "analysis", "rules": ["new"]})
    with pytest.raises(SafePathError, match="corrupt"):
        HermesActivationHandler(log_path, receipt_path=receipt_path).activate(
            candidate, proposal_id="p", operation_id="op", expected_revision=old,
        )
    assert active_revision(module_id, log_path=log_path) == old


def test_real_hermes_corrupt_change_log_fails_closed(tmp_path):
    log_path = tmp_path / "changes.jsonl"
    receipt_path = tmp_path / "receipts.jsonl"
    old, _ = _seed_old_pointer(log_path, {"family": "analysis", "rules": ["old"]})
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("{corrupt\n")
    candidate = _candidate({"family": "analysis", "rules": ["new"]})
    handler = HermesActivationHandler(log_path, receipt_path=receipt_path)
    with pytest.raises(SafePathError, match="change log"):
        handler.activate(
            candidate, proposal_id="p", operation_id="op", expected_revision=old,
        )
    assert not receipt_path.exists()


def test_completed_receipt_replay_rejects_displacement_and_pointer_cycle(tmp_path):
    log_path = tmp_path / "changes.jsonl"
    receipt_path = tmp_path / "receipts.jsonl"
    old, _ = _seed_old_pointer(log_path, {"family": "analysis", "rules": ["old"]})
    first = _candidate({"family": "analysis", "rules": ["first"]})
    second = _candidate({"family": "analysis", "rules": ["second"]})
    handler = HermesActivationHandler(log_path, receipt_path=receipt_path)
    handler.activate(
        first, proposal_id="a", operation_id="activate-a", expected_revision=old
    )
    handler.activate(
        second, proposal_id="b", operation_id="activate-b",
        expected_revision=first.revision,
    )
    with pytest.raises(OperationDisplacedError, match="displaced"):
        handler.activate(
            first, proposal_id="a", operation_id="activate-a",
            expected_revision=old,
        )
    HermesRollbackHandler(log_path, receipt_path=receipt_path).rollback(
        "analysis", first.revision, reason="cycle",
        operation_id="cycle-back", expected_revision=second.revision,
    )
    with pytest.raises(OperationDisplacedError, match="displaced"):
        handler.activate(
            first, proposal_id="a", operation_id="activate-a",
            expected_revision=old,
        )


def test_queue_retry_of_completed_but_unpersisted_activation_cannot_displace_newer(tmp_path):
    from trustforge.skill_changes import active_revision
    from trustforge.skills import artifact_hash, skill_id_for

    log_path = tmp_path / "changes.jsonl"
    receipt_path = tmp_path / "receipts.jsonl"
    old, module_id = _seed_old_pointer(
        log_path, {"family": "analysis", "rules": ["old"]}
    )
    artifacts = {
        artifact_hash(value): value
        for value in (
            {"family": "analysis", "rules": ["a"]},
            {"family": "analysis", "rules": ["b"]},
        )
    }

    class CatalogByRevision:
        def resolve(self, family, revision, artifact_hash_value):
            return UpgradeCandidate(
                family, revision, artifact_hash_value,
                artifacts[revision], skill_id_for(family),
            )

    lost = {"once": True}

    def lose_sqlite_window(_receipt):
        if lost["once"]:
            lost["once"] = False
            raise RuntimeError("completed receipt response lost")

    q = UpgradeQueue(
        tmp_path / "queue.sqlite3",
        authority=PrincipalAuthority(),
        catalog=CatalogByRevision(),
        activation_handler=HermesActivationHandler(
            log_path, receipt_path=receipt_path, after_receipt=lose_sqlite_window
        ),
        rollback_handler=HermesRollbackHandler(log_path, receipt_path=receipt_path),
        sandbox_verifier=SANDBOX_AUTHORITY,
    )

    def approve_proposal(proposal_id, revision):
        q.sync_diagnostic({"proposals": [{
            "id": proposal_id, "area": "x", "tenant_id": "t1",
        }]})
        durable_id = record_review(q, proposal_id, "sandbox_ready")
        q.record_sandbox(attestation(
            proposal_id=durable_id, revision=revision, db_identity=q.path
        ))
        q.decide(
            durable_id, "approve", "green",
            principal=principal("upgrade:approve"),
        )
        return durable_id

    revisions = list(artifacts)
    durable_a = approve_proposal("a", revisions[0])
    with pytest.raises(RuntimeError, match="response lost"):
        q.activate(durable_a, "release a", principal=principal("upgrade:activate"))
    assert q.status()["proposals"][0]["state"] == "approved"

    q.activation_handler = HermesActivationHandler(log_path, receipt_path=receipt_path)
    durable_b = approve_proposal("b", revisions[1])
    q.activate(durable_b, "release b", principal=principal("upgrade:activate"))
    before = q.status()
    assert active_revision(module_id, log_path=log_path) == revisions[1]

    with pytest.raises(OperationDisplacedError):
        q.activate(durable_a, "retry a", principal=principal("upgrade:activate"))
    after = q.status()
    assert after["proposals"] == before["proposals"]
    assert after["activations"] == before["activations"]
    assert active_revision(module_id, log_path=log_path) == revisions[1]
    assert len(after["activations"]) == 1


def test_queue_retry_of_unpersisted_rollback_cannot_displace_newer_activation(tmp_path):
    from trustforge.skill_changes import active_revision
    from trustforge.skills import artifact_hash, skill_id_for

    log_path = tmp_path / "changes.jsonl"
    receipt_path = tmp_path / "receipts.jsonl"
    old, module_id = _seed_old_pointer(
        log_path, {"family": "analysis", "rules": ["old"]}
    )
    artifact_a = {"family": "analysis", "rules": ["a"]}
    artifact_b = {"family": "analysis", "rules": ["b"]}
    artifacts = {
        artifact_hash(artifact_a): artifact_a,
        artifact_hash(artifact_b): artifact_b,
    }

    class CatalogByRevision:
        def resolve(self, family, revision, artifact_hash_value):
            return UpgradeCandidate(
                family, revision, artifact_hash_value,
                artifacts[revision], skill_id_for(family),
            )

    def configured_queue(path):
        return UpgradeQueue(
            path,
            authority=PrincipalAuthority(),
            catalog=CatalogByRevision(),
            activation_handler=HermesActivationHandler(
                log_path, receipt_path=receipt_path
            ),
            rollback_handler=HermesRollbackHandler(
                log_path, receipt_path=receipt_path
            ),
            sandbox_verifier=SANDBOX_AUTHORITY,
        )

    def approve_and_activate(q, proposal_id, revision):
        q.sync_diagnostic({"proposals": [{
            "id": proposal_id, "area": "x", "tenant_id": "t1",
        }]})
        durable_id = record_review(q, proposal_id, "sandbox_ready")
        q.record_sandbox(attestation(
            proposal_id=durable_id, revision=revision, db_identity=q.path
        ))
        q.decide(
            durable_id, "approve", "green",
            principal=principal("upgrade:approve"),
        )
        q.activate(durable_id, "release", principal=principal("upgrade:activate"))
        return durable_id

    revision_a, revision_b = list(artifacts)
    queue_a = configured_queue(tmp_path / "queue-a.sqlite3")
    durable_a = approve_and_activate(queue_a, "a", revision_a)
    lost = {"once": True}

    def lose_rollback_sqlite_window(_receipt):
        if lost["once"]:
            lost["once"] = False
            raise RuntimeError("rollback completed response lost")

    queue_a.rollback_handler = HermesRollbackHandler(
        log_path,
        receipt_path=receipt_path,
        after_receipt=lose_rollback_sqlite_window,
    )
    with pytest.raises(RuntimeError, match="response lost"):
        queue_a.rollback(
            durable_a, old, "incident", principal=principal("upgrade:rollback")
        )
    assert queue_a.status()["proposals"][0]["state"] == "activated"

    queue_b = configured_queue(tmp_path / "queue-b.sqlite3")
    approve_and_activate(queue_b, "b", revision_b)
    assert active_revision(module_id, log_path=log_path) == revision_b
    before = queue_a.status()

    queue_a.rollback_handler = HermesRollbackHandler(
        log_path, receipt_path=receipt_path
    )
    with pytest.raises(OperationDisplacedError):
        queue_a.rollback(
            durable_a, old, "different retry reason",
            principal=principal("upgrade:rollback"),
        )
    after = queue_a.status()
    assert after["proposals"] == before["proposals"]
    assert after["activations"] == before["activations"]
    assert active_revision(module_id, log_path=log_path) == revision_b


def test_prepared_activation_recovery_rejects_cycle_back_to_target(tmp_path):
    log_path = tmp_path / "changes.jsonl"
    receipt_path = tmp_path / "receipts.jsonl"
    old, _ = _seed_old_pointer(log_path, {"family": "analysis", "rules": ["old"]})
    target = _candidate({"family": "analysis", "rules": ["target"]})
    newer = _candidate({"family": "analysis", "rules": ["newer"]})
    crashed = {"once": True}

    def crash(_receipt):
        if crashed["once"]:
            crashed["once"] = False
            raise RuntimeError("crash after target pointer")

    with pytest.raises(RuntimeError, match="crash after target"):
        HermesActivationHandler(
            log_path, receipt_path=receipt_path, after_pointer=crash
        ).activate(
            target, proposal_id="a", operation_id="prepared-a",
            expected_revision=old,
        )
    HermesActivationHandler(log_path, receipt_path=receipt_path).activate(
        newer, proposal_id="b", operation_id="newer-b",
        expected_revision=target.revision,
    )
    HermesRollbackHandler(log_path, receipt_path=receipt_path).rollback(
        "analysis", target.revision, reason="cycle",
        operation_id="cycle-target", expected_revision=newer.revision,
    )
    for _ in range(2):
        with pytest.raises(OperationDisplacedError, match="displaced"):
            HermesActivationHandler(log_path, receipt_path=receipt_path).activate(
                target, proposal_id="a", operation_id="prepared-a",
                expected_revision=old,
            )


def test_prepared_rollback_recovery_rejects_cycle_back_to_target(tmp_path):
    log_path = tmp_path / "changes.jsonl"
    receipt_path = tmp_path / "receipts.jsonl"
    old, _ = _seed_old_pointer(log_path, {"family": "analysis", "rules": ["old"]})
    active = _candidate({"family": "analysis", "rules": ["active"]})
    newer = _candidate({"family": "analysis", "rules": ["newer"]})
    HermesActivationHandler(log_path, receipt_path=receipt_path).activate(
        active, proposal_id="a", operation_id="activate-a",
        expected_revision=old,
    )
    crashed = {"once": True}

    def crash(_receipt):
        if crashed["once"]:
            crashed["once"] = False
            raise RuntimeError("crash after rollback pointer")

    with pytest.raises(RuntimeError, match="crash after rollback"):
        HermesRollbackHandler(
            log_path, receipt_path=receipt_path, after_pointer=crash
        ).rollback(
            "analysis", old, reason="incident",
            operation_id="prepared-rollback", expected_revision=active.revision,
        )
    HermesActivationHandler(log_path, receipt_path=receipt_path).activate(
        newer, proposal_id="b", operation_id="activate-b",
        expected_revision=old,
    )
    HermesRollbackHandler(log_path, receipt_path=receipt_path).rollback(
        "analysis", old, reason="cycle",
        operation_id="cycle-old", expected_revision=newer.revision,
    )
    for _ in range(2):
        with pytest.raises(OperationDisplacedError, match="displaced"):
            HermesRollbackHandler(log_path, receipt_path=receipt_path).rollback(
                "analysis", old, reason="retry",
                operation_id="prepared-rollback",
                expected_revision=active.revision,
            )


def test_improvement_dynamic_rounds_create_content_addressed_instances(tmp_path):
    from trustforge.improvement import diagnose

    path = tmp_path / "queue.sqlite3"
    q = UpgradeQueue(
        path,
        authority=PrincipalAuthority(),
        catalog=Catalog(),
        activation_handler=Activator(),
        rollback_handler=Rollback(),
        sandbox_verifier=SANDBOX_AUTHORITY,
    )
    round_one = diagnose(
        scheduler_runs=[{"failure_count": 1, "failures": ["timeout"]}],
        generated_at="2026-01-01T00:00:00Z",
    )
    round_two = diagnose(
        scheduler_runs=[
            {"failure_count": 1, "failures": ["timeout"]},
            {"failure_count": 1, "failures": ["timeout"]},
        ],
        generated_at="2026-01-02T00:00:00Z",
    )
    q.sync_diagnostic(round_one)
    first_id = record_review(
        q, "source-reliability-investigation", "sandbox_ready"
    )
    q.record_sandbox(attestation(proposal_id=first_id, db_identity=q.path))
    q.decide(
        first_id,
        "approve",
        "green",
        principal=principal("upgrade:approve", tenant=""),
    )

    q.sync_diagnostic(round_two)
    status = q.status()
    instances = [
        row for row in status["proposals"]
        if row["logical_id"] == "source-reliability-investigation"
    ]
    assert len(instances) == 2
    assert {row["state"] for row in instances} == {"approved", "proposed"}
    assert all(row["proposal_id"].startswith("sha256:") for row in instances)

    # A job resolves logical identity once, then commits only that durable
    # instance.
    record_review(q, "source-reliability-investigation", "sandbox_ready")
    states = {row["proposal_id"]: row["state"] for row in q.status()["proposals"]}
    assert states[first_id] == "approved"
    assert list(states.values()).count("llm_reviewed") == 1

    q.sync_diagnostic(round_two)
    assert len([
        row for row in q.status()["proposals"]
        if row["logical_id"] == "source-reliability-investigation"
    ]) == 2


def test_logical_review_rejects_equal_timestamp_ambiguity(tmp_path):
    q = queue(tmp_path)
    q.sync_diagnostic({"proposals": [
        {"id": "same", "area": "x", "evidence": {"count": 1}},
        {"id": "same", "area": "x", "evidence": {"count": 2}},
    ]})
    before = q.status()
    with pytest.raises(ValueError, match="ambiguously"):
        q.resolve_review_instance("same")
    with pytest.raises(ValueError, match="durable"):
        q.record_reviews({"reviews": [{
            "proposal_id": "same", "verdict": "sandbox_ready",
        }]})
    after = q.status()
    assert after["proposals"] == before["proposals"]
    assert after["reviews"] == []


def test_inflight_review_binding_does_not_drift_to_new_round(tmp_path):
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "review_hermes_upgrades.py"
    spec = importlib.util.spec_from_file_location("review_hermes_upgrades_test", script)
    assert spec and spec.loader
    reviewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reviewer)
    q = queue(tmp_path)
    round_one = {"proposals": [{
        "id": "dynamic", "area": "x", "evidence": {"count": 1},
    }]}
    round_two = {"proposals": [{
        "id": "dynamic", "area": "x", "evidence": {"count": 2},
    }]}
    q.sync_diagnostic(round_one)
    bound, bindings = reviewer._bind_diagnostic(q, round_one)
    first_id = bound["proposals"][0]["id"]
    q.sync_diagnostic(round_two)
    result = reviewer._attach_review_bindings(
        {
            "status": "reviewed",
            "reviews": [{
                "proposal_id": first_id,
                "verdict": "sandbox_ready",
                "reasons": [],
                "required_checks": [],
            }],
        },
        bindings,
    )
    q.record_reviews(result)
    states = {
        row["proposal_id"]: row["state"] for row in q.status()["proposals"]
    }
    assert states[first_id] == "llm_reviewed"
    assert list(states.values()).count("proposed") == 1


def test_stale_review_source_binds_exact_round_not_latest(tmp_path):
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "review_hermes_upgrades.py"
    spec = importlib.util.spec_from_file_location("review_exact_stale_test", script)
    assert spec and spec.loader
    reviewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reviewer)
    q = queue(tmp_path)
    round_one_proposal = {
        "id": "dynamic", "area": "x", "evidence": {"count": 1},
    }
    round_two_proposal = {
        "id": "dynamic", "area": "x", "evidence": {"count": 2},
    }
    q.sync_diagnostic({"proposals": [round_one_proposal]})
    q.sync_diagnostic({"proposals": [round_two_proposal]})

    bound, bindings = reviewer._bind_diagnostic(
        q, {"proposals": [round_one_proposal]}
    )
    bound_row = bound["proposals"][0]
    expected = q.resolve_exact_review_instance(round_one_proposal)
    assert bound_row["id"] == expected["proposal_id"]
    assert bound_row["payload_sha256"] == expected["payload_sha256"]
    assert bound_row["evidence"] == {"count": 1}
    result = reviewer._attach_review_bindings(
        {"reviews": [{
            "proposal_id": bound_row["id"],
            "verdict": "sandbox_ready",
            "reasons": [],
            "required_checks": [],
        }]},
        bindings,
    )
    q.record_reviews(result)
    states = {
        row["proposal_id"]: row["state"] for row in q.status()["proposals"]
    }
    assert states[expected["proposal_id"]] == "llm_reviewed"
    second = q.resolve_exact_review_instance(round_two_proposal)
    assert states[second["proposal_id"]] == "proposed"

    tampered = {
        "id": "dynamic", "area": "x", "evidence": {"count": 999},
    }
    with pytest.raises(ValueError, match="not ingested"):
        reviewer._bind_diagnostic(q, {"proposals": [tampered]})


def test_sandbox_cli_resolves_logical_once_before_new_round(
    tmp_path, monkeypatch
):
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_skill_sandbox.py"
    spec = importlib.util.spec_from_file_location("run_skill_sandbox_binding", script)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    queue_path = tmp_path / "queue.sqlite3"
    q = UpgradeQueue(queue_path)
    round_one = {"proposals": [{
        "id": "dynamic", "area": "x", "evidence": {"count": 1},
    }]}
    round_two = {"proposals": [{
        "id": "dynamic", "area": "x", "evidence": {"count": 2},
    }]}
    q.sync_diagnostic(round_one)
    first_id = record_review(q, "dynamic", "sandbox_ready")
    artifact = tmp_path / "candidate.json"
    artifact.write_text(
        json.dumps({"family": "analysis", "rules": ["bounded"]}),
        encoding="utf-8",
    )
    output = tmp_path / "sandbox.json"
    monkeypatch.setenv("TRUSTFORGE_SKILL_ROOT", str(tmp_path / "skills"))
    invoked = {"count": 0}

    def run(_argv):
        if invoked["count"] == 0:
            UpgradeQueue(queue_path).sync_diagnostic(round_two)
        invoked["count"] += 1
        return {"argv": _argv, "returncode": 0, "stdout_tail": "ok", "stderr_tail": ""}

    monkeypatch.setattr(runner, "_run", run)
    assert runner.main([
        str(artifact),
        "--proposal-id", "dynamic",
        "--queue-db", str(queue_path),
        "--out", str(output),
    ]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["proposal_binding"]["proposal_id"] == first_id
    assert "capability" not in json.dumps(result).lower()
    status = q.status()
    assert len(status["proposals"]) == 2
    assert len(status["sandbox_runs"]) == 1
    assert status["sandbox_runs"][0]["proposal_id"] == first_id


def test_activate_and_rollback_reject_missing_or_obsolete_decision_binding(tmp_path):
    first = queue(tmp_path / "first")
    first_id = prepare_approved(first)
    with sqlite3.connect(first.path) as db:
        db.execute(
            "UPDATE upgrade_decisions SET payload_json='{}' WHERE proposal_id=?",
            (first_id,),
        )
    with pytest.raises(ValueError, match="missing or obsolete"):
        first.activate(
            first_id, "release", principal=principal("upgrade:activate")
        )
    assert first.status()["proposals"][0]["state"] == "approved"
    assert first.status()["activations"] == []

    second = queue(tmp_path / "second")
    second_id = prepare_approved(second)
    second.activate(second_id, "release", principal=principal("upgrade:activate"))
    before = second.status()
    with sqlite3.connect(second.path) as db:
        db.execute(
            "UPDATE upgrade_decisions SET payload_json=? WHERE proposal_id=?",
            (json.dumps({"previous_state": "sandbox_passed"}), second_id),
        )
    with pytest.raises(ValueError, match="missing or obsolete"):
        second.rollback(
            second_id, "old", "incident", principal=principal("upgrade:rollback")
        )
    after = second.status()
    assert after["proposals"] == before["proposals"]
    assert after["activations"] == before["activations"]
