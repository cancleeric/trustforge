"""Fail-closed deployment authority for the paid-preview admission store.

This module deliberately does not route an endpoint.  It verifies the dedicated
store and produces a nominal runtime-ready result that #956 may consume later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import os
import re
from typing import Callable, Protocol


FEATURE_ENV = "TRUSTFORGE_PREVIEW_ADMISSION_ENABLED"
TABLE_ENV = "TRUSTFORGE_PREVIEW_ADMISSION_TABLE"
KEY_PARAMETER_ENV = "TRUSTFORGE_PREVIEW_QUOTA_KEY_PARAMETER"
DEFAULT_TABLE = "trustforge-preview-admission"
TTL_ATTRIBUTE = "ttl"
_TABLE_RE = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")
_PARAMETER_RE = re.compile(r"^/[A-Za-z0-9_.\-/]{1,1010}$")


class PreviewDeploymentStatus(StrEnum):
    DISABLED = "disabled"
    READY = "ready"
    UNAVAILABLE = "unavailable"


class PreviewReleaseStage(StrEnum):
    DARK = "dark"
    CANARY = "canary"
    ENABLED = "enabled"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class PreviewDeploymentConfig:
    requested: bool
    table_name: str
    quota_key_parameter: str
    expected_kms_key_arn: str
    expected_table_arn: str

    def __post_init__(self) -> None:
        if (
            type(self.requested) is not bool
            or type(self.table_name) is not str
            or not _TABLE_RE.fullmatch(self.table_name)
            or type(self.quota_key_parameter) is not str
            or not _PARAMETER_RE.fullmatch(self.quota_key_parameter)
            or type(self.expected_kms_key_arn) is not str
            or not self.expected_kms_key_arn.startswith("arn:")
            or ":kms:" not in self.expected_kms_key_arn
            or type(self.expected_table_arn) is not str
            or not self.expected_table_arn.startswith("arn:")
            or ":dynamodb:" not in self.expected_table_arn
            or not self.expected_table_arn.endswith(f":table/{self.table_name}")
        ):
            raise ValueError("invalid preview deployment config")

    @classmethod
    def from_env(
        cls,
        *,
        expected_kms_key_arn: str,
        expected_table_arn: str,
        environ: dict[str, str] | None = None,
    ) -> "PreviewDeploymentConfig":
        values = os.environ if environ is None else environ
        raw = values.get(FEATURE_ENV, "0")
        if raw not in {"0", "1"}:
            raise ValueError("preview flag must be exactly 0 or 1")
        return cls(
            requested=raw == "1",
            table_name=values.get(TABLE_ENV, DEFAULT_TABLE),
            quota_key_parameter=values.get(
                KEY_PARAMETER_ENV, "/trustforge/runtime/preview/quota-hmac"
            ),
            expected_kms_key_arn=expected_kms_key_arn,
            expected_table_arn=expected_table_arn,
        )


class PreviewDeploymentClient(Protocol):
    def describe_table(self, **kwargs: object) -> object: ...

    def describe_time_to_live(self, **kwargs: object) -> object: ...

    def describe_continuous_backups(self, **kwargs: object) -> object: ...

    def list_tags_of_resource(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class PreviewStoreReadiness:
    status: PreviewDeploymentStatus
    reason: str
    table_name: str
    checks: tuple[str, ...] = ()
    _runtime: object | None = field(default=None, repr=False)

    @property
    def enabled(self) -> bool:
        """Enabled means the requested feature has a verified store runtime."""

        return self.status is PreviewDeploymentStatus.READY and self._runtime is not None

    def runtime(self) -> object:
        if not self.enabled:
            raise ValueError("preview admission runtime unavailable")
        return self._runtime


class PreviewAdmissionRuntimeComposer:
    """Verify infrastructure before constructing any paid-preview runtime."""

    def __init__(
        self,
        *,
        client: PreviewDeploymentClient,
        config: PreviewDeploymentConfig,
        compose: Callable[[], object],
    ) -> None:
        if (
            not all(
                callable(getattr(client, method, None))
                for method in (
                    "describe_table",
                    "describe_time_to_live",
                    "describe_continuous_backups",
                    "list_tags_of_resource",
                )
            )
            or type(config) is not PreviewDeploymentConfig
            or not callable(compose)
        ):
            raise ValueError("invalid preview runtime composer")
        self._client = client
        self._config = config
        self._compose = compose

    def evaluate(self) -> PreviewStoreReadiness:
        if not self._config.requested:
            return PreviewStoreReadiness(
                PreviewDeploymentStatus.DISABLED,
                "feature_default_off",
                self._config.table_name,
            )
        try:
            checks = self._verify_store()
            runtime = self._compose()
            if runtime is None:
                raise ValueError("runtime composition failed")
        except Exception:
            return PreviewStoreReadiness(
                PreviewDeploymentStatus.UNAVAILABLE,
                "store_runtime_not_ready",
                self._config.table_name,
            )
        return PreviewStoreReadiness(
            PreviewDeploymentStatus.READY,
            "store_runtime_ready",
            self._config.table_name,
            checks,
            runtime,
        )

    def _verify_store(self) -> tuple[str, ...]:
        table_response = self._client.describe_table(
            TableName=self._config.table_name
        )
        table = _exact_payload(table_response, "Table")
        if (
            table.get("TableName") != self._config.table_name
            or table.get("TableArn") != self._config.expected_table_arn
            or table.get("TableStatus") != "ACTIVE"
            or table.get("BillingModeSummary", {}).get("BillingMode")
            != "PAY_PER_REQUEST"
            or table.get("KeySchema")
            != [
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ]
            or table.get("AttributeDefinitions")
            != [
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ]
            or table.get("SSEDescription", {}).get("Status") != "ENABLED"
            or table.get("SSEDescription", {}).get("SSEType") != "KMS"
            or table.get("SSEDescription", {}).get("KMSMasterKeyArn")
            != self._config.expected_kms_key_arn
        ):
            raise ValueError("table contract mismatch")
        ttl = _exact_payload(
            self._client.describe_time_to_live(
                TableName=self._config.table_name
            ),
            "TimeToLiveDescription",
        )
        if ttl != {"TimeToLiveStatus": "ENABLED", "AttributeName": TTL_ATTRIBUTE}:
            raise ValueError("ttl unavailable")
        backups = _exact_payload(
            self._client.describe_continuous_backups(
                TableName=self._config.table_name
            ),
            "ContinuousBackupsDescription",
        )
        pitr = backups.get("PointInTimeRecoveryDescription")
        if (
            backups.get("ContinuousBackupsStatus") != "ENABLED"
            or type(pitr) is not dict
            or pitr.get("PointInTimeRecoveryStatus") != "ENABLED"
        ):
            raise ValueError("pitr unavailable")
        tags = _exact_payload(
            self._client.list_tags_of_resource(
                ResourceArn=self._config.expected_table_arn
            ),
            "Tags",
        )
        tag_map = {
            item["Key"]: item["Value"]
            for item in tags
            if type(item) is dict and set(item) == {"Key", "Value"}
        }
        if tag_map.get("TrustForgeComponent") != "preview-admission":
            raise ValueError("dedicated table tag missing")
        return ("table", "kms", "ttl", "pitr", "dedicated")


@dataclass(frozen=True, slots=True)
class PreviewDisableObservation:
    control_state: str
    pending_binding: bool
    recovery_shard: int
    required_shard: int
    lifecycle_mode: str

    def __post_init__(self) -> None:
        if (
            self.control_state not in {"open", "dispatching", "quarantined"}
            or type(self.pending_binding) is not bool
            or type(self.recovery_shard) is not int
            or type(self.required_shard) is not int
            or min(self.recovery_shard, self.required_shard) < 0
            or self.lifecycle_mode not in {"single", "overlap"}
        ):
            raise ValueError("invalid disable observation")


@dataclass(frozen=True, slots=True)
class PreviewDisableDecision:
    safe_to_disable: bool
    reason: str


def evaluate_preview_disable(
    observation: PreviewDisableObservation,
) -> PreviewDisableDecision:
    """Bounded, read-only rollback check; it never deletes table or keys."""

    if type(observation) is not PreviewDisableObservation:
        return PreviewDisableDecision(False, "malformed_observation")
    if observation.control_state != "open" or observation.pending_binding:
        return PreviewDisableDecision(False, "pending_admission")
    if observation.recovery_shard <= observation.required_shard:
        return PreviewDisableDecision(False, "recovery_not_converged")
    if observation.lifecycle_mode != "single":
        return PreviewDisableDecision(False, "rotation_overlap_active")
    return PreviewDisableDecision(True, "disable_safe_retain_state")


def advance_release_stage(
    current: PreviewReleaseStage,
    target: PreviewReleaseStage,
    *,
    readiness: PreviewStoreReadiness,
    canary_verified: bool,
    disable_decision: PreviewDisableDecision | None = None,
) -> PreviewReleaseStage:
    """Pure deployment/canary/rollback state machine."""

    if type(current) is not PreviewReleaseStage or type(target) is not PreviewReleaseStage:
        raise ValueError("invalid release stage")
    if target is PreviewReleaseStage.CANARY:
        if current is not PreviewReleaseStage.DARK or not readiness.enabled:
            raise ValueError("canary requires ready dark runtime")
    elif target is PreviewReleaseStage.ENABLED:
        if current is not PreviewReleaseStage.CANARY or canary_verified is not True:
            raise ValueError("enable requires verified canary")
    elif target is PreviewReleaseStage.DISABLED:
        if (
            current not in {PreviewReleaseStage.CANARY, PreviewReleaseStage.ENABLED}
            or type(disable_decision) is not PreviewDisableDecision
            or not disable_decision.safe_to_disable
        ):
            raise ValueError("rollback requires converged recovery")
    else:
        raise ValueError("unsupported release transition")
    return target


def _exact_payload(response: object, name: str) -> object:
    if type(response) is not dict:
        raise ValueError("malformed AWS response")
    metadata = response.get("ResponseMetadata")
    if (
        type(metadata) is not dict
        or metadata.get("HTTPStatusCode") != 200
        or type(metadata.get("RequestId")) is not str
        or not metadata["RequestId"]
        or name not in response
    ):
        raise ValueError("unconfirmed AWS response")
    return response[name]
