from __future__ import annotations

import hashlib
import json
import os
import runpy
import sqlite3
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.agent.shadow_contracts import (
    CONTRACT_VERSION,
    ShadowReleaseIdentity,
    canonical_json,
    load_policy,
    policy_digest as shadow_policy_digest,
    to_dict,
)
import trustforge.agent.shadow_evidence_store as evidence_store_module
import trustforge.asset_intrinsic_promotion_receipt as receipt_module
from trustforge.asset_intrinsic_promotion import load_intrinsic_promotion_policy
from trustforge.asset_intrinsic_promotion_dataset import (
    DATASET_SCHEMA_VERSION,
    build_promotion_evidence_dataset,
)
from trustforge.asset_intrinsic_promotion_receipt import (
    EVENT_KIND,
    FAILURE_EVENT_KIND,
    SIGNER_DOMAIN,
    PromotionReceiptError,
    ReleaseBinding,
    produce_signed_receipt,
    validate_receipt_event,
)
from trustforge.release_manifest import ReleaseManifest
from trustforge.signed_event_ledger import SignedEventLedger

PIT = "2026-07-29T00:00:00Z"
BENCHMARK = "sha256:" + "b" * 64
_DATASET_DOMAIN = b"trustforge.intrinsic-promotion-dataset.v1\x00"


def _ledger(tmp_path, *, bootstrap: bool = True) -> SignedEventLedger:
    seed = b"k" * 32
    public = (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    return SignedEventLedger(
        directory=tmp_path / "ledger" / "receipts",
        verification_keys={"receipt-v1": public},
        event_permissions={
            SIGNER_DOMAIN: frozenset({EVENT_KIND, FAILURE_EVENT_KIND})
        },
        domain_keys={SIGNER_DOMAIN: frozenset({"receipt-v1"})},
        signing_key_id="receipt-v1",
        signing_private_key=seed,
        signing_domain=SIGNER_DOMAIN,
        ledger_role="intrinsic-promotion-receipts",
        bootstrap=bootstrap,
        coordination_root=tmp_path / "ledger",
    )


def _release() -> ReleaseBinding:
    return ReleaseBinding(
        git_sha="a" * 40,
        active_artifact_digest="sha256:" + "d" * 64,
        shadow_candidate_artifact_digest="sha256:" + "c" * 64,
        artifact_digest="sha256:" + "c" * 64,
        release_id="release:test@1",
    )


def _dataset() -> dict:
    body = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "pit_cutoff": PIT,
        "provenance": {
            "release_identity": {
                "active_artifact_digest": "sha256:" + "d" * 64,
                "candidate_artifact_digest": "sha256:" + "c" * 64,
            }
        },
        "observations": [],
    }
    return {
        **body,
        "dataset_digest": "sha256:"
        + hashlib.sha256(_DATASET_DOMAIN + canonical_json(body)).hexdigest(),
    }


def test_success_receipt_is_signed_idempotent_and_never_promotes(tmp_path):
    ledger = _ledger(tmp_path)
    ticks = iter(
        [
            datetime(2026, 8, 1, 0, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 1, 0, 2, tzinfo=timezone.utc),
        ]
    )
    kwargs = {
        "ledger": ledger,
        "release": _release(),
        "pit_cutoff": PIT,
        "policy": load_intrinsic_promotion_policy(),
        "benchmark_manifest_digest": BENCHMARK,
        "dataset_loader": _dataset,
        "now": lambda: next(ticks),
    }
    first = produce_signed_receipt(**kwargs)
    second = produce_signed_receipt(**kwargs)
    assert first == second
    assert first["kind"] == EVENT_KIND
    assert first["decision"] == "block"
    assert first["recommendation_only"] is True
    assert first["auto_promote"] is False
    assert len(ledger.read()) == 1


def test_missing_dataset_has_null_fields_and_stable_failure_sentinel(tmp_path):
    ledger = _ledger(tmp_path)

    def unavailable():
        raise OSError("secret path must not escape")

    event = produce_signed_receipt(
        ledger=ledger,
        release=_release(),
        pit_cutoff=PIT,
        policy=load_intrinsic_promotion_policy(),
        benchmark_manifest_digest=BENCHMARK,
        dataset_loader=unavailable,
        now=lambda: datetime(2026, 8, 1, 0, 1, tzinfo=timezone.utc),
    )
    assert event["kind"] == FAILURE_EVENT_KIND
    assert event["dataset_schema_version"] is None
    assert event["dataset_digest"] is None
    assert event["evaluator_receipt"] is None
    assert event["decision"] == "block"
    assert "secret" not in str(event)


def test_coordination_lock_covers_dataset_evaluation_and_post_read(tmp_path):
    ledger = _ledger(tmp_path)

    def dataset():
        held = ledger._coordination_state.held
        assert held[str(ledger.coordination_lock_path)]["depth"] == 1
        return _dataset()

    produce_signed_receipt(
        ledger=ledger,
        release=_release(),
        pit_cutoff=PIT,
        policy=load_intrinsic_promotion_policy(),
        benchmark_manifest_digest=BENCHMARK,
        dataset_loader=dataset,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_digest", "sha256:" + "f" * 64),
        ("git_sha", "f" * 40),
        ("auto_promote", True),
        ("decision", "pass"),
        ("evaluation_key", "sha256:" + "e" * 64),
    ],
)
def test_envelope_tampering_fails_closed(tmp_path, field, value):
    event = produce_signed_receipt(
        ledger=_ledger(tmp_path),
        release=_release(),
        pit_cutoff=PIT,
        policy=load_intrinsic_promotion_policy(),
        benchmark_manifest_digest=BENCHMARK,
        dataset_loader=_dataset,
    )
    tampered = {**event, field: value}
    with pytest.raises(PromotionReceiptError):
        validate_receipt_event(tampered)


def test_release_binding_rejects_unverified_identity():
    with pytest.raises(PromotionReceiptError):
        ReleaseBinding(
            git_sha="unknown",
            active_artifact_digest="sha256:" + "d" * 64,
            shadow_candidate_artifact_digest="sha256:" + "c" * 64,
            artifact_digest="sha256:" + "c" * 64,
            release_id="release:test",
        )


def test_validator_rejects_future_pit_and_non_candidate_success(tmp_path):
    success = produce_signed_receipt(
        ledger=_ledger(tmp_path / "success"),
        release=_release(),
        pit_cutoff=PIT,
        policy=load_intrinsic_promotion_policy(),
        benchmark_manifest_digest=BENCHMARK,
        dataset_loader=_dataset,
    )
    with pytest.raises(PromotionReceiptError, match="pit_cutoff follows"):
        validate_receipt_event(
            {**success, "generated_at": "2026-07-28T23:59:59Z"}
        )
    with pytest.raises(PromotionReceiptError, match="success receipt is malformed"):
        validate_receipt_event(
            {**success, "artifact_digest": "sha256:" + "e" * 64}
        )

    failure = produce_signed_receipt(
        ledger=_ledger(tmp_path / "failure"),
        release=_release(),
        pit_cutoff=PIT,
        policy=load_intrinsic_promotion_policy(),
        benchmark_manifest_digest=BENCHMARK,
        dataset_loader=lambda: (_ for _ in ()).throw(OSError()),
    )
    with pytest.raises(PromotionReceiptError, match="pit_cutoff follows"):
        validate_receipt_event(
            {**failure, "generated_at": "2026-07-28T23:59:59Z"}
        )


def test_evaluator_exception_is_a_dataset_bound_signed_block(tmp_path, monkeypatch):
    monkeypatch.setattr(
        receipt_module,
        "evaluate_promotion",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    event = produce_signed_receipt(
        ledger=_ledger(tmp_path),
        release=_release(),
        pit_cutoff=PIT,
        policy=load_intrinsic_promotion_policy(),
        benchmark_manifest_digest=BENCHMARK,
        dataset_loader=_dataset,
    )
    assert event["kind"] == FAILURE_EVENT_KIND
    assert event["failure_stage"] == "evaluator"
    assert event["dataset_digest"] == _dataset()["dataset_digest"]
    assert event["decision"] == "block"
    assert "secret" not in str(event)


def test_worker_and_scheduler_have_no_promotion_or_git_identity_side_channel():
    worker = Path("scripts/run_intrinsic_promotion_receipt.py").read_text()
    renderer = Path("deploy/render_intrinsic_promotion_scheduler.sh").read_text()
    assert "subprocess" not in worker
    assert "git rev-parse" not in worker
    assert "auto_promote" not in worker
    assert "systemctl" not in renderer


def test_naive_generation_clock_fails_closed_without_append(tmp_path):
    ledger = _ledger(tmp_path)
    with pytest.raises(PromotionReceiptError, match="generated_at lacks timezone"):
        produce_signed_receipt(
            ledger=ledger,
            release=_release(),
            pit_cutoff=PIT,
            policy=load_intrinsic_promotion_policy(),
            benchmark_manifest_digest=BENCHMARK,
            dataset_loader=_dataset,
            now=lambda: datetime(2026, 8, 1, 0, 1),
        )
    assert ledger.read() == []


def test_retry_reconciles_crash_after_durable_append(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    original_read = ledger.read
    crashed = False

    def crash_after_append():
        nonlocal crashed
        records = original_read()
        if records and not crashed:
            crashed = True
            raise OSError("simulated crash after append")
        return records

    monkeypatch.setattr(ledger, "read", crash_after_append)
    kwargs = {
        "ledger": ledger,
        "release": _release(),
        "pit_cutoff": PIT,
        "policy": load_intrinsic_promotion_policy(),
        "benchmark_manifest_digest": BENCHMARK,
        "dataset_loader": _dataset,
    }
    with pytest.raises(OSError, match="simulated crash"):
        produce_signed_receipt(**kwargs)
    monkeypatch.setattr(ledger, "read", original_read)
    winner = produce_signed_receipt(**kwargs)
    assert winner == original_read()[0]["event"]
    assert len(original_read()) == 1


def test_two_workers_converge_on_one_signed_winner(tmp_path):
    first_ledger = _ledger(tmp_path)
    second_ledger = _ledger(tmp_path, bootstrap=False)

    def run(ledger):
        return produce_signed_receipt(
            ledger=ledger,
            release=_release(),
            pit_cutoff=PIT,
            policy=load_intrinsic_promotion_policy(),
            benchmark_manifest_digest=BENCHMARK,
            dataset_loader=_dataset,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(pool.map(run, (first_ledger, second_ledger)))
    assert first == second
    assert len(first_ledger.read()) == 1


def test_canonical_store_to_dataset_to_evaluator_to_signed_ledger(
    tmp_path, monkeypatch
):
    helpers = runpy.run_path("tests/test_asset_intrinsic_promotion_dataset.py")
    observed_at = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    identity = helpers["_identity"]()
    real_connect = sqlite3.connect

    def fixed_clock_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.create_function(
            "strftime",
            2,
            lambda fmt, value: (
                "2026-07-20T13:00:00.000Z"
                if fmt == "%Y-%m-%dT%H:%M:%fZ" and value == "now"
                else None
            ),
        )
        return connection

    monkeypatch.setattr(evidence_store_module.sqlite3, "connect", fixed_clock_connect)
    store_path = tmp_path / "shadow" / "shadow.sqlite3"
    store = helpers["_store"](
        store_path,
        [helpers["_observation"]("BTC", observed_at, request="receipt-e2e")],
    )

    def canonical_loader():
        return build_promotion_evidence_dataset(
            store,
            identity,
            load_policy(),
            pit_cutoff="2026-07-21T00:00:00Z",
            stale_after_days=30,
        )

    event = produce_signed_receipt(
        ledger=_ledger(tmp_path / "receipt"),
        release=ReleaseBinding(
            git_sha="a" * 40,
            active_artifact_digest=identity.active_artifact_digest,
            shadow_candidate_artifact_digest=identity.candidate_artifact_digest,
            artifact_digest=identity.candidate_artifact_digest,
            release_id="release:test@1",
        ),
        pit_cutoff="2026-07-21T00:00:00Z",
        policy=load_intrinsic_promotion_policy(),
        benchmark_manifest_digest=BENCHMARK,
        dataset_loader=canonical_loader,
        now=lambda: datetime(2026, 7, 21, 1, tzinfo=timezone.utc),
    )
    assert event["kind"] == EVENT_KIND, (
        event["failure_stage"],
        event["failure_reason"],
        event["reason_codes"],
    )
    assert event["dataset_schema_version"] == DATASET_SCHEMA_VERSION
    assert event["evaluator_receipt"] is not None
    assert event["decision"] == "block"
    store.close()


def test_offline_provisioner_is_dry_by_default_and_bootstraps_sandbox(tmp_path):
    seed = b"k" * 32
    public = (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    keyring = tmp_path / "keyring.json"
    keyring.write_text(
        json.dumps(
            {
                "key_id": "receipt-v1",
                "private_key": seed.hex(),
                "verification_keys": {"receipt-v1": public.hex()},
            }
        )
    )
    keyring.chmod(0o600)
    root = tmp_path / "security-ledger"
    command = [
        sys.executable,
        str(Path("scripts/provision_intrinsic_promotion_ledger.py").resolve()),
        "--ledger-root",
        str(root),
        "--keyring",
        str(keyring),
    ]
    subprocess_env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    dry = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=subprocess_env,
    )
    assert "bootstrap-intrinsic-promotion-ledger" in dry.stdout
    assert not root.exists()
    subprocess.run(
        [*command, "--apply", "--test-owner-current-user"],
        check=True,
        capture_output=True,
        text=True,
        env=subprocess_env,
    )
    assert stat.S_IMODE(root.stat().st_mode) == 0o750
    assert stat.S_IMODE((root / "coordination.lock").stat().st_mode) == 0o660
    assert (
        stat.S_IMODE(
            (root / "intrinsic-promotion-receipts" / "bootstrap.json").stat().st_mode
        )
        == 0o640
    )


def test_key_mismatch_and_untrusted_ledger_fail_without_append_or_secret(tmp_path):
    seed = b"k" * 32
    wrong_seed = b"x" * 32
    wrong_public = (
        Ed25519PrivateKey.from_private_bytes(wrong_seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    keyring = tmp_path / "mismatch.json"
    keyring.write_text(
        json.dumps(
            {
                "key_id": "receipt-v1",
                "private_key": seed.hex(),
                "verification_keys": {"receipt-v1": wrong_public.hex()},
            }
        )
    )
    keyring.chmod(0o600)
    root = tmp_path / "must-not-exist"
    result = subprocess.run(
        [
            sys.executable,
            str(Path("scripts/provision_intrinsic_promotion_ledger.py").resolve()),
            "--ledger-root",
            str(root),
            "--keyring",
            str(keyring),
            "--apply",
            "--test-owner-current-user",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
    )
    assert result.returncode != 0
    assert not root.exists()
    assert seed.hex() not in result.stdout + result.stderr

    malformed = tmp_path / "malformed.json"
    malformed.write_text(
        json.dumps(
            {
                "key_id": "receipt-v1",
                "private_key": seed.hex(),
                "verification_keys": {
                    "receipt-v1": public.hex()
                    if (
                        public := Ed25519PrivateKey.from_private_bytes(seed)
                        .public_key()
                        .public_bytes(Encoding.Raw, PublicFormat.Raw)
                    )
                    else "",
                    "hostile-TOPSECRET": "00",
                },
            }
        )
    )
    malformed.chmod(0o600)
    malformed_root = tmp_path / "malformed-root"
    malformed_result = subprocess.run(
        [
            sys.executable,
            str(Path("scripts/provision_intrinsic_promotion_ledger.py").resolve()),
            "--ledger-root",
            str(malformed_root),
            "--keyring",
            str(malformed),
            "--apply",
            "--test-owner-current-user",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
    )
    assert malformed_result.returncode != 0
    assert not malformed_root.exists()
    assert "TOPSECRET" not in malformed_result.stdout + malformed_result.stderr

    worker = str(Path("scripts/run_intrinsic_promotion_receipt.py").resolve())
    runtime_root = tmp_path / "runtime-root"
    runtime_result = subprocess.run(
        [
            sys.executable,
            worker,
            "run",
            "--shadow-db",
            str(tmp_path / "missing.sqlite3"),
            "--shadow-release-identity",
            str(tmp_path / "missing.json"),
            "--stale-after-days",
            "30",
            "--release-manifest",
            str(tmp_path / "missing-release.json"),
            "--release-artifact",
            str(tmp_path / "missing.zip"),
            "--benchmark-manifest",
            str(tmp_path / "missing-benchmark.json"),
            "--benchmark-corpus",
            str(tmp_path / "missing-corpus.json"),
            "--repo-root",
            str(tmp_path),
            "--receipt-keyring",
            str(keyring),
            "--ledger-root",
            str(runtime_root),
            "--pit-cutoff",
            PIT,
            "--test-owner-current-user",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
    )
    assert runtime_result.returncode != 0
    assert not runtime_root.exists()
    assert seed.hex() not in runtime_result.stdout + runtime_result.stderr


def test_provision_keyring_mutation_and_bootstrap_failure_leave_no_root(
    tmp_path, monkeypatch
):
    script = runpy.run_path("scripts/provision_intrinsic_promotion_ledger.py")
    seed = b"m" * 32
    public = (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    keyring = tmp_path / "keyring.json"
    keyring.write_text(
        json.dumps(
            {
                "key_id": "receipt-v1",
                "private_key": seed.hex(),
                "verification_keys": {"receipt-v1": public.hex()},
            }
        )
    )
    keyring.chmod(0o600)
    root = tmp_path / "mutation-root"
    original_read = os.read
    mutated = False

    def mutate_after_read(descriptor, size):
        nonlocal mutated
        raw = original_read(descriptor, size)
        if not mutated:
            mutated = True
            os.utime(keyring, ns=(keyring.stat().st_atime_ns, keyring.stat().st_mtime_ns + 1))
        return raw

    monkeypatch.setattr(os, "read", mutate_after_read)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provision",
            "--ledger-root",
            str(root),
            "--keyring",
            str(keyring),
            "--apply",
            "--test-owner-current-user",
        ],
    )
    with pytest.raises(SystemExit, match="changed during read"):
        script["main"]()
    assert not root.exists()

    monkeypatch.setattr(os, "read", original_read)
    monkeypatch.setitem(
        script["main"].__globals__,
        "SignedEventLedger",
        lambda **_: (_ for _ in ()).throw(RuntimeError("bootstrap failed")),
    )
    failed_root = tmp_path / "failed-root"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provision",
            "--ledger-root",
            str(failed_root),
            "--keyring",
            str(keyring),
            "--apply",
            "--test-owner-current-user",
        ],
    )
    with pytest.raises(RuntimeError, match="bootstrap failed"):
        script["main"]()
    assert not failed_root.exists()
    assert not tuple(tmp_path.glob(".failed-root.bootstrap-*"))


def test_cli_concurrency_and_public_only_verify(tmp_path):
    seed = b"z" * 32
    public = (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    private_keyring = tmp_path / "private.json"
    private_keyring.write_text(
        json.dumps(
            {
                "key_id": "receipt-v1",
                "private_key": seed.hex(),
                "verification_keys": {"receipt-v1": public.hex()},
            }
        )
    )
    private_keyring.chmod(0o600)
    public_keyring = tmp_path / "public.json"
    public_keyring.write_text(
        json.dumps({"verification_keys": {"receipt-v1": public.hex()}})
    )
    public_keyring.chmod(0o644)
    root = tmp_path / "ledger"
    subprocess_env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    subprocess.run(
        [
            sys.executable,
            str(Path("scripts/provision_intrinsic_promotion_ledger.py").resolve()),
            "--ledger-root",
            str(root),
            "--keyring",
            str(private_keyring),
            "--apply",
            "--test-owner-current-user",
        ],
        check=True,
        env=subprocess_env,
    )
    worker = str(Path("scripts/run_intrinsic_promotion_receipt.py").resolve())
    run = [
        sys.executable,
        worker,
        "run",
        "--shadow-db",
        str(tmp_path / "missing.sqlite3"),
        "--shadow-release-identity",
        str(tmp_path / "missing-identity.json"),
        "--stale-after-days",
        "30",
        "--release-manifest",
        str(tmp_path / "missing-manifest.json"),
        "--release-artifact",
        str(tmp_path / "missing.zip"),
        "--benchmark-manifest",
        str(tmp_path / "missing-benchmark.json"),
        "--benchmark-corpus",
        str(tmp_path / "missing-corpus.json"),
        "--repo-root",
        str(tmp_path),
        "--receipt-keyring",
        str(private_keyring),
        "--ledger-root",
        str(root),
        "--pit-cutoff",
        PIT,
        "--test-owner-current-user",
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                lambda _: subprocess.run(
                    run,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=subprocess_env,
                ),
                range(2),
            )
        )
    assert results[0].stdout == results[1].stdout
    assert "TOPSECRET" not in results[0].stdout + results[0].stderr

    artifact = tmp_path / "candidate.zip"
    artifact.write_bytes(b"candidate")
    candidate_digest = "sha256:" + hashlib.sha256(b"candidate").hexdigest()
    identity = ShadowReleaseIdentity(
        active_release="release:active@1",
        candidate_release="release:candidate@1",
        active_artifact_digest="sha256:" + "a" * 64,
        candidate_artifact_digest=candidate_digest,
        policy_digest=shadow_policy_digest(load_policy()),
        contract_version=CONTRACT_VERSION,
    )
    identity_path = tmp_path / "identity.json"
    identity_path.write_bytes(canonical_json(to_dict(identity)) + b"\n")
    identity_path.chmod(0o600)
    invalid_release = [*run]
    invalid_release[
        invalid_release.index(str(tmp_path / "missing-identity.json"))
    ] = str(identity_path)
    release_results = [
        subprocess.run(
            invalid_release,
            check=True,
            capture_output=True,
            text=True,
            env=subprocess_env,
        )
        for _ in range(2)
    ]
    assert release_results[0].stdout == release_results[1].stdout
    assert json.loads(release_results[0].stdout)["reason_codes"] == [
        "release_identity_invalid"
    ]

    manifest_path = tmp_path / "release.json"
    manifest_path.write_text(
        ReleaseManifest(
            artifact_digest=candidate_digest,
            git_sha="a" * 40,
            app_version="1.0.0",
            kernel_contract_version="1",
            kernel_resolution_version="1",
            core_content_hash="sha256:" + "c" * 64,
            config_snapshot_identity="sha256:" + "d" * 64,
            build_timestamp="2026-07-29T00:00:00Z",
            build_host="builder",
        ).to_json()
    )
    manifest_path.chmod(0o644)
    invalid_policy_path = tmp_path / "invalid-policy.json"
    invalid_policy_path.write_text('{"hostile":"TOPSECRET"}')
    invalid_policy = [*invalid_release]
    invalid_policy[invalid_policy.index(str(tmp_path / "missing-manifest.json"))] = str(
        manifest_path
    )
    invalid_policy[invalid_policy.index(str(tmp_path / "missing.zip"))] = str(artifact)
    invalid_policy.extend(["--promotion-policy", str(invalid_policy_path)])
    policy_results = [
        subprocess.run(
            invalid_policy,
            check=True,
            capture_output=True,
            text=True,
            env=subprocess_env,
        )
        for _ in range(2)
    ]
    assert policy_results[0].stdout == policy_results[1].stdout
    assert json.loads(policy_results[0].stdout)["reason_codes"] == ["policy_invalid"]
    assert "TOPSECRET" not in "".join(
        item.stdout + item.stderr for item in policy_results
    )

    script = runpy.run_path(worker)
    records = script["_ledger"](
        SimpleNamespace(ledger_root=root, test_owner_current_user=True),
        {"receipt-v1": public},
        signer=None,
    ).read()
    receipt_events = [
        record
        for record in records
        if record["event"].get("kind") in {EVENT_KIND, FAILURE_EVENT_KIND}
    ]
    assert len(receipt_events) == 3
    verify = subprocess.run(
        [
            sys.executable,
            worker,
            "verify",
            "--verification-keyring",
            str(public_keyring),
            "--ledger-root",
            str(root),
            "--test-owner-current-user",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=subprocess_env,
    )
    assert json.loads(verify.stdout)["decision"] == "block"
    rejected = subprocess.run(
        [
            sys.executable,
            worker,
            "verify",
            "--verification-keyring",
            str(private_keyring),
            "--ledger-root",
            str(root),
            "--test-owner-current-user",
        ],
        capture_output=True,
        text=True,
        env=subprocess_env,
    )
    assert rejected.returncode != 0

    wrong_seed = b"w" * 32
    wrong_public = (
        Ed25519PrivateKey.from_private_bytes(wrong_seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    wrong_keyring = tmp_path / "wrong-public.json"
    wrong_keyring.write_text(
        json.dumps({"verification_keys": {"receipt-v1": wrong_public.hex()}})
    )
    wrong = subprocess.run(
        [
            sys.executable,
            worker,
            "verify",
            "--verification-keyring",
            str(wrong_keyring),
            "--ledger-root",
            str(root),
            "--test-owner-current-user",
        ],
        capture_output=True,
        text=True,
        env=subprocess_env,
    )
    assert wrong.returncode != 0


def test_protected_inputs_reject_mode_symlink_and_in_read_mutation(
    tmp_path, monkeypatch
):
    script = runpy.run_path("scripts/run_intrinsic_promotion_receipt.py")
    protected_json = script["_protected_json"]
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text("{}")
    unsafe.chmod(0o666)
    with pytest.raises(SystemExit, match="unsafe protected input"):
        protected_json(unsafe, private=True)
    target = tmp_path / "target.json"
    target.write_text("{}")
    target.chmod(0o600)
    symlink = tmp_path / "link.json"
    symlink.symlink_to(target)
    with pytest.raises(OSError):
        protected_json(symlink, private=True)
    mutable = tmp_path / "mutable.json"
    mutable.write_text('{"a":1}')
    mutable.chmod(0o600)
    original_read = os.read

    def mutate_after_read(descriptor, size):
        payload = original_read(descriptor, size)
        mutable.write_text('{"a":2}')
        return payload

    monkeypatch.setattr(os, "read", mutate_after_read)
    with pytest.raises(SystemExit, match="changed during read"):
        protected_json(mutable, private=True)


def test_shadow_release_tuple_mismatch_is_signed_block(tmp_path):
    dataset = _dataset()
    dataset["provenance"]["release_identity"][
        "candidate_artifact_digest"
    ] = "sha256:" + "f" * 64
    body = {key: value for key, value in dataset.items() if key != "dataset_digest"}
    dataset["dataset_digest"] = "sha256:" + hashlib.sha256(
        _DATASET_DOMAIN + canonical_json(body)
    ).hexdigest()
    event = produce_signed_receipt(
        ledger=_ledger(tmp_path),
        release=_release(),
        pit_cutoff=PIT,
        policy=load_intrinsic_promotion_policy(),
        benchmark_manifest_digest=BENCHMARK,
        dataset_loader=lambda: dataset,
        now=lambda: datetime(2026, 7, 29, 1, tzinfo=timezone.utc),
    )
    assert event["kind"] == FAILURE_EVENT_KIND
    assert event["failure_reason"] == "canonical_dataset_invalid"


def test_future_pit_never_appends(tmp_path):
    ledger = _ledger(tmp_path)
    with pytest.raises(PromotionReceiptError, match="pit_cutoff follows"):
        produce_signed_receipt(
            ledger=ledger,
            release=_release(),
            pit_cutoff="2026-07-30T00:00:00Z",
            policy=load_intrinsic_promotion_policy(),
            benchmark_manifest_digest=BENCHMARK,
            dataset_loader=lambda: {
                **_dataset(),
                "pit_cutoff": "2026-07-30T00:00:00Z",
            },
            now=lambda: datetime(2026, 7, 29, 1, tzinfo=timezone.utc),
        )
    assert ledger.read() == []
