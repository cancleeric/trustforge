"""Retry-safe signed recommendation receipts for intrinsic promotion evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable, Mapping

from trustforge.asset_intrinsic_promotion import (
    IntrinsicPromotionDecision,
    IntrinsicPromotionReason,
    IntrinsicPromotionReceipt,
    evaluate_promotion,
    policy_digest,
    receipt_canonical_dict,
    receipt_digest,
)
from trustforge.asset_intrinsic_promotion_dataset import (
    DATASET_SCHEMA_VERSION,
    IntrinsicPromotionDatasetError,
    promotion_observations,
)
from trustforge.signed_event_ledger import SignedEventLedger

SCHEMA_VERSION = "trustforge.intrinsic-promotion-signed-receipt/v1"
EVENT_KIND = "intrinsic_promotion_receipt"
FAILURE_EVENT_KIND = "intrinsic_promotion_evaluation_failed"
SIGNER_DOMAIN = "intrinsic-promotion-receipt"
_KEY_DOMAIN = b"trustforge.intrinsic-promotion-evaluation-key.v1\x00"
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


class PromotionReceiptError(RuntimeError):
    """A signed promotion receipt could not be safely produced or verified."""


class PromotionInputFailure(PromotionReceiptError):
    """A sanitized pre-evaluation failure that must become a signed BLOCK."""

    def __init__(self, stage: str, reason: "FailureReason") -> None:
        self.stage = stage
        self.reason = reason
        super().__init__(reason.value)


class FailureReason(StrEnum):
    CANONICAL_DATASET_UNAVAILABLE = "canonical_dataset_unavailable"
    CANONICAL_DATASET_INVALID = "canonical_dataset_invalid"
    RELEASE_IDENTITY_INVALID = "release_identity_invalid"
    SHADOW_IDENTITY_INVALID = "shadow_identity_invalid"
    POLICY_INVALID = "policy_invalid"
    BENCHMARK_MANIFEST_INVALID = "benchmark_manifest_invalid"
    EVALUATION_FAILED = "evaluation_failed"


@dataclass(frozen=True, slots=True)
class ReleaseBinding:
    git_sha: str
    active_artifact_digest: str
    shadow_candidate_artifact_digest: str
    artifact_digest: str
    release_id: str

    def __post_init__(self) -> None:
        if (
            _GIT_SHA_RE.fullmatch(self.git_sha) is None
            or _DIGEST_RE.fullmatch(self.active_artifact_digest) is None
            or _DIGEST_RE.fullmatch(self.shadow_candidate_artifact_digest) is None
            or _DIGEST_RE.fullmatch(self.artifact_digest) is None
            or not isinstance(self.release_id, str)
            or not self.release_id
        ):
            raise PromotionReceiptError("release identity is invalid")


def _canonical(value: Any) -> bytes:
    from trustforge.agent.shadow_contracts import canonical_json

    return canonical_json(value)


def _timestamp(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PromotionReceiptError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PromotionReceiptError(f"{label} is malformed") from exc
    if parsed.tzinfo is None:
        raise PromotionReceiptError(f"{label} lacks timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _evaluation_key(material: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_KEY_DOMAIN + _canonical(material)).hexdigest()


def _receipt_from_dict(value: Mapping[str, Any]) -> IntrinsicPromotionReceipt:
    try:
        return IntrinsicPromotionReceipt(
            receipt_domain_version=value["receipt_domain_version"],
            policy_digest=value["policy_digest"],
            observation_root_digest=value["observation_root_digest"],
            benchmark_manifest_digest=value["benchmark_manifest_digest"],
            evaluated_at=value["evaluated_at"],
            policy=dict(value["policy"]),
            decision=IntrinsicPromotionDecision(value["decision"]),
            reasons=tuple(IntrinsicPromotionReason(item) for item in value["reasons"]),
            calibration_claim=value["calibration_claim"],
            counts=dict(value["counts"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PromotionReceiptError("evaluator receipt is malformed") from exc


def validate_receipt_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstruct every binding in one authenticated ledger event."""
    value = dict(event)
    expected = {
        "kind",
        "schema_version",
        "evaluation_key",
        "nonce",
        "generated_at",
        "pit_cutoff",
        "dataset_schema_version",
        "dataset_digest",
        "policy_version",
        "policy_digest",
        "benchmark_manifest_digest",
        "git_sha",
        "active_artifact_digest",
        "shadow_candidate_artifact_digest",
        "artifact_digest",
        "release_id",
        "failure_stage",
        "failure_reason",
        "evaluator_receipt",
        "evaluator_receipt_digest",
        "decision",
        "reason_codes",
        "recommendation_only",
        "auto_promote",
    }
    if set(value) != expected or value["schema_version"] != SCHEMA_VERSION:
        raise PromotionReceiptError("signed receipt envelope is malformed")
    if value["kind"] not in {EVENT_KIND, FAILURE_EVENT_KIND}:
        raise PromotionReceiptError("signed receipt event kind is invalid")
    generated_at = _timestamp(value["generated_at"], "generated_at")
    pit_cutoff = _timestamp(value["pit_cutoff"], "pit_cutoff")
    if pit_cutoff > generated_at:
        raise PromotionReceiptError("pit_cutoff follows generated_at")
    if (
        value["recommendation_only"] is not True
        or value["auto_promote"] is not False
        or value["nonce"] != value["evaluation_key"]
    ):
        raise PromotionReceiptError("signed receipt binding is invalid")

    failure = value["kind"] == FAILURE_EVENT_KIND
    if failure:
        try:
            reason = FailureReason(value["failure_reason"])
        except ValueError as exc:
            raise PromotionReceiptError("failure reason is invalid") from exc
        dataset_fields_absent = (
            value["dataset_schema_version"] is None
            and value["dataset_digest"] is None
        )
        dataset_fields_valid = (
            value["dataset_schema_version"] == DATASET_SCHEMA_VERSION
            and _DIGEST_RE.fullmatch(str(value["dataset_digest"])) is not None
        )
        release_fields = (
            value["git_sha"],
            value["active_artifact_digest"],
            value["shadow_candidate_artifact_digest"],
            value["artifact_digest"],
            value["release_id"],
        )
        release_fields_absent = all(item is None for item in release_fields)
        try:
            if not release_fields_absent:
                ReleaseBinding(
                    git_sha=value["git_sha"],
                    active_artifact_digest=value["active_artifact_digest"],
                    shadow_candidate_artifact_digest=value[
                        "shadow_candidate_artifact_digest"
                    ],
                    artifact_digest=value["artifact_digest"],
                    release_id=value["release_id"],
                )
        except PromotionReceiptError as exc:
            raise PromotionReceiptError("failure release binding is malformed") from exc
        policy_absent = (
            value["policy_version"] is None and value["policy_digest"] is None
        )
        policy_valid = (
            isinstance(value["policy_version"], str)
            and bool(value["policy_version"])
            and _DIGEST_RE.fullmatch(str(value["policy_digest"])) is not None
        )
        benchmark_valid = value["benchmark_manifest_digest"] is None or (
            _DIGEST_RE.fullmatch(str(value["benchmark_manifest_digest"])) is not None
        )
        if (
            not isinstance(value["failure_stage"], str)
            or not value["failure_stage"]
            or not (dataset_fields_absent or dataset_fields_valid)
            or value["evaluator_receipt"] is not None
            or value["evaluator_receipt_digest"] is not None
            or value["decision"] != "block"
            or value["reason_codes"] != [reason.value]
            or not (release_fields_absent or all(item is not None for item in release_fields))
            or not (policy_absent or policy_valid)
            or not benchmark_valid
        ):
            raise PromotionReceiptError("failure receipt is malformed")
    else:
        release = ReleaseBinding(
            git_sha=value["git_sha"],
            active_artifact_digest=value["active_artifact_digest"],
            shadow_candidate_artifact_digest=value[
                "shadow_candidate_artifact_digest"
            ],
            artifact_digest=value["artifact_digest"],
            release_id=value["release_id"],
        )
        if (
            release.artifact_digest != release.shadow_candidate_artifact_digest
            or value["failure_stage"] is not None
            or value["failure_reason"] is not None
            or value["dataset_schema_version"] != DATASET_SCHEMA_VERSION
            or _DIGEST_RE.fullmatch(str(value["dataset_digest"])) is None
            or not isinstance(value["evaluator_receipt"], Mapping)
            or _DIGEST_RE.fullmatch(str(value["evaluator_receipt_digest"])) is None
            or _DIGEST_RE.fullmatch(str(value["policy_digest"])) is None
            or _DIGEST_RE.fullmatch(str(value["benchmark_manifest_digest"])) is None
            or not isinstance(value["policy_version"], str)
            or not value["policy_version"]
        ):
            raise PromotionReceiptError("success receipt is malformed")
        evaluator = _receipt_from_dict(value["evaluator_receipt"])
        if (
            receipt_digest(evaluator) != value["evaluator_receipt_digest"]
            or evaluator.policy_digest != value["policy_digest"]
            or evaluator.benchmark_manifest_digest
            != value["benchmark_manifest_digest"]
            or evaluator.policy.get("version") != value["policy_version"]
            or evaluator.decision.value != value["decision"]
            or [item.value for item in evaluator.reasons] != value["reason_codes"]
        ):
            raise PromotionReceiptError("evaluator receipt binding conflicts")

    key_material = {
        "schema_version": SCHEMA_VERSION,
        "pit_cutoff": pit_cutoff,
        "policy_version": value["policy_version"],
        "policy_digest": value["policy_digest"],
        "benchmark_manifest_digest": value["benchmark_manifest_digest"],
        "git_sha": value["git_sha"],
        "active_artifact_digest": value["active_artifact_digest"],
        "shadow_candidate_artifact_digest": value[
            "shadow_candidate_artifact_digest"
        ],
        "artifact_digest": value["artifact_digest"],
        "release_id": value["release_id"],
        "dataset_schema_version": value["dataset_schema_version"],
        "dataset_digest": value["dataset_digest"],
        "failure_stage": value["failure_stage"],
        "failure_reason": value["failure_reason"],
    }
    if value["evaluation_key"] != _evaluation_key(key_material):
        raise PromotionReceiptError("evaluation key binding conflicts")
    return value


def _find_existing(
    records: list[dict[str, Any]], evaluation_key: str
) -> dict[str, Any] | None:
    matches = [
        validate_receipt_event(record["event"])
        for record in records
        if record.get("event", {}).get("evaluation_key") == evaluation_key
    ]
    if len(matches) > 1:
        raise PromotionReceiptError("duplicate authenticated evaluation receipts")
    return matches[0] if matches else None


def _base_material(
    *,
    release: ReleaseBinding | None,
    pit_cutoff: str,
    policy_version: str | None,
    policy_digest_value: str | None,
    benchmark_manifest_digest: str | None,
    dataset_schema_version: str | None,
    dataset_digest: str | None,
    failure_stage: str | None,
    failure_reason: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "pit_cutoff": pit_cutoff,
        "policy_version": policy_version,
        "policy_digest": policy_digest_value,
        "benchmark_manifest_digest": benchmark_manifest_digest,
        "git_sha": release.git_sha if release else None,
        "active_artifact_digest": release.active_artifact_digest if release else None,
        "shadow_candidate_artifact_digest": (
            release.shadow_candidate_artifact_digest if release else None
        ),
        "artifact_digest": release.artifact_digest if release else None,
        "release_id": release.release_id if release else None,
        "dataset_schema_version": dataset_schema_version,
        "dataset_digest": dataset_digest,
        "failure_stage": failure_stage,
        "failure_reason": failure_reason,
    }


def produce_failure_receipt(
    *,
    ledger: SignedEventLedger,
    pit_cutoff: str,
    stage: str,
    reason: FailureReason,
    release: ReleaseBinding | None = None,
    policy_version: str | None = None,
    policy_digest_value: str | None = None,
    benchmark_manifest_digest: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Append a sanitized signed BLOCK after signer/ledger trust is established."""
    pit = _timestamp(pit_cutoff, "pit_cutoff")
    material = _base_material(
        release=release,
        pit_cutoff=pit,
        policy_version=policy_version,
        policy_digest_value=policy_digest_value,
        benchmark_manifest_digest=benchmark_manifest_digest,
        dataset_schema_version=None,
        dataset_digest=None,
        failure_stage=stage,
        failure_reason=reason.value,
    )
    key = _evaluation_key(material)
    with ledger.coordination_lock():
        existing = _find_existing(ledger.read(), key)
        if existing is not None:
            return existing
        generated_at = _timestamp(now().isoformat(), "generated_at")
        if pit > generated_at:
            raise PromotionReceiptError("pit_cutoff follows generated_at")
        event = {
            "kind": FAILURE_EVENT_KIND,
            **material,
            "evaluation_key": key,
            "nonce": key,
            "generated_at": generated_at,
            "evaluator_receipt": None,
            "evaluator_receipt_digest": None,
            "decision": "block",
            "reason_codes": [reason.value],
            "recommendation_only": True,
            "auto_promote": False,
        }
        validate_receipt_event(event)
        ledger.append(event)
        winner = _find_existing(ledger.read(), key)
        if winner != event:
            raise PromotionReceiptError("post-append failure receipt verification failed")
        return winner


def produce_signed_receipt(
    *,
    ledger: SignedEventLedger,
    release: ReleaseBinding,
    pit_cutoff: str,
    policy: Any,
    benchmark_manifest_digest: str,
    dataset_loader: Callable[[], Mapping[str, Any]],
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Evaluate and append once while holding the ledger coordination lock."""
    pit = _timestamp(pit_cutoff, "pit_cutoff")
    if _DIGEST_RE.fullmatch(benchmark_manifest_digest) is None:
        raise PromotionReceiptError("benchmark manifest digest is invalid")
    try:
        pdig = policy_digest(policy)
        policy_version = policy.version
    except Exception as exc:
        raise PromotionReceiptError("policy is invalid") from exc

    with ledger.coordination_lock():
        try:
            dataset = dict(dataset_loader())
            observations = promotion_observations(dataset)
            dataset_digest = dataset.get("dataset_digest")
            if (
                dataset.get("schema_version") != DATASET_SCHEMA_VERSION
                or _DIGEST_RE.fullmatch(str(dataset_digest)) is None
                or dataset.get("pit_cutoff") != pit
                or dataset.get("provenance", {})
                .get("release_identity", {})
                .get("active_artifact_digest")
                != release.active_artifact_digest
                or dataset.get("provenance", {})
                .get("release_identity", {})
                .get("candidate_artifact_digest")
                != release.shadow_candidate_artifact_digest
            ):
                raise IntrinsicPromotionDatasetError("canonical dataset binding is invalid")
        except Exception as exc:
            if isinstance(exc, PromotionInputFailure):
                reason, failure_stage = exc.reason, exc.stage
            else:
                reason = (
                    FailureReason.CANONICAL_DATASET_INVALID
                    if isinstance(exc, IntrinsicPromotionDatasetError)
                    else FailureReason.CANONICAL_DATASET_UNAVAILABLE
                )
                failure_stage = "canonical_dataset"
            material = _base_material(
                release=release,
                pit_cutoff=pit,
                policy_version=policy_version,
                policy_digest_value=pdig,
                benchmark_manifest_digest=benchmark_manifest_digest,
                dataset_schema_version=None,
                dataset_digest=None,
                failure_stage=failure_stage,
                failure_reason=reason.value,
            )
            key = _evaluation_key(material)
            existing = _find_existing(ledger.read(), key)
            if existing is not None:
                return existing
            generated_at = _timestamp(now().isoformat(), "generated_at")
            if pit > generated_at:
                raise PromotionReceiptError("pit_cutoff follows generated_at")
            event = {
                "kind": FAILURE_EVENT_KIND,
                **material,
                "evaluation_key": key,
                "nonce": key,
                "generated_at": generated_at,
                "evaluator_receipt": None,
                "evaluator_receipt_digest": None,
                "decision": "block",
                "reason_codes": [reason.value],
                "recommendation_only": True,
                "auto_promote": False,
            }
        else:
            material = _base_material(
                release=release,
                pit_cutoff=pit,
                policy_version=policy_version,
                policy_digest_value=pdig,
                benchmark_manifest_digest=benchmark_manifest_digest,
                dataset_schema_version=DATASET_SCHEMA_VERSION,
                dataset_digest=str(dataset_digest),
                failure_stage=None,
                failure_reason=None,
            )
            key = _evaluation_key(material)
            existing = _find_existing(ledger.read(), key)
            if existing is not None:
                return existing
            generated_at = _timestamp(now().isoformat(), "generated_at")
            if _timestamp(pit, "pit_cutoff") > generated_at:
                raise PromotionReceiptError("pit_cutoff follows generated_at")
            try:
                evaluator = evaluate_promotion(
                    policy,
                    observations,
                    benchmark_manifest_digest=benchmark_manifest_digest,
                    now=generated_at,
                )
            except Exception:
                reason = FailureReason.EVALUATION_FAILED
                material = _base_material(
                    release=release,
                    pit_cutoff=pit,
                    policy_version=policy_version,
                    policy_digest_value=pdig,
                    benchmark_manifest_digest=benchmark_manifest_digest,
                    dataset_schema_version=DATASET_SCHEMA_VERSION,
                    dataset_digest=str(dataset_digest),
                    failure_stage="evaluator",
                    failure_reason=reason.value,
                )
                key = _evaluation_key(material)
                existing = _find_existing(ledger.read(), key)
                if existing is not None:
                    return existing
                event = {
                    "kind": FAILURE_EVENT_KIND,
                    **material,
                    "evaluation_key": key,
                    "nonce": key,
                    "generated_at": generated_at,
                    "evaluator_receipt": None,
                    "evaluator_receipt_digest": None,
                    "decision": "block",
                    "reason_codes": [reason.value],
                    "recommendation_only": True,
                    "auto_promote": False,
                }
            else:
                event = {
                    "kind": EVENT_KIND,
                    **material,
                    "evaluation_key": key,
                    "nonce": key,
                    "generated_at": generated_at,
                    "evaluator_receipt": receipt_canonical_dict(evaluator),
                    "evaluator_receipt_digest": receipt_digest(evaluator),
                    "decision": evaluator.decision.value,
                    "reason_codes": [item.value for item in evaluator.reasons],
                    "recommendation_only": True,
                    "auto_promote": False,
                }
        validate_receipt_event(event)
        ledger.append(event)
        winner = _find_existing(ledger.read(), key)
        if winner is None or winner != event:
            raise PromotionReceiptError("post-append receipt verification failed")
        return winner
