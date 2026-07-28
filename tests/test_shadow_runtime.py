"""Bounded formal shadow runtime regressions for #732 PR3."""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import asdict
import json
import multiprocessing
from functools import partial
from pathlib import Path
import statistics
import hashlib
import ast
from datetime import datetime, timezone

import pytest

from trustforge.agent.shadow_evidence_store import (
    ShadowEvidenceStore,
    ShadowEvidenceStoreError,
)
from trustforge.agent.orchestrator import run_agent_pipeline
from trustforge.agent.shadow_runtime import observe_candidate
from trustforge.agent.shadow_identity import (
    ATTESTATION_VERSION,
    _read_secure,
    measured_release_identity,
)
import trustforge.agent.shadow_identity as shadow_identity
from trustforge.agent.shadow_contracts import (
    ShadowBlocker,
    ShadowContractError,
    load_policy,
)
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType
from trustforge.trust.scoring import Claim, aggregate, score
from trustforge.release_manifest import (
    ReleaseManifest,
    _sha256_of_file,
)
from trustforge_core import KERNEL_CONTRACT_VERSION, run_kernel


def _forbidden(*args, **kwargs):
    raise AssertionError("candidate boundary must remain untouched")


def _fail_boundary(*args, **kwargs):
    raise ShadowEvidenceStoreError("injected boundary failure")


def _slow_kernel(kernel_input):
    time.sleep(0.9)
    return run_kernel(kernel_input)


def _hung_kernel(kernel_input):
    while True:
        time.sleep(0.2)


def _blocked_kernel(kernel_input, entered, release):
    entered.set()
    if not release.wait(timeout=1):
        raise TimeoutError("test release was not signalled")
    return run_kernel(kernel_input)


class _BrokenStore:
    def __init__(self, *, failure, **kwargs):
        self.failure = failure

    @staticmethod
    def observation_event_id(observation):
        return "sha256:" + "f" * 64

    def record_policy_and_observation(
        self, policy, event_id, observation, *, commit_guard,
    ):
        raise ShadowEvidenceStoreError(f"store is {self.failure}")

    def close(self):
        return None


class _SlowCommitStore(ShadowEvidenceStore):
    def __init__(self, *, marker_path, delay, **kwargs):
        self._marker_path = marker_path
        self._delay = delay
        super().__init__(**kwargs)

    def record_policy_and_observation(
        self, policy, event_id, observation, *, commit_guard,
    ):
        def delayed_guard():
            Path(self._marker_path).write_text("entered")
            time.sleep(self._delay)
            return commit_guard()

        return super().record_policy_and_observation(
            policy, event_id, observation, commit_guard=delayed_guard,
        )


class _DelayedDurabilityStore(ShadowEvidenceStore):
    def __init__(self, *, delay, **kwargs):
        self._delay = delay
        super().__init__(**kwargs)

    def record_policy_and_observation(
        self, policy, event_id, observation, *, commit_guard,
    ):
        time.sleep(self._delay)
        return super().record_policy_and_observation(
            policy, event_id, observation, commit_guard=commit_guard,
        )


def _claims() -> list[Claim]:
    return [
        Claim(
            "claim-a", "BTC demand increased",
            Document("doc-a", "news", "coindesk", "BTC demand increased", 100.0),
            "fact", "bullish",
        ),
        Claim(
            "claim-b", "BTC exchange supply declined",
            Document("doc-b", "onchain", "glassnode", "BTC supply declined", 100.0),
            "fact", "bullish",
        ),
    ]


def _configure(monkeypatch, path) -> None:
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent.chmod(0o700)
    artifact_path = parent / "active.zip"
    artifact_path.write_bytes(b"measured active artifact")
    artifact_path.chmod(0o600)
    manifest = ReleaseManifest(
        artifact_digest=_sha256_of_file(artifact_path),
        git_sha="c" * 40,
        app_version="1.0.0",
        kernel_contract_version="1.0.0",
        kernel_resolution_version="1.0.0",
        core_content_hash="d" * 64,
        config_snapshot_identity="sha256:" + "e" * 64,
        build_timestamp="2026-07-28T00:00:00Z",
        build_host="test",
    )
    manifest_path = parent / "manifest.json"
    manifest_path.write_text(manifest.to_json())
    manifest_path.chmod(0o600)
    attestation_path = parent / "shadow-runtime-attestation.json"
    reviewed_manifest = (
        Path(__file__).parents[1]
        / "data/contracts/reviewed-shadow-candidate.v1.json"
    )
    candidate_digest = "sha256:" + hashlib.sha256(
        reviewed_manifest.read_bytes()
    ).hexdigest()
    attestation_path.write_text(json.dumps({
        "version": ATTESTATION_VERSION,
        "dedicated_runtime": True,
        "active_manifest_path": str(manifest_path),
        "active_artifact_path": str(artifact_path),
    }))
    attestation_path.chmod(0o600)
    values = {
        "TRUSTFORGE_SHADOW_RUNTIME_ENABLED": "1",
        "KERNEL_SHADOW_OBSERVE": "1",
        "TRUSTFORGE_SHADOW_DEDICATED_RUNTIME": "1",
        "TRUSTFORGE_SHADOW_RUNTIME_ATTESTATION_PATH": str(attestation_path),
        "TRUSTFORGE_SHADOW_DB_PATH": str(path),
        "TRUSTFORGE_SHADOW_ACTIVE_RELEASE": "release:trustforge@1.0.0+" + "c" * 12,
        "TRUSTFORGE_SHADOW_CANDIDATE_RELEASE": (
            f"release:kernel@{KERNEL_CONTRACT_VERSION}+"
            f"{candidate_digest.removeprefix('sha256:')[:12]}"
        ),
        "TRUSTFORGE_SHADOW_ACTIVE_ARTIFACT_DIGEST": manifest.artifact_digest,
        "TRUSTFORGE_SHADOW_CANDIDATE_ARTIFACT_DIGEST": candidate_digest,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _observe(**kwargs):
    claims = _claims()
    scored = score(claims, now=100.0, dynamic_reputation=False, offline=True)
    brief = aggregate(scored, query="BTC", coin="BTC")
    request_id = kwargs.pop("request_id", "hermes-test-request")
    return observe_candidate(
        claims=claims,
        scored=scored,
        legacy_confidence=brief.calibrated_confidence,
        legacy_trust_raw=brief.confidence,
        coin="BTC",
        question_type="multi_source",
        query="BTC",
        request_id=request_id,
        pit_epoch=100.0,
        observed_epoch=100.0,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("runtime_flag", "observe_flag"),
    [(None, None), ("1", None), (None, "1"), ("0", "1")],
)
def test_default_off_and_every_incomplete_flag_combination(
    monkeypatch, runtime_flag, observe_flag,
):
    for key, value in (
        ("TRUSTFORGE_SHADOW_RUNTIME_ENABLED", runtime_flag),
        ("KERNEL_SHADOW_OBSERVE", observe_flag),
    ):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    assert _observe(mapper_fn=_forbidden).status == "not_observed"


def test_success_is_durable_zero_cost_and_survives_store_restart(monkeypatch, tmp_path):
    path = tmp_path / "private" / "shadow.sqlite3"
    _configure(monkeypatch, path)
    first = _observe(request_id="hermes-test-request-1")
    second = _observe(request_id="hermes-test-request-2")

    assert first.status == second.status == "success"
    assert first.provider_calls == second.provider_calls == 0
    assert first.cost_usd == second.cost_usd == 0.0
    assert first.kernel_output is not None
    assert first.observation_event_id != second.observation_event_id
    restarted = ShadowEvidenceStore(path)
    restarted.record_policy(load_policy())
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 2
    connection.close()


def test_success_latency_is_recorded_and_hard_bounded(monkeypatch, tmp_path):
    path = tmp_path / "private" / "shadow.sqlite3"
    _configure(monkeypatch, path)
    elapsed = [
        _observe(request_id=f"hermes-latency-{index}").elapsed_ms
        for index in range(20)
    ]
    p95 = statistics.quantiles(elapsed, n=100, method="inclusive")[94]
    assert p95 <= 500.0
    assert max(elapsed) <= 2_000.0


def test_external_executable_candidate_artifact_is_rejected(monkeypatch, tmp_path):
    path = tmp_path / "private" / "shadow.sqlite3"
    _configure(monkeypatch, path)
    marker = tmp_path / "malicious-ran"
    candidate_path = path.parent / "candidate.py"
    candidate_path.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
    )
    candidate_path.chmod(0o600)
    attestation_path = path.parent / "shadow-runtime-attestation.json"
    payload = json.loads(attestation_path.read_text())
    payload["candidate_artifact_path"] = str(candidate_path)
    attestation_path.write_text(json.dumps(payload))
    attestation_path.chmod(0o600)

    assert _observe().status == "not_observed"
    assert not marker.exists()


def test_reviewed_candidate_static_import_boundary_denies_network_and_providers():
    root = Path(__file__).parents[1]
    manifest = json.loads(
        (root / "data/contracts/reviewed-shadow-candidate.v1.json").read_text()
    )
    forbidden = {
        "boto3", "botocore", "requests", "urllib", "http", "socket",
        "trustforge.bedrock", "trustforge.ledger",
    }
    imported: set[str] = set()
    for relative in manifest["files"]:
        tree = ast.parse((root / relative).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not {
        name for name in imported
        if any(name == item or name.startswith(item + ".") for item in forbidden)
    }


def test_slow_durable_write_is_persisted_and_blocks_latency_policy(
    monkeypatch, tmp_path,
):
    path = tmp_path / "private" / "shadow.sqlite3"
    _configure(monkeypatch, path)
    result = _observe(
        store_factory=partial(_DelayedDurabilityStore, delay=0.3),
    )
    assert result.status == "success"
    assert 250.0 < result.elapsed_ms <= 1_000.0

    store = ShadowEvidenceStore(path)
    policy = load_policy()
    identity = measured_release_identity(policy).identity
    now = datetime.fromtimestamp(100.0, tz=timezone.utc).isoformat()
    evaluation = store.evaluate(identity, policy, now=now)
    assert ShadowBlocker.LATENCY_P95 in evaluation.decision.aggregate.blockers


@pytest.mark.parametrize("failure_at", ["mapper", "kernel"])
def test_mapper_and_kernel_failures_are_isolated(
    monkeypatch, tmp_path, failure_at,
):
    _configure(monkeypatch, tmp_path / failure_at / "shadow.sqlite3")

    kwargs = {}
    if failure_at == "mapper":
        kwargs["mapper_fn"] = _fail_boundary
    elif failure_at == "kernel":
        kwargs["kernel_fn"] = _fail_boundary
    result = _observe(**kwargs)
    assert result.status == "error"
    assert result.kernel_output is None
    assert result.provider_calls == 0
    assert result.cost_usd == 0.0


@pytest.mark.parametrize("store_failure", ["locked", "corrupt", "full"])
def test_locked_corrupt_and_full_store_failures_are_isolated(
    monkeypatch, tmp_path, store_failure,
):
    _configure(monkeypatch, tmp_path / store_failure / "shadow.sqlite3")

    result = _observe(store_factory=partial(_BrokenStore, failure=store_failure))
    assert result.status == "error"
    assert result.kernel_output is None
    assert result.provider_calls == 0
    assert result.cost_usd == 0.0


@pytest.mark.parametrize("kind", ["relative", "unsafe_parent", "corrupt"])
def test_invalid_posix_store_configuration_never_enters_candidate(
    monkeypatch, tmp_path, kind,
):
    path = tmp_path / "private" / "shadow.sqlite3"
    _configure(monkeypatch, path)
    if kind == "relative":
        monkeypatch.setenv("TRUSTFORGE_SHADOW_DB_PATH", "relative.sqlite3")
    elif kind == "unsafe_parent":
        path.parent.chmod(0o755)
    else:
        path.write_bytes(b"not sqlite")

    assert _observe(kernel_fn=_forbidden).status == "not_observed"


@pytest.mark.parametrize(
    "missing_gate",
    [
        "TRUSTFORGE_SHADOW_DEDICATED_RUNTIME",
        "TRUSTFORGE_SHADOW_RUNTIME_ATTESTATION_PATH",
    ],
)
def test_dedicated_runtime_attestation_is_mandatory(
    monkeypatch, tmp_path, missing_gate,
):
    _configure(monkeypatch, tmp_path / "private" / "shadow.sqlite3")
    monkeypatch.delenv(missing_gate)

    assert _observe(kernel_fn=_forbidden).status == "not_observed"


@pytest.mark.parametrize(
    "mismatch",
    [
        "TRUSTFORGE_SHADOW_ACTIVE_RELEASE",
        "TRUSTFORGE_SHADOW_CANDIDATE_RELEASE",
        "TRUSTFORGE_SHADOW_ACTIVE_ARTIFACT_DIGEST",
        "TRUSTFORGE_SHADOW_CANDIDATE_ARTIFACT_DIGEST",
    ],
)
def test_environment_identity_must_match_measured_artifacts(
    monkeypatch, tmp_path, mismatch,
):
    _configure(monkeypatch, tmp_path / "private" / "shadow.sqlite3")
    monkeypatch.setenv(mismatch, "mismatch")
    assert _observe().status == "not_observed"


def test_active_artifact_tamper_fails_identity_closed(monkeypatch, tmp_path):
    path = tmp_path / "private" / "shadow.sqlite3"
    _configure(monkeypatch, path)
    (path.parent / "active.zip").write_bytes(b"tampered after attestation")
    assert _observe().status == "not_observed"


def test_fd_snapshot_ignores_path_swap_after_open(monkeypatch, tmp_path):
    original = tmp_path / "identity.json"
    replacement = tmp_path / "replacement.json"
    original.write_bytes(b"trusted")
    replacement.write_bytes(b"attacker")
    original.chmod(0o600)
    replacement.chmod(0o600)
    real_read_descriptor = shadow_identity._read_descriptor

    def swap_then_read(descriptor, *, max_bytes):
        original.rename(tmp_path / "old.json")
        replacement.rename(original)
        return real_read_descriptor(descriptor, max_bytes=max_bytes)

    monkeypatch.setattr(shadow_identity, "_read_descriptor", swap_then_read)
    assert _read_secure(original, owner_only=True, max_bytes=100) == b"trusted"


def test_fd_snapshot_rejects_in_place_tamper(monkeypatch, tmp_path):
    target = tmp_path / "identity.json"
    target.write_bytes(b"trusted")
    target.chmod(0o600)
    real_read = shadow_identity.os.read
    changed = False

    def tampering_read(descriptor, size):
        nonlocal changed
        chunk = real_read(descriptor, size)
        if chunk and not changed:
            changed = True
            target.write_bytes(b"tampered")
        return chunk

    monkeypatch.setattr(shadow_identity.os, "read", tampering_read)
    with pytest.raises(ShadowContractError, match="changed during snapshot"):
        _read_secure(target, owner_only=True, max_bytes=100)


def test_timeout_is_hard_bounded_and_late_worker_cannot_record(monkeypatch, tmp_path):
    import trustforge.agent.shadow_runtime as runtime

    path = tmp_path / "private" / "shadow.sqlite3"
    _configure(monkeypatch, path)
    monkeypatch.setattr(runtime, "_HARD_TIMEOUT_MS", 700.0)

    started = time.monotonic()
    result = _observe(kernel_fn=_slow_kernel)
    elapsed = time.monotonic() - started
    assert result.status == "timeout"
    assert elapsed < 1.0
    time.sleep(0.1)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
    connection.close()


def test_transaction_entered_before_timeout_cannot_commit_late(
    monkeypatch, tmp_path,
):
    import trustforge.agent.shadow_runtime as runtime

    path = tmp_path / "private" / "shadow.sqlite3"
    marker = tmp_path / "commit-guard-entered"
    _configure(monkeypatch, path)
    monkeypatch.setattr(runtime, "_HARD_TIMEOUT_MS", 700.0)

    result = _observe(store_factory=partial(
        _SlowCommitStore, marker_path=str(marker), delay=2.0,
    ))
    assert result.status == "timeout"
    assert marker.read_text() == "entered"
    time.sleep(0.05)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM policies").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
    connection.close()


def test_hung_child_is_reaped_and_next_observation_succeeds(monkeypatch, tmp_path):
    import trustforge.agent.shadow_runtime as runtime

    path = tmp_path / "private" / "shadow.sqlite3"
    _configure(monkeypatch, path)
    monkeypatch.setattr(runtime, "_HARD_TIMEOUT_MS", 700.0)

    assert _observe(kernel_fn=_hung_kernel).status == "timeout"
    assert not [
        child for child in multiprocessing.active_children()
        if child.name == "trustforge-shadow-observation"
    ]
    monkeypatch.setattr(runtime, "_HARD_TIMEOUT_MS", 1_000.0)
    assert _observe(request_id="hermes-after-hang").status == "success"
    assert not [
        child for child in multiprocessing.active_children()
        if child.name == "trustforge-shadow-observation"
    ]


def test_process_start_failure_isolated_and_lease_is_recoverable(monkeypatch, tmp_path):
    path = tmp_path / "private" / "shadow.sqlite3"
    _configure(monkeypatch, path)
    def unpickleable(kernel_input):
        return run_kernel(kernel_input)

    assert _observe(kernel_fn=unpickleable).status == "error"
    assert _observe(request_id="hermes-after-start-error").status == "success"


def test_cleanup_failures_cannot_strand_single_flight_lease(monkeypatch, tmp_path):
    import trustforge.agent.shadow_runtime as runtime

    path = tmp_path / "private" / "shadow.sqlite3"
    _configure(monkeypatch, path)
    real_get_context = runtime.multiprocessing.get_context

    class BrokenConnection:
        def poll(self, timeout):
            return False

        def close(self):
            raise RuntimeError("close failed")

    class BrokenProcess:
        def start(self):
            return None

        def is_alive(self):
            return True

        def terminate(self):
            return None

        def kill(self):
            return None

        def join(self, timeout=None):
            return None

        def close(self):
            raise ValueError("cannot close a process while it is still running")

    class BrokenContext:
        process_count = 0

        def Pipe(self, duplex):
            return BrokenConnection(), BrokenConnection()

        def Process(self, **kwargs):
            self.process_count += 1
            return BrokenProcess()

    broken_context = BrokenContext()
    monkeypatch.setattr(
        runtime.multiprocessing, "get_context", lambda method: broken_context,
    )
    assert _observe().status == "error"
    poisoned = _observe(request_id="hermes-after-cleanup-error")
    assert poisoned.status == "not_observed"
    assert poisoned.diagnostic == "runtime_poisoned_unreaped_child"
    assert broken_context.process_count == 1

    # Test-only cleanup: production poison is intentionally process-lifetime.
    runtime._SHADOW_RUNTIME_POISONED = False
    runtime._SINGLE_FLIGHT.release()
    monkeypatch.setattr(runtime.multiprocessing, "get_context", real_get_context)


def test_dead_process_close_error_is_isolated():
    import trustforge.agent.shadow_runtime as runtime

    class DeadProcess:
        def is_alive(self):
            return False

        def close(self):
            raise ValueError("close failed")

    assert runtime._cleanup_process(DeadProcess()) is True


def test_single_flight_bounds_concurrency(monkeypatch, tmp_path):
    path = tmp_path / "private" / "shadow.sqlite3"
    _configure(monkeypatch, path)
    context = multiprocessing.get_context("forkserver")
    entered = context.Event()
    release = context.Event()
    holder: list = []

    thread = threading.Thread(
        target=lambda: holder.append(_observe(kernel_fn=partial(
            _blocked_kernel, entered=entered, release=release,
        ))),
    )
    thread.start()
    assert entered.wait(timeout=1)
    concurrent = _observe()
    release.set()
    thread.join(timeout=1)
    assert concurrent.status == "not_observed"
    assert holder[0].status == "success"


def test_candidate_success_cannot_change_active_report_or_evidence(monkeypatch, tmp_path):
    docs = [claim.doc for claim in _claims()]
    def now_fn():
        return 100.0

    monkeypatch.delenv("TRUSTFORGE_SHADOW_RUNTIME_ENABLED", raising=False)
    baseline_report, baseline_evidence = run_agent_pipeline(
        "BTC", "BTC", QuestionType.MULTI_SOURCE, docs,
        client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=now_fn, run_id="hermes-baseline"),
        now_fn=now_fn,
    )

    _configure(monkeypatch, tmp_path / "private" / "shadow.sqlite3")
    shadow_log = ExecutionLog(now_fn=now_fn, run_id="hermes-shadow")
    shadow_report, shadow_evidence = run_agent_pipeline(
        "BTC", "BTC", QuestionType.MULTI_SOURCE, docs,
        client=BedrockClient(offline=True), log=shadow_log, now_fn=now_fn,
    )
    derive = next(
        event for event in shadow_log.events if event["tool"] == "judgment.derive"
    )
    assert derive["params"]["shadow_observation_status"] == "success"
    assert derive["params"]["shadow_provider_calls"] == 0
    assert derive["params"]["shadow_cost_usd"] == 0.0
    assert asdict(shadow_report) == asdict(baseline_report)
    assert [asdict(item) for item in shadow_evidence] == [
        asdict(item) for item in baseline_evidence
    ]
