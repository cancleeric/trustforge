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

from trustforge.preview_admission_executor import (
    AwsQuotaLifecycleBootstrap,
    PreviewAdmissionExecutor,
)
from trustforge.preview_lease_recovery import (
    PreviewAmbiguityRecovery,
    PreviewLeaseRecovery,
)
from trustforge.preview_terminal_reconcile import PreviewTerminalReconciler

FEATURE_ENV = "TRUSTFORGE_PREVIEW_ADMISSION_ENABLED"
TABLE_ENV = "TRUSTFORGE_PREVIEW_ADMISSION_TABLE"
KEY_PARAMETER_ENV = "TRUSTFORGE_PREVIEW_QUOTA_KEY_PARAMETER"
KEY_VERSION_ENV = "TRUSTFORGE_PREVIEW_QUOTA_KEY_VERSION"
KEY_INCARNATION_ENV = "TRUSTFORGE_PREVIEW_QUOTA_KEY_INCARNATION"
PREVIOUS_KEY_PARAMETER_ENV = "TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_PARAMETER"
PREVIOUS_KEY_VERSION_ENV = "TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_VERSION"
PREVIOUS_KEY_INCARNATION_ENV = "TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_INCARNATION"
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
    quota_key_version: int = 1
    quota_key_incarnation: str = "quota-1"
    previous_quota_key_parameter: str | None = None
    previous_quota_key_version: int | None = None
    previous_quota_key_incarnation: str | None = None

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
            or type(self.quota_key_version) is not int
            or self.quota_key_version < 1
            or type(self.quota_key_incarnation) is not str
            or not self.quota_key_incarnation
        ):
            raise ValueError("invalid preview deployment config")
        previous = (
            self.previous_quota_key_parameter,
            self.previous_quota_key_version,
            self.previous_quota_key_incarnation,
        )
        if any(value is not None for value in previous) and not (
            type(previous[0]) is str
            and _PARAMETER_RE.fullmatch(previous[0])
            and type(previous[1]) is int
            and previous[1] + 1 == self.quota_key_version
            and type(previous[2]) is str
            and bool(previous[2])
            and previous[2] != self.quota_key_incarnation
        ):
            raise ValueError("invalid previous quota key")

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
            quota_key_version=_positive_int(values.get(KEY_VERSION_ENV, "1")),
            quota_key_incarnation=values.get(KEY_INCARNATION_ENV, "quota-1"),
            previous_quota_key_parameter=values.get(PREVIOUS_KEY_PARAMETER_ENV),
            previous_quota_key_version=(
                _positive_int(values[PREVIOUS_KEY_VERSION_ENV])
                if PREVIOUS_KEY_VERSION_ENV in values
                else None
            ),
            previous_quota_key_incarnation=values.get(
                PREVIOUS_KEY_INCARNATION_ENV
            ),
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


@dataclass(frozen=True, slots=True)
class PreviewAdmissionProductionRuntime:
    """One composition-integrity graph shared by all preview authorities."""

    executor: PreviewAdmissionExecutor
    terminal: PreviewTerminalReconciler
    lease_recovery: PreviewLeaseRecovery
    ambiguity_recovery: PreviewAmbiguityRecovery

    def __post_init__(self) -> None:
        if (
            type(self.executor) is not PreviewAdmissionExecutor
            or type(self.terminal) is not PreviewTerminalReconciler
            or type(self.lease_recovery) is not PreviewLeaseRecovery
            or type(self.ambiguity_recovery) is not PreviewAmbiguityRecovery
        ):
            raise ValueError("invalid production preview runtime")
        client = self.executor._client
        table = self.executor._table_name
        clock = self.executor._durable_gate._trusted_clock
        if (
            self.executor._durable_gate._client is not client
            or self.executor._lifecycle_authority._client is not client
            or self.executor._lifecycle_authority._clock is not clock
            or self.terminal._client is not client
            or self.terminal._table_name != table
            or self.lease_recovery._client is not client
            or self.lease_recovery._terminal is not self.terminal
            or self.ambiguity_recovery._client is not client
            or self.ambiguity_recovery._terminal is not self.terminal
            or self.ambiguity_recovery._gate is not self.executor._durable_gate
        ):
            raise ValueError("split preview authority graph")

    @classmethod
    def from_aws_components(
        cls,
        table_name: str,
        *,
        lifecycle: AwsQuotaLifecycleBootstrap,
        region_name: str | None = None,
    ) -> "PreviewAdmissionProductionRuntime":
        """Use the executor's attempts=1 clients and share every authority."""

        executor = PreviewAdmissionExecutor.from_aws_components(
            table_name, lifecycle=lifecycle, region_name=region_name
        )
        client = executor._client
        terminal = PreviewTerminalReconciler(client, table_name)
        return cls(
            executor=executor,
            terminal=terminal,
            lease_recovery=PreviewLeaseRecovery(client, table_name, terminal),
            ambiguity_recovery=PreviewAmbiguityRecovery(
                client, table_name, terminal, executor._durable_gate
            ),
        )


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

    @classmethod
    def evaluate_production(
        cls,
        *,
        config: PreviewDeploymentConfig,
        lifecycle: AwsQuotaLifecycleBootstrap,
        region_name: str | None = None,
    ) -> PreviewStoreReadiness:
        """Compose the sealed #991/#992/#993 graph; off performs zero AWS I/O."""

        if not config.requested:
            return PreviewStoreReadiness(
                PreviewDeploymentStatus.DISABLED,
                "feature_default_off",
                config.table_name,
            )
        try:
            runtime = PreviewAdmissionProductionRuntime.from_aws_components(
                config.table_name,
                lifecycle=lifecycle,
                region_name=region_name,
            )
            verifier = cls(
                client=runtime.executor._client,
                config=config,
                compose=lambda: runtime,
            )
            result = verifier.evaluate()
            if not result.enabled:
                raise ValueError("production runtime unavailable")
            # These properties are backed by the same exact client and validate
            # control/lifecycle on construction; clock refresh is an explicit
            # authenticated, low-cardinality readiness sample.
            runtime.executor._durable_gate._trusted_clock.refresh()
            runtime.lease_recovery._read_watermark()
            if (
                not runtime.executor._durable_gate.ready
                or runtime.executor._lifecycle_authority._lifecycle is None
            ):
                raise ValueError("authority rows unavailable")
            return PreviewStoreReadiness(
                PreviewDeploymentStatus.READY,
                "production_runtime_ready",
                config.table_name,
                result.checks + ("control", "watermark", "lifecycle", "clock"),
                runtime,
            )
        except Exception:
            return PreviewStoreReadiness(
                PreviewDeploymentStatus.UNAVAILABLE,
                "production_runtime_not_ready",
                config.table_name,
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


def bounded_admin_recover_and_disable_check(
    runtime: PreviewAdmissionProductionRuntime,
    *,
    required_shard: int,
) -> PreviewDisableDecision:
    """Run only bounded #993 recovery, then strongly re-observe disable proof."""

    if (
        type(runtime) is not PreviewAdmissionProductionRuntime
        or type(required_shard) is not int
        or required_shard < 0
    ):
        return PreviewDisableDecision(False, "malformed_admin_request")
    try:
        clock = runtime.executor._durable_gate._trusted_clock
        interval = clock.refresh()
        gate = runtime.executor._durable_gate
        if not gate.ready and not runtime.executor.recover_pending(
            runtime.ambiguity_recovery
        ):
            return PreviewDisableDecision(False, "pending_admission")
        runtime.lease_recovery.run(interval)
        watermark = runtime.lease_recovery._read_watermark()
        control = gate._control
        lifecycle = runtime.executor._lifecycle_authority._lifecycle
        if control is None or lifecycle is None:
            raise ValueError("authority unavailable")
        return evaluate_preview_disable(
            PreviewDisableObservation(
                control_state=control.state.value,
                pending_binding=control.binding is not None,
                recovery_shard=watermark.shard,
                required_shard=required_shard,
                lifecycle_mode=lifecycle.mode.value,
            )
        )
    except Exception:
        return PreviewDisableDecision(False, "admin_probe_unavailable")


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


def _positive_int(value: object) -> int:
    if type(value) is not str or not value.isascii() or not value.isdigit():
        raise ValueError("expected positive integer")
    result = int(value)
    if result < 1:
        raise ValueError("expected positive integer")
    return result
