#!/usr/bin/env python3
"""Run or verify signed intrinsic-promotion recommendation receipts."""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.agent.shadow_contracts import (
    ShadowReleaseIdentity,
    canonical_json,
    load_policy,
)
from trustforge.agent.shadow_evidence_store import ShadowEvidenceStore
from trustforge.asset_intrinsic_promotion import (
    POLICY_PATH,
    load_intrinsic_promotion_policy,
)
from trustforge.asset_intrinsic_promotion_dataset import (
    build_promotion_evidence_dataset,
)
from trustforge.asset_intrinsic_promotion_receipt import (
    EVENT_KIND,
    FAILURE_EVENT_KIND,
    SIGNER_DOMAIN,
    FailureReason,
    PromotionInputFailure,
    ReleaseBinding,
    produce_failure_receipt,
    produce_signed_receipt,
    validate_receipt_event,
)
from trustforge.release_manifest import ReleaseManifest
from trustforge.signed_event_ledger import SignedEventLedger

_MAX_JSON = 4 * 1024 * 1024


def _protected_json(
    path: Path,
    *,
    private: bool,
    max_bytes: int = _MAX_JSON,
    require_canonical: bool = False,
) -> Any:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        forbidden = 0o077 if private else 0o022
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(info.st_mode) & forbidden
            or info.st_nlink != 1
            or info.st_size > max_bytes
        ):
            raise SystemExit(f"unsafe protected input: {path}")
        raw = os.read(descriptor, max_bytes + 1)
        after = os.fstat(descriptor)
        reread = os.pread(descriptor, min(info.st_size, max_bytes + 1), 0)
        if (
            len(raw) != info.st_size
            or reread != raw
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        ):
            raise SystemExit(f"protected input changed during read: {path}")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid JSON input: {path}") from exc
    if require_canonical and canonical_json(value) + b"\n" != raw:
        raise SystemExit(f"non-canonical JSON input: {path}")
    return value


def _file_digest(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    digest = hashlib.sha256()
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SystemExit("bound artifact is unsafe")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return "sha256:" + digest.hexdigest()


def _release(
    path: Path,
    artifact: Path,
    *,
    active_artifact_digest: str,
    shadow_candidate_artifact_digest: str,
) -> ReleaseBinding:
    value = _protected_json(path, private=False, max_bytes=32_768)
    try:
        manifest = ReleaseManifest(**value)
    except (TypeError, ValueError) as exc:
        raise SystemExit("release manifest contract is invalid") from exc
    if _file_digest(artifact) != manifest.artifact_digest:
        raise SystemExit("release artifact identity is invalid")
    if manifest.artifact_digest != shadow_candidate_artifact_digest:
        raise SystemExit("release artifact is not the evaluated shadow candidate")
    return ReleaseBinding(
        git_sha=manifest.git_sha,
        active_artifact_digest=active_artifact_digest,
        shadow_candidate_artifact_digest=shadow_candidate_artifact_digest,
        artifact_digest=manifest.artifact_digest,
        release_id=f"release:{manifest.app_version}",
    )


def _shadow_identity(path: Path) -> ShadowReleaseIdentity:
    value = _protected_json(
        path,
        private=True,
        max_bytes=32_768,
        require_canonical=True,
    )
    if not isinstance(value, dict):
        raise SystemExit("shadow release identity is not canonical")
    try:
        return ShadowReleaseIdentity(**value)
    except (TypeError, ValueError) as exc:
        raise SystemExit("shadow release identity is invalid") from exc


def _benchmark_digest(path: Path, corpus: Path, repo_root: Path) -> str:
    from trustforge import asset_intrinsic_benchmark as benchmark

    checked_in = _protected_json(path, private=False)
    expected = benchmark.manifest_with_data_version(
        benchmark.run_benchmark(
            corpus,
            repo_root=repo_root,
            pit_cutoff=benchmark.PIT_CUTOFF,
            seed=benchmark.DEFAULT_SEED,
        ),
        corpus_path=corpus,
        repo_root=repo_root,
    )
    if checked_in != expected:
        raise ValueError("benchmark manifest is not reproducible")
    return _file_digest(path)


def _private_keyring(path: Path) -> tuple[str, bytes, dict[str, bytes]]:
    value = _protected_json(path, private=True, max_bytes=32_768)
    if not isinstance(value, dict) or set(value) != {
        "key_id",
        "private_key",
        "verification_keys",
    }:
        raise SystemExit("receipt private keyring contract is invalid")
    try:
        private = bytes.fromhex(value["private_key"])
        public = {
            key_id: bytes.fromhex(raw)
            for key_id, raw in value["verification_keys"].items()
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise SystemExit("receipt keyring encoding is invalid") from exc
    key_id = value["key_id"]
    if not isinstance(key_id, str) or key_id not in public:
        raise SystemExit("receipt signing key is not trusted")
    try:
        derived = (
            Ed25519PrivateKey.from_private_bytes(private)
            .public_key()
            .public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
    except ValueError as exc:
        raise SystemExit("receipt signing key is invalid") from exc
    if derived != public[key_id]:
        raise SystemExit("receipt signing key does not match verification key")
    return key_id, private, public


def _public_keyring(path: Path) -> dict[str, bytes]:
    value = _protected_json(path, private=False, max_bytes=32_768)
    if not isinstance(value, dict) or set(value) != {"verification_keys"}:
        raise SystemExit("receipt public keyring contract is invalid")
    try:
        return {
            key_id: bytes.fromhex(raw)
            for key_id, raw in value["verification_keys"].items()
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise SystemExit("receipt public keyring encoding is invalid") from exc


def _ledger(
    args: argparse.Namespace,
    public: dict[str, bytes],
    *,
    signer: tuple[str, bytes] | None,
) -> SignedEventLedger:
    key_id, private = signer or (None, None)
    test_owner = args.test_owner_current_user
    if test_owner and Path(tempfile.gettempdir()).resolve() not in (
        args.ledger_root.resolve(),
        *args.ledger_root.resolve().parents,
    ):
        raise SystemExit("test ownership mode requires a temporary ledger root")
    root_uid = os.geteuid() if test_owner else 0
    group = (
        grp.getgrgid(os.getegid()).gr_name
        if test_owner
        else "trustforge-release"
    )
    return SignedEventLedger(
        directory=args.ledger_root / "intrinsic-promotion-receipts",
        verification_keys=public,
        event_permissions={
            SIGNER_DOMAIN: frozenset({EVENT_KIND, FAILURE_EVENT_KIND})
        },
        domain_keys={SIGNER_DOMAIN: frozenset(public)},
        signing_key_id=key_id,
        signing_private_key=private,
        signing_domain=SIGNER_DOMAIN if signer else None,
        ledger_role="intrinsic-promotion-receipts",
        coordination_root=args.ledger_root,
        coordination_lock_path=args.ledger_root / "coordination.lock",
        coordination_lock_mode=0o660,
        coordination_lock_owner_uid=root_uid,
        coordination_lock_group=group,
        root_owner_uid=root_uid,
        root_group=group,
        root_mode=0o750,
        directory_group=group,
        directory_mode=0o750,
        file_mode=0o640,
    )


def _summary(event: dict[str, Any]) -> str:
    return json.dumps(
        {
            "evaluation_key": event["evaluation_key"],
            "decision": event["decision"],
            "reason_codes": event["reason_codes"],
        },
        sort_keys=True,
    )


def _run(args: argparse.Namespace) -> int:
    key_id, private, public = _private_keyring(args.receipt_keyring)
    ledger = _ledger(args, public, signer=(key_id, private))
    pit_cutoff = args.pit_cutoff or datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    ).isoformat().replace("+00:00", "Z")
    try:
        shadow_identity = _shadow_identity(args.shadow_release_identity)
    except (OSError, SystemExit, TypeError, ValueError):
        event = produce_failure_receipt(
            ledger=ledger,
            pit_cutoff=pit_cutoff,
            stage="shadow_identity",
            reason=FailureReason.SHADOW_IDENTITY_INVALID,
        )
        print(_summary(event))
        return 0
    try:
        release = _release(
            args.release_manifest,
            args.release_artifact,
            active_artifact_digest=shadow_identity.active_artifact_digest,
            shadow_candidate_artifact_digest=(
                shadow_identity.candidate_artifact_digest
            ),
        )
    except (OSError, SystemExit, TypeError, ValueError):
        event = produce_failure_receipt(
            ledger=ledger,
            pit_cutoff=pit_cutoff,
            stage="release_identity",
            reason=FailureReason.RELEASE_IDENTITY_INVALID,
        )
        print(_summary(event))
        return 0
    try:
        promotion_policy = load_intrinsic_promotion_policy(args.promotion_policy)
    except Exception:
        event = produce_failure_receipt(
            ledger=ledger,
            pit_cutoff=pit_cutoff,
            stage="promotion_policy",
            reason=FailureReason.POLICY_INVALID,
            release=release,
        )
        print(_summary(event))
        return 0

    def dataset_loader() -> dict[str, Any]:
        store = ShadowEvidenceStore(
            args.shadow_db,
            read_only=True,
            max_query_rows=args.limit,
            max_rows=max(args.limit, 10_000),
        )
        try:
            return build_promotion_evidence_dataset(
                store,
                shadow_identity,
                load_policy(),
                pit_cutoff=pit_cutoff,
                stale_after_days=args.stale_after_days,
                limit=args.limit,
            )
        finally:
            store.close()

    # Benchmark validation occurs inside the signed domain flow.  A malformed
    # benchmark is represented as an unavailable dataset BLOCK without leaking
    # parser details; it can never reach the evaluator.
    try:
        benchmark_digest = _benchmark_digest(
            args.benchmark_manifest, args.benchmark_corpus, args.repo_root
        )
    except Exception:
        benchmark_digest = "sha256:" + "0" * 64

        def dataset_loader() -> dict[str, Any]:
            raise PromotionInputFailure(
                "benchmark_manifest",
                FailureReason.BENCHMARK_MANIFEST_INVALID,
            )

    event = produce_signed_receipt(
        ledger=ledger,
        release=release,
        pit_cutoff=pit_cutoff,
        policy=promotion_policy,
        benchmark_manifest_digest=benchmark_digest,
        dataset_loader=dataset_loader,
    )
    print(_summary(event))
    return 0


def _verify(args: argparse.Namespace) -> int:
    public = _public_keyring(args.verification_keyring)
    records = _ledger(args, public, signer=None).read()
    events = [
        validate_receipt_event(record["event"])
        for record in records
        if record["event"].get("kind") in {EVENT_KIND, FAILURE_EVENT_KIND}
    ]
    if args.evaluation_key:
        events = [
            event
            for event in events
            if event["evaluation_key"] == args.evaluation_key
        ]
    if not events:
        raise SystemExit("no matching authenticated receipt")
    selected = events[-1]
    print(_summary(selected))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--shadow-db", type=Path, required=True)
    run.add_argument("--shadow-release-identity", type=Path, required=True)
    run.add_argument("--stale-after-days", type=int, required=True)
    run.add_argument("--limit", type=int, default=10_000)
    run.add_argument("--release-manifest", type=Path, required=True)
    run.add_argument("--release-artifact", type=Path, required=True)
    run.add_argument("--benchmark-manifest", type=Path, required=True)
    run.add_argument("--benchmark-corpus", type=Path, required=True)
    run.add_argument("--repo-root", type=Path, required=True)
    run.add_argument("--receipt-keyring", type=Path, required=True)
    run.add_argument("--ledger-root", type=Path, required=True)
    run.add_argument("--pit-cutoff")
    run.add_argument(
        "--promotion-policy",
        type=Path,
        default=POLICY_PATH,
    )
    run.add_argument("--test-owner-current-user", action="store_true", help=argparse.SUPPRESS)
    run.set_defaults(handler=_run)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--verification-keyring", type=Path, required=True)
    verify.add_argument("--ledger-root", type=Path, required=True)
    verify.add_argument("--evaluation-key")
    verify.add_argument(
        "--test-owner-current-user", action="store_true", help=argparse.SUPPRESS
    )
    verify.set_defaults(handler=_verify)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
