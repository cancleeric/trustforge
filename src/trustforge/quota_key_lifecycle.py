"""Fail-closed quota-key lifecycle and purpose-separated digest authority."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
import hashlib
import hmac
import json
import threading
from typing import Protocol

from trustforge.preview_admission_compiler import AdmissionCompileRequest
from trustforge.preview_trusted_clock import PreviewTrustedClock, TrustedUtcInterval


MAX_SNAPSHOT_AGE_SECONDS = 90
MIN_OVERLAP_SECONDS = 24 * 60 * 60 + MAX_SNAPSHOT_AGE_SECONDS
_QUOTA_DOMAIN = b"TrustForge/PAP1/quota-identity/v1\x00"
_OBS_DOMAIN = b"TrustForge/PAP1/observability/v1\x00"
_SNAPSHOT_TOKEN = object()
_DIGEST_TOKEN = object()
_BOUND_ADMISSION_TOKEN = object()
_RETIREMENT_TOKEN = object()
_KEY_MATERIAL_TOKEN = object()
LIFECYCLE_CONTROL_KEY = {
    "pk": {"S": "PAP#1#QUOTA-KEY"},
    "sk": {"S": "LIFECYCLE#CONTROL"},
}


class LifecycleMode(StrEnum):
    SINGLE = "single"
    OVERLAP = "overlap"


class LifecycleClient(Protocol):
    def get_item(self, **kwargs: object) -> object: ...

    def put_item(self, **kwargs: object) -> object: ...

    def transact_get_items(self, **kwargs: object) -> object: ...

    def transact_write_items(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True, init=False)
class RetirementCapability:
    lifecycle_generation: int
    previous_quota_key_version: int
    _authority: object = field(repr=False)
    _decision_nonce: object = field(repr=False)

    @classmethod
    def _mint(
        cls,
        token: object,
        generation: int,
        version: int,
        authority: object,
        decision_nonce: object,
    ) -> "RetirementCapability":
        if token is not _RETIREMENT_TOKEN:
            raise ValueError("invalid retirement capability")
        result = object.__new__(cls)
        object.__setattr__(result, "lifecycle_generation", generation)
        object.__setattr__(result, "previous_quota_key_version", version)
        object.__setattr__(result, "_authority", authority)
        object.__setattr__(result, "_decision_nonce", decision_nonce)
        return result


class QuotaKeyMaterialProvider:
    """Authenticate secret-manager metadata and mint nominal key material."""

    __slots__ = ("_nonce", "_revisions")

    def __init__(self) -> None:
        self._nonce = object()
        self._revisions: dict[str, bytes] = {}

    def verify(
        self,
        *,
        version: int,
        key_id: str,
        key_bytes: bytes,
        activated: int,
        source_revision: str,
        authenticated_revision: bool,
        csprng_provenance: bool,
        superseded: int | None = None,
        retire_not_before: int | None = None,
    ) -> "QuotaKey":
        if authenticated_revision is not True or csprng_provenance is not True:
            raise ValueError("unverified quota key material")
        prior = self._revisions.get(source_revision)
        if prior is not None and not hmac.compare_digest(prior, key_bytes):
            raise ValueError("source revision was rebound")
        self._revisions[source_revision] = key_bytes
        return QuotaKey._mint(
            _KEY_MATERIAL_TOKEN,
            self._nonce,
            version=version,
            key_id=key_id,
            key_bytes=key_bytes,
            activated=activated,
            source_revision=source_revision,
            superseded=superseded,
            retire_not_before=retire_not_before,
        )


@dataclass(frozen=True, slots=True, init=False)
class QuotaKey:
    version: int
    key_id: str
    key_bytes: bytes = field(repr=False)
    activated: int
    source_revision: str
    superseded: int | None = None
    retire_not_before: int | None = None
    _provider: object = field(repr=False)

    def __new__(cls, *args: object, **kwargs: object) -> "QuotaKey":
        del args, kwargs
        raise TypeError("quota key material must be provider-minted")

    @classmethod
    def _mint(
        cls,
        token: object,
        provider: object,
        **values: object,
    ) -> "QuotaKey":
        if token is not _KEY_MATERIAL_TOKEN:
            raise ValueError("invalid quota key material")
        result = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(result, name, value)
        object.__setattr__(result, "_provider", provider)
        result.__post_init__()
        return result

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version < 1
            or type(self.key_id) is not str
            or not self.key_id
            or len(self.key_id) > 64
            or type(self.key_bytes) is not bytes
            or len(self.key_bytes) < 32
            or type(self.source_revision) is not str
            or not self.source_revision
            or len(self.source_revision) > 128
            or self.source_revision != self.source_revision.strip()
            or type(self.activated) is not int
            or self.activated < 0
            or (self.superseded is None)
            != (self.retire_not_before is None)
        ):
            raise ValueError("invalid quota key")
        if self.superseded is not None and (
            type(self.superseded) is not int
            or type(self.retire_not_before) is not int
            or self.superseded < self.activated
            or self.retire_not_before
            < self.superseded + MIN_OVERLAP_SECONDS
        ):
            raise ValueError("invalid quota key")


@dataclass(frozen=True, slots=True)
class ObservabilityKey:
    version: int
    key_bytes: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version < 1
            or type(self.key_bytes) is not bytes
            or len(self.key_bytes) < 32
        ):
            raise ValueError("invalid observability key")


@dataclass(frozen=True, slots=True)
class QuotaKeyLifecycle:
    generation: int
    issued: TrustedUtcInterval
    current: QuotaKey
    previous: QuotaKey | None = None
    purpose: str = "pap1_quota"
    schema_version: int = 1

    def __post_init__(self) -> None:
        mode = LifecycleMode.OVERLAP if self.previous is not None else LifecycleMode.SINGLE
        if (
            type(self.generation) is not int
            or self.generation < 1
            or type(self.issued) is not TrustedUtcInterval
            or type(self.current) is not QuotaKey
            or self.purpose != "pap1_quota"
            or self.schema_version != 1
            or (
                (self.generation == 1 or self.previous is not None)
                and self.issued.latest > self.current.activated
            )
            or (
                mode is LifecycleMode.OVERLAP
                and (
                    type(self.previous) is not QuotaKey
                    or self.previous.version + 1 != self.current.version
                    or self.previous.key_id == self.current.key_id
                    or hmac.compare_digest(
                        self.previous.key_bytes, self.current.key_bytes
                    )
                    or self.previous.superseded != self.current.activated
                )
            )
        ):
            raise ValueError("invalid quota lifecycle")

    @property
    def mode(self) -> LifecycleMode:
        return (
            LifecycleMode.OVERLAP
            if self.previous is not None
            else LifecycleMode.SINGLE
        )


@dataclass(frozen=True, slots=True, init=False)
class QuotaLifecycleSnapshot:
    lifecycle: QuotaKeyLifecycle = field(repr=False)
    observed: TrustedUtcInterval = field(repr=False)
    _authority: object = field(repr=False)

    @classmethod
    def _create(
        cls,
        token: object,
        lifecycle: QuotaKeyLifecycle,
        observed: TrustedUtcInterval,
        authority: object,
    ) -> "QuotaLifecycleSnapshot":
        if token is not _SNAPSHOT_TOKEN:
            raise ValueError("invalid lifecycle snapshot")
        instance = object.__new__(cls)
        object.__setattr__(instance, "lifecycle", lifecycle)
        object.__setattr__(instance, "observed", observed)
        object.__setattr__(instance, "_authority", authority)
        return instance


@dataclass(frozen=True, slots=True, init=False)
class QuotaIdentityDigests:
    current: str = field(repr=False)
    previous: str | None = field(repr=False)
    generation: int
    current_version: int
    previous_version: int | None
    _snapshot: QuotaLifecycleSnapshot = field(repr=False)

    @classmethod
    def _create(
        cls,
        token: object,
        snapshot: QuotaLifecycleSnapshot,
        current: str,
        previous: str | None,
    ) -> "QuotaIdentityDigests":
        if token is not _DIGEST_TOKEN:
            raise ValueError("invalid quota digests")
        lifecycle = snapshot.lifecycle
        instance = object.__new__(cls)
        object.__setattr__(instance, "current", current)
        object.__setattr__(instance, "previous", previous)
        object.__setattr__(instance, "generation", lifecycle.generation)
        object.__setattr__(instance, "current_version", lifecycle.current.version)
        object.__setattr__(
            instance,
            "previous_version",
            lifecycle.previous.version if lifecycle.previous else None,
        )
        object.__setattr__(instance, "_snapshot", snapshot)
        return instance


@dataclass(frozen=True, slots=True, init=False)
class BoundAdmissionRequest:
    """Nominal request proving exact lifecycle-authority derivation."""

    request: AdmissionCompileRequest = field(repr=False)
    _digests: QuotaIdentityDigests = field(repr=False)
    _authority: object = field(repr=False)

    @classmethod
    def _create(
        cls,
        token: object,
        request: AdmissionCompileRequest,
        digests: QuotaIdentityDigests,
        authority: object,
    ) -> "BoundAdmissionRequest":
        if token is not _BOUND_ADMISSION_TOKEN:
            raise ValueError("invalid bound admission")
        instance = object.__new__(cls)
        object.__setattr__(instance, "request", request)
        object.__setattr__(instance, "_digests", digests)
        object.__setattr__(instance, "_authority", authority)
        return instance


@dataclass(frozen=True, slots=True, init=False)
class ObservabilityDigest:
    value: str = field(repr=False)
    version: int

    @classmethod
    def derive(
        cls, material: bytes, key: ObservabilityKey
    ) -> "ObservabilityDigest":
        if (
            type(material) is not bytes
            or not material
            or type(key) is not ObservabilityKey
        ):
            raise ValueError("invalid observability material")
        instance = object.__new__(cls)
        object.__setattr__(
            instance,
            "value",
            hmac.new(
                key.key_bytes,
                _OBS_DOMAIN + str(key.version).encode() + b"\x00" + material,
                hashlib.sha256,
            ).hexdigest(),
        )
        object.__setattr__(instance, "version", key.version)
        return instance


class QuotaKeyLifecycleAuthority:
    """Thread-safe monotonic lifecycle snapshots bound to a trusted clock."""

    def __init__(
        self,
        clock: PreviewTrustedClock,
        *,
        key_material_provider: QuotaKeyMaterialProvider,
    ) -> None:
        if (
            type(clock) is not PreviewTrustedClock
            or type(key_material_provider) is not QuotaKeyMaterialProvider
        ):
            raise ValueError("trusted clock required")
        self._clock = clock
        self._key_material_provider = key_material_provider
        self._nonce = object()
        self._lock = threading.RLock()
        self._lifecycle: QuotaKeyLifecycle | None = None
        self._observed: TrustedUtcInterval | None = None

    def install(self, lifecycle: QuotaKeyLifecycle) -> QuotaLifecycleSnapshot:
        if (
            type(lifecycle) is not QuotaKeyLifecycle
            or not self._owns_material(lifecycle)
        ):
            raise ValueError("invalid quota lifecycle")
        with self._lock:
            now = self._trusted_now()
            previous = self._lifecycle
            if (
                lifecycle.current.activated > now.earliest
                or lifecycle.issued.latest > now.earliest
                or (
                    previous is not None
                    and (
                        lifecycle.generation < previous.generation
                        or (
                            lifecycle.generation == previous.generation
                            and lifecycle != previous
                        )
                        or lifecycle.current.version < previous.current.version
                    )
                )
            ):
                raise ValueError("quota lifecycle rejected")
            self._lifecycle = lifecycle
            self._observed = now
            return self._snapshot(now)

    def snapshot(self) -> QuotaLifecycleSnapshot:
        with self._lock:
            now = self._trusted_now()
            if (
                self._lifecycle is None
                or self._observed is None
                or now.latest - self._observed.earliest
                > MAX_SNAPSHOT_AGE_SECONDS
            ):
                raise ValueError("quota lifecycle unavailable")
            return self._snapshot(now)

    def commit_bound(self, snapshot: QuotaLifecycleSnapshot) -> bool:
        with self._lock:
            try:
                current = self.snapshot()
            except Exception:
                return False
            return (
                type(snapshot) is QuotaLifecycleSnapshot
                and snapshot._authority is self._nonce
                and snapshot.lifecycle == current.lifecycle
            )

    def derive(
        self, snapshot: QuotaLifecycleSnapshot, identity_material: bytes
    ) -> QuotaIdentityDigests:
        if (
            not self.commit_bound(snapshot)
            or type(identity_material) is not bytes
            or not identity_material
        ):
            raise ValueError("quota identity unavailable")
        lifecycle = snapshot.lifecycle
        current = _quota_hmac(lifecycle.current, identity_material)
        previous = (
            _quota_hmac(lifecycle.previous, identity_material)
            if lifecycle.previous is not None
            else None
        )
        if previous is not None and hmac.compare_digest(current, previous):
            raise ValueError("quota digest collision")
        return QuotaIdentityDigests._create(
            _DIGEST_TOKEN, snapshot, current, previous
        )

    def bind_admission(
        self,
        request: AdmissionCompileRequest,
        digests: QuotaIdentityDigests,
    ) -> BoundAdmissionRequest:
        if (
            type(request) is not AdmissionCompileRequest
            or type(digests) is not QuotaIdentityDigests
            or not self.commit_bound(digests._snapshot)
        ):
            raise ValueError("quota admission unavailable")
        bound_request = replace(
            request,
            identity_digest=digests.current,
            previous_identity_digest=digests.previous,
            lifecycle_generation=digests.generation,
            current_quota_key_version=digests.current_version,
            previous_quota_key_version=digests.previous_version,
        )
        return BoundAdmissionRequest._create(
            _BOUND_ADMISSION_TOKEN, bound_request, digests, self._nonce
        )

    def validate_admission(
        self, bound: BoundAdmissionRequest
    ) -> AdmissionCompileRequest | None:
        """Revalidate authority identity, lifecycle currency, and freshness."""

        if type(bound) is not BoundAdmissionRequest:
            return None
        try:
            authority = bound._authority
            digests = bound._digests
            request = bound.request
        except AttributeError:
            return None
        if (
            authority is not self._nonce
            or type(digests) is not QuotaIdentityDigests
            or not self.commit_bound(digests._snapshot)
        ):
            return None
        if (
            type(request) is not AdmissionCompileRequest
            or request.identity_digest != digests.current
            or request.previous_identity_digest != digests.previous
            or request.lifecycle_generation != digests.generation
            or request.current_quota_key_version != digests.current_version
            or request.previous_quota_key_version != digests.previous_version
        ):
            return None
        return request

    def _snapshot(self, now: TrustedUtcInterval) -> QuotaLifecycleSnapshot:
        assert self._lifecycle is not None
        return QuotaLifecycleSnapshot._create(
            _SNAPSHOT_TOKEN, self._lifecycle, now, self._nonce
        )

    def _owns_material(self, lifecycle: QuotaKeyLifecycle) -> bool:
        return (
            lifecycle.current._provider is self._key_material_provider._nonce
            and (
                lifecycle.previous is None
                or lifecycle.previous._provider
                is self._key_material_provider._nonce
            )
        )

    def _trusted_now(self) -> TrustedUtcInterval:
        return (
            self._clock.refresh()
            if self._clock.needs_refresh()
            else self._clock.trusted_interval()
        )


class DurableQuotaKeyLifecycleAuthority(QuotaKeyLifecycleAuthority):
    """Persist lifecycle metadata and admit only exact monotonic transitions."""

    def __init__(
        self,
        clock: PreviewTrustedClock,
        *,
        dynamodb_client: LifecycleClient,
        table_name: str,
        key_material_provider: QuotaKeyMaterialProvider,
    ) -> None:
        if (
            getattr(clock, "_client", None) is not dynamodb_client
            or getattr(clock, "_table_name", None) != table_name
            or not callable(getattr(dynamodb_client, "get_item", None))
            or not callable(getattr(dynamodb_client, "put_item", None))
        ):
            raise ValueError("durable lifecycle storage mismatch")
        super().__init__(
            clock, key_material_provider=key_material_provider
        )
        self._client = dynamodb_client
        self._table = table_name
        self._durable_fingerprint: str | None = None
        self._retired_capabilities: set[RetirementCapability] = set()

    def install(
        self,
        lifecycle: QuotaKeyLifecycle,
        retirement: RetirementCapability | None = None,
    ) -> QuotaLifecycleSnapshot:
        if (
            type(lifecycle) is not QuotaKeyLifecycle
            or not self._owns_material(lifecycle)
        ):
            raise ValueError("invalid quota lifecycle")
        with self._lock:
            now = self._trusted_now()
            desired = _lifecycle_metadata(lifecycle)
            current = self._read_metadata()
            if current == desired:
                self._lifecycle = lifecycle
                self._observed = now
                self._durable_fingerprint = desired["config_fingerprint"]
                return self._snapshot(now)
            if (
                lifecycle.issued.latest > now.earliest
                or lifecycle.current.activated > now.earliest
                or now.latest - lifecycle.issued.earliest > MAX_SNAPSHOT_AGE_SECONDS
            ):
                raise ValueError("stale lifecycle transition")
            if current is None:
                if lifecycle.generation != 1 or lifecycle.mode is not LifecycleMode.SINGLE:
                    raise ValueError("invalid lifecycle bootstrap")
                condition = "attribute_not_exists(pk) AND attribute_not_exists(sk)"
                names = None
                values = None
            else:
                _require_transition(current, desired, retirement, self._nonce)
                condition = "#generation=:generation AND #fingerprint=:fingerprint"
                names = {
                    "#generation": "generation",
                    "#fingerprint": "config_fingerprint",
                }
                values = {
                    ":generation": {"N": str(current["generation"])},
                    ":fingerprint": {"S": current["config_fingerprint"]},
                }
            request: dict[str, object] = {
                "TableName": self._table,
                "Item": _encode_metadata(desired),
                "ConditionExpression": condition,
                "ReturnValues": "NONE",
            }
            if names is not None:
                request["ExpressionAttributeNames"] = names
                request["ExpressionAttributeValues"] = values
            try:
                response = self._client.put_item(**request)
            except Exception:
                if self._read_metadata() != desired:
                    raise ValueError("unresolved lifecycle write") from None
            else:
                if (
                    not _confirmed_ddb_success(response)
                    and self._read_metadata() != desired
                ):
                    raise ValueError("unresolved lifecycle write")
            self._lifecycle = lifecycle
            self._observed = now
            self._durable_fingerprint = desired["config_fingerprint"]
            return self._snapshot(now)

    def durable_current(self, snapshot: QuotaLifecycleSnapshot) -> bool:
        try:
            return (
                self.commit_bound(snapshot)
                and self._read_metadata()["config_fingerprint"]
                == self._durable_fingerprint
            )
        except Exception:
            return False

    def validate_admission(
        self, bound: BoundAdmissionRequest
    ) -> AdmissionCompileRequest | None:
        request = super().validate_admission(bound)
        if request is None or not self.durable_current(bound._digests._snapshot):
            return None
        return request

    def admission_condition(
        self, bound: BoundAdmissionRequest
    ) -> dict[str, object] | None:
        request = self.validate_admission(bound)
        if request is None or self._durable_fingerprint is None:
            return None
        return {
            "ConditionCheck": {
                "TableName": self._table,
                "Key": LIFECYCLE_CONTROL_KEY,
                "ConditionExpression": (
                    "#kind=:kind AND #schema=:schema "
                    "AND #generation=:generation AND #fingerprint=:fingerprint"
                ),
                "ExpressionAttributeNames": {
                    "#kind": "kind",
                    "#schema": "schema_version",
                    "#generation": "generation",
                    "#fingerprint": "config_fingerprint",
                },
                "ExpressionAttributeValues": {
                    ":kind": {"S": "quota_key_lifecycle_control"},
                    ":schema": {"N": "1"},
                    ":generation": {"N": str(request.lifecycle_generation)},
                    ":fingerprint": {"S": self._durable_fingerprint},
                },
            }
        }

    def retire_previous(self, capability: RetirementCapability) -> bool:
        """Atomically retire the previous key with every durable safety proof."""

        from trustforge.quota_key_retirement import (
            _control_condition,
            _decode_item,
            _decode_lifecycle,
            _decode_recovery,
            _decode_waterline,
            _recovery_condition,
            _require_open,
            _strong_read,
        )

        with self._lock:
            lifecycle = self._lifecycle
            if (
                type(capability) is not RetirementCapability
                or capability._authority is not self._nonce
                or capability in self._retired_capabilities
                or lifecycle is None
                or lifecycle.mode is not LifecycleMode.OVERLAP
                or lifecycle.generation != capability.lifecycle_generation
                or lifecycle.previous is None
                or lifecycle.previous.version
                != capability.previous_quota_key_version
            ):
                return False
            # Consume before dispatch so concurrent callers and ambiguous failures
            # can never replay the authority.
            self._retired_capabilities.add(capability)
            try:
                now = self._trusted_now()
                responses = _strong_read(self._client, self._table)
                waterline = _decode_waterline(_decode_item(responses[0]))
                recovery = _decode_recovery(_decode_item(responses[1]))
                control = _require_open(_decode_item(responses[2]))
                durable = _decode_lifecycle(_decode_item(responses[3]))
                if (
                    now.earliest <= waterline.retire_not_before
                    or now.earliest > waterline.retention_until
                    or recovery.version < waterline.required_recovery_version
                    or recovery.shard <= waterline.last_old_expiry_shard
                    or durable.generation != lifecycle.generation
                    or durable.current_version != lifecycle.current.version
                    or durable.previous_version != lifecycle.previous.version
                    or durable.config_fingerprint != self._durable_fingerprint
                ):
                    return False
                following = QuotaKeyLifecycle(
                    lifecycle.generation + 1,
                    now,
                    lifecycle.current,
                )
                desired = _lifecycle_metadata(following)
                proof = type("_Proof", (), {
                    "recovery": recovery,
                    "control": control,
                })()
                request = {
                    "TransactItems": [
                        {
                            "Put": {
                                "TableName": self._table,
                                "Item": _encode_metadata(desired),
                                "ConditionExpression": (
                                    "#generation=:generation "
                                    "AND #fingerprint=:fingerprint"
                                ),
                                "ExpressionAttributeNames": {
                                    "#generation": "generation",
                                    "#fingerprint": "config_fingerprint",
                                },
                                "ExpressionAttributeValues": {
                                    ":generation": {
                                        "N": str(lifecycle.generation)
                                    },
                                    ":fingerprint": {
                                        "S": self._durable_fingerprint
                                    },
                                },
                            }
                        },
                        {
                            "ConditionCheck": _waterline_condition(
                                self._table, waterline
                            )
                        },
                        {
                            "ConditionCheck": _recovery_condition(
                                self._table, proof
                            )
                        },
                        {
                            "ConditionCheck": _control_condition(
                                self._table, proof
                            )
                        },
                    ]
                }
                try:
                    response = self._client.transact_write_items(**request)
                except Exception:
                    if not self._prove_retired(
                        desired, waterline, recovery, control
                    ):
                        return False
                else:
                    if (
                        not _confirmed_ddb_success(response)
                        and not self._prove_retired(
                            desired, waterline, recovery, control
                        )
                    ):
                        return False
                self._lifecycle = following
                self._observed = now
                self._durable_fingerprint = desired["config_fingerprint"]
                return True
            except Exception:
                return False

    def _prove_retired(
        self,
        desired: dict[str, object],
        waterline: object,
        recovery: object,
        control: object,
    ) -> bool:
        try:
            from trustforge.quota_key_retirement import (
                _decode_item,
                _decode_recovery,
                _decode_waterline,
                _require_open,
                _strong_read,
            )

            responses = _strong_read(self._client, self._table)
            return (
                _decode_waterline(_decode_item(responses[0])) == waterline
                and _decode_recovery(_decode_item(responses[1])) == recovery
                and _require_open(_decode_item(responses[2])) == control
                and _decode_lifecycle_any(_decode_item(responses[3]))
                == desired
            )
        except Exception:
            return False

    def _read_metadata(self) -> dict[str, object] | None:
        response = self._client.get_item(
            TableName=self._table,
            Key=LIFECYCLE_CONTROL_KEY,
            ConsistentRead=True,
        )
        if type(response) is not dict or set(response) - {"Item", "ResponseMetadata"}:
            raise ValueError("malformed lifecycle response")
        if "Item" not in response:
            return None
        return _decode_metadata(response["Item"])


def _lifecycle_metadata(lifecycle: QuotaKeyLifecycle) -> dict[str, object]:
    value: dict[str, object] = {
        "pk": "PAP#1#QUOTA-KEY",
        "sk": "LIFECYCLE#CONTROL",
        "kind": "quota_key_lifecycle_control",
        "schema_version": 1,
        "generation": lifecycle.generation,
        "mode": lifecycle.mode.value,
        "current_version": lifecycle.current.version,
        "current_source_revision": lifecycle.current.source_revision,
        "current_activated": lifecycle.current.activated,
        "issued_earliest": lifecycle.issued.earliest,
        "issued_latest": lifecycle.issued.latest,
    }
    if lifecycle.previous is not None:
        value["previous_version"] = lifecycle.previous.version
        value["previous_source_revision"] = lifecycle.previous.source_revision
        value["previous_superseded"] = lifecycle.previous.superseded
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    value["config_fingerprint"] = hashlib.sha256(
        b"TrustForge/PAP1/quota-lifecycle-config/v1\x00" + canonical
    ).hexdigest()
    return value


def _require_transition(
    current: dict[str, object],
    desired: dict[str, object],
    retirement: RetirementCapability | None,
    authority: object,
) -> None:
    if desired == current:
        raise ValueError("lifecycle restart requires fresh transition")
    if (
        desired["generation"] != current["generation"] + 1
        or desired["issued_earliest"] <= current["issued_latest"]
    ):
        raise ValueError("non-monotonic lifecycle transition")
    if current["mode"] == "single" and desired["mode"] == "overlap":
        if (
            desired.get("previous_version") != current["current_version"]
            or desired.get("previous_source_revision")
            != current["current_source_revision"]
            or desired["current_version"] != current["current_version"] + 1
        ):
            raise ValueError("invalid overlap predecessor")
        return
    if current["mode"] == "overlap" and desired["mode"] == "single":
        if (
            type(retirement) is not RetirementCapability
            or retirement._authority is not authority
            or retirement.lifecycle_generation != current["generation"]
            or retirement.previous_quota_key_version != current["previous_version"]
            or desired["current_version"] != current["current_version"]
            or desired["current_source_revision"]
            != current["current_source_revision"]
        ):
            raise ValueError("retirement proof required")
        return
    raise ValueError("unsupported lifecycle transition")


def _encode_metadata(value: dict[str, object]) -> dict[str, object]:
    return {
        key: {"S": item} if type(item) is str else {"N": str(item)}
        for key, item in value.items()
    }


def _decode_metadata(item: object) -> dict[str, object]:
    if type(item) is not dict:
        raise ValueError("malformed lifecycle metadata")
    decoded: dict[str, object] = {}
    for key, value in item.items():
        if type(value) is not dict or len(value) != 1:
            raise ValueError("malformed lifecycle metadata")
        if "S" in value and type(value["S"]) is str:
            decoded[key] = value["S"]
        elif "N" in value and type(value["N"]) is str:
            text = value["N"]
            decoded[key] = float(text) if "." in text else int(text)
        else:
            raise ValueError("malformed lifecycle metadata")
    mode = decoded.get("mode")
    expected = {
        "pk",
        "sk",
        "kind",
        "schema_version",
        "generation",
        "mode",
        "current_version",
        "current_source_revision",
        "current_activated",
        "issued_earliest",
        "issued_latest",
        "config_fingerprint",
    }
    if mode == "overlap":
        expected |= {
            "previous_version",
            "previous_source_revision",
            "previous_superseded",
        }
    if (
        set(decoded) != expected
        or decoded.get("pk") != "PAP#1#QUOTA-KEY"
        or decoded.get("sk") != "LIFECYCLE#CONTROL"
        or decoded.get("kind") != "quota_key_lifecycle_control"
        or decoded.get("schema_version") != 1
        or mode not in {"single", "overlap"}
        or type(decoded.get("generation")) is not int
        or decoded["generation"] < 1
    ):
        raise ValueError("malformed lifecycle metadata")
    fingerprint = decoded.pop("config_fingerprint")
    canonical = json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
    decoded["config_fingerprint"] = fingerprint
    expected_fingerprint = hashlib.sha256(
        b"TrustForge/PAP1/quota-lifecycle-config/v1\x00" + canonical
    ).hexdigest()
    if not hmac.compare_digest(str(fingerprint), expected_fingerprint):
        raise ValueError("malformed lifecycle fingerprint")
    return decoded


def _confirmed_ddb_success(response: object) -> bool:
    if type(response) is not dict:
        return False
    metadata = response.get("ResponseMetadata")
    return (
        type(metadata) is dict
        and metadata.get("HTTPStatusCode") == 200
        and type(metadata.get("RequestId")) is str
        and bool(metadata["RequestId"])
    )


def _decode_lifecycle_any(item: dict[str, object]) -> dict[str, object]:
    return _decode_metadata(item)


def _waterline_condition(table: str, waterline: object) -> dict[str, object]:
    names = {
        "#version": "waterline_version",
        "#generation": "lifecycle_generation",
        "#previous": "previous_quota_key_version",
        "#current": "current_quota_key_version",
        "#upper": "last_old_admission_upper",
        "#shard": "last_old_expiry_shard",
        "#recovery": "required_recovery_version",
        "#retire": "retire_not_before",
        "#retention": "retention_until",
    }
    attributes = {
        ":version": waterline.waterline_version,
        ":generation": waterline.lifecycle_generation,
        ":previous": waterline.previous_quota_key_version,
        ":current": waterline.current_quota_key_version,
        ":upper": waterline.last_old_admission_upper,
        ":shard": waterline.last_old_expiry_shard,
        ":recovery": waterline.required_recovery_version,
        ":retire": waterline.retire_not_before,
        ":retention": waterline.retention_until,
    }
    return {
        "TableName": table,
        "Key": {
            "pk": {"S": "PAP#1#QUOTA-KEY"},
            "sk": {"S": "RETIREMENT#WATERLINE"},
        },
        "ConditionExpression": " AND ".join(
            f"#{name[1:]}={name}" for name in attributes
        ),
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": {
            name: {"N": str(value)} for name, value in attributes.items()
        },
    }


def _quota_hmac(key: QuotaKey, material: bytes) -> str:
    return hmac.new(
        key.key_bytes,
        _QUOTA_DOMAIN + str(key.version).encode() + b"\x00" + material,
        hashlib.sha256,
    ).hexdigest()
