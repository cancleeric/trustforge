"""Authenticated, fd-bound evidence for release-level deployment readiness."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from trustforge.agent.shadow_contracts import (
    CONTRACT_VERSION,
    ShadowReleaseIdentity,
    canonical_json,
    load_policy,
)
from trustforge.agent.shadow_health_provenance import (
    ShadowHealthProvenanceError,
    verify_shadow_health_provenance,
)
from trustforge.safe_fs import pinned_directory, read_regular_file

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
REQUIRED_GATES = frozenset(
    {
        "health",
        "kernel_golden",
        "api_contract",
        "report",
        "evidence",
        "snapshot",
        "replay",
        "real_user_workflow",
        "rollback_drill",
    }
)


class EvidenceError(RuntimeError):
    """Evidence is absent, stale, forged, oversized, or identity-mismatched."""


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise EvidenceError("evidence timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise EvidenceError("evidence timestamp must be timezone aware")
    return parsed.astimezone(timezone.utc)


def _require_digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise EvidenceError(f"{name} must be a sha256 digest")
    return value


def _mac(secret: bytes, domain: bytes, payload: Mapping[str, Any]) -> str:
    if len(secret) < 32:
        raise EvidenceError("evidence key must be at least 32 bytes")
    return hmac.new(secret, domain + canonical_json(payload), hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    path: str
    digest: str
    size: int
    device: int
    inode: int


def snapshot_artifact(path: str | Path, expected_digest: str) -> ArtifactSnapshot:
    """Hash one immutable regular file through a pinned dir fd and stable fd."""
    expected = _require_digest(expected_digest, "artifact digest")
    target = Path(path)
    digest = hashlib.sha256()
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    with pinned_directory(target.parent) as parent_fd:
        fd = os.open(target.name, flags, dir_fd=parent_fd)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > 512 * 1024 * 1024:
                raise EvidenceError("artifact is not a bounded regular file")
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(fd)
        finally:
            os.close(fd)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise EvidenceError("artifact changed during verification")
    actual = "sha256:" + digest.hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise EvidenceError("artifact digest mismatch")
    return ArtifactSnapshot(
        path=str(target),
        digest=actual,
        size=after.st_size,
        device=after.st_dev,
        inode=after.st_ino,
    )


@dataclass(frozen=True, slots=True)
class ShadowHealthEvidence:
    report_digest: str
    evaluated_at: str
    identity: ShadowReleaseIdentity
    observation_root_digest: str
    aggregate_event_id: str
    decision_event_id: str
    policy_digest: str
    contract_version: str
    provider_calls: int
    cost_usd: float


def verify_shadow_health_export(
    path: str | Path,
    store_path: str | Path,
    *,
    expected_identity: ShadowReleaseIdentity,
    now: datetime,
    maximum_age: timedelta = timedelta(minutes=10),
) -> ShadowHealthEvidence:
    """Consume the canonical #732 health export, not a synthetic readiness file."""
    try:
        current_policy = load_policy()
        verified = verify_shadow_health_provenance(
            path,
            store_path,
            identity=expected_identity,
            policy=current_policy,
            now=now,
            maximum_age=maximum_age,
        )
    except ShadowHealthProvenanceError as exc:
        raise EvidenceError("shadow health lacks durable SQLite provenance") from exc
    if (
        expected_identity.contract_version != CONTRACT_VERSION
        or verified.metrics.get("provider_calls") != 0
        or verified.metrics.get("cost_usd") != 0
    ):
        raise EvidenceError("shadow contract or provider cost is invalid")
    return ShadowHealthEvidence(
        report_digest=verified.report_digest,
        evaluated_at=verified.evaluated_at,
        identity=verified.identity,
        observation_root_digest=verified.observation_root_digest,
        aggregate_event_id=verified.aggregate_event_id,
        decision_event_id=verified.decision_event_id,
        policy_digest=verified.identity.policy_digest,
        contract_version=verified.identity.contract_version,
        provider_calls=0,
        cost_usd=0.0,
    )


@dataclass(frozen=True, slots=True)
class GateReceipt:
    gate: str
    active_artifact_digest: str
    candidate_artifact_digest: str
    command_digest: str
    output_digest: str
    result: str
    provider_calls: int
    cost_usd: float
    executed_at: str
    expires_at: str
    key_id: str
    nonce: str
    signature: str
    receipt_version: str = "trustforge.executable-gate-receipt/v1"

    def unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("signature")
        return value


def verify_gate_receipts(
    paths: Mapping[str, str | Path],
    *,
    active_artifact_digest: str,
    candidate_artifact_digest: str,
    keyring: Mapping[str, bytes],
    now: datetime,
) -> tuple[GateReceipt, ...]:
    if set(paths) != REQUIRED_GATES:
        raise EvidenceError("exact executable gate set is required")
    receipts: list[GateReceipt] = []
    nonces: set[str] = set()
    for gate in sorted(paths):
        try:
            raw, info = read_regular_file(Path(paths[gate]), maximum_bytes=32_768)
            if info.st_mode & 0o077:
                raise EvidenceError("gate receipt permissions are too broad")
            receipt = GateReceipt(**json.loads(raw))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise EvidenceError("gate receipt is invalid") from exc
        secret = keyring.get(receipt.key_id)
        if secret is None:
            raise EvidenceError("gate receipt key_id is unknown")
        signature = _mac(secret, b"trustforge.gate-receipt.v1\x00", receipt.unsigned())
        if not hmac.compare_digest(receipt.signature, signature):
            raise EvidenceError("gate receipt signature is invalid")
        if (
            receipt.receipt_version
            != "trustforge.executable-gate-receipt/v1"
            or
            receipt.gate != gate
            or receipt.result != "pass"
            or receipt.active_artifact_digest != active_artifact_digest
            or receipt.candidate_artifact_digest != candidate_artifact_digest
            or receipt.provider_calls != 0
            or receipt.cost_usd != 0
        ):
            raise EvidenceError("gate receipt result, identity, or cost is invalid")
        for name in ("command_digest", "output_digest"):
            _require_digest(getattr(receipt, name), name)
        executed, expires = _utc(receipt.executed_at), _utc(receipt.expires_at)
        if executed > now or expires <= now or expires - executed > timedelta(hours=24):
            raise EvidenceError("gate receipt is stale or future-dated")
        if not receipt.nonce or receipt.nonce in nonces:
            raise EvidenceError("gate receipt nonce is empty or repeated")
        nonces.add(receipt.nonce)
        receipts.append(receipt)
    return tuple(receipts)


def evidence_bundle_digest(
    *,
    active: ArtifactSnapshot,
    candidate: ArtifactSnapshot,
    shadow: ShadowHealthEvidence,
    gates: tuple[GateReceipt, ...],
) -> str:
    payload = {
        "active": asdict(active),
        "candidate": asdict(candidate),
        "shadow": asdict(shadow),
        "gates": [receipt.unsigned() | {"signature": receipt.signature} for receipt in gates],
    }
    return "sha256:" + hashlib.sha256(
        b"trustforge.deployment-evidence.v1\x00" + canonical_json(payload)
    ).hexdigest()
