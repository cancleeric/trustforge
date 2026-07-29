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


@dataclass(frozen=True, slots=True, init=False)
class RetirementCapability:
    lifecycle_generation: int
    previous_quota_key_version: int
    _authority: object = field(repr=False)

    @classmethod
    def _mint(
        cls, token: object, generation: int, version: int, authority: object
    ) -> "RetirementCapability":
        if token is not _RETIREMENT_TOKEN:
            raise ValueError("invalid retirement capability")
        result = object.__new__(cls)
        object.__setattr__(result, "lifecycle_generation", generation)
        object.__setattr__(result, "previous_quota_key_version", version)
        object.__setattr__(result, "_authority", authority)
        return result


@dataclass(frozen=True, slots=True)
class QuotaKey:
    version: int
    key_id: str
    key_bytes: bytes = field(repr=False)
    activated: int
    superseded: int | None = None
    retire_not_before: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version < 1
            or type(self.key_id) is not str
            or not self.key_id
            or len(self.key_id) > 64
            or type(self.key_bytes) is not bytes
            or len(self.key_bytes) < 32
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
            or self.issued.latest > self.current.activated
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

    def __init__(self, clock: PreviewTrustedClock) -> None:
        if type(clock) is not PreviewTrustedClock:
            raise ValueError("trusted clock required")
        self._clock = clock
        self._nonce = object()
        self._lock = threading.RLock()
        self._lifecycle: QuotaKeyLifecycle | None = None
        self._observed: TrustedUtcInterval | None = None

    def install(self, lifecycle: QuotaKeyLifecycle) -> QuotaLifecycleSnapshot:
        if type(lifecycle) is not QuotaKeyLifecycle:
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
    ) -> None:
        if (
            getattr(clock, "_client", None) is not dynamodb_client
            or getattr(clock, "_table_name", None) != table_name
            or not callable(getattr(dynamodb_client, "get_item", None))
            or not callable(getattr(dynamodb_client, "put_item", None))
        ):
            raise ValueError("durable lifecycle storage mismatch")
        super().__init__(clock)
        self._client = dynamodb_client
        self._table = table_name
        self._durable_fingerprint: str | None = None

    def install(
        self,
        lifecycle: QuotaKeyLifecycle,
        retirement: RetirementCapability | None = None,
    ) -> QuotaLifecycleSnapshot:
        if type(lifecycle) is not QuotaKeyLifecycle:
            raise ValueError("invalid quota lifecycle")
        with self._lock:
            now = self._trusted_now()
            if (
                lifecycle.issued.latest > now.earliest
                or now.latest - lifecycle.issued.earliest > MAX_SNAPSHOT_AGE_SECONDS
            ):
                raise ValueError("stale lifecycle transition")
            desired = _lifecycle_metadata(lifecycle)
            current = self._read_metadata()
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
                self._client.put_item(**request)
            except Exception:
                if self._read_metadata() != desired:
                    raise ValueError("unresolved lifecycle write") from None
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


def _key_fingerprint(key: QuotaKey) -> str:
    return hashlib.sha256(
        b"TrustForge/PAP1/quota-key-fingerprint/v1\x00"
        + str(key.version).encode()
        + b"\x00"
        + key.key_id.encode()
        + b"\x00"
        + key.key_bytes
    ).hexdigest()


def _lifecycle_metadata(lifecycle: QuotaKeyLifecycle) -> dict[str, object]:
    value: dict[str, object] = {
        "pk": "PAP#1#QUOTA-KEY",
        "sk": "LIFECYCLE#CONTROL",
        "kind": "quota_key_lifecycle_control",
        "schema_version": 1,
        "generation": lifecycle.generation,
        "mode": lifecycle.mode.value,
        "current_version": lifecycle.current.version,
        "current_fingerprint": _key_fingerprint(lifecycle.current),
        "issued_earliest": lifecycle.issued.earliest,
        "issued_latest": lifecycle.issued.latest,
    }
    if lifecycle.previous is not None:
        value["previous_version"] = lifecycle.previous.version
        value["previous_fingerprint"] = _key_fingerprint(lifecycle.previous)
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
            or desired.get("previous_fingerprint") != current["current_fingerprint"]
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
            or desired["current_fingerprint"] != current["current_fingerprint"]
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
        "current_fingerprint",
        "issued_earliest",
        "issued_latest",
        "config_fingerprint",
    }
    if mode == "overlap":
        expected |= {"previous_version", "previous_fingerprint"}
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


def _quota_hmac(key: QuotaKey, material: bytes) -> str:
    return hmac.new(
        key.key_bytes,
        _QUOTA_DOMAIN + str(key.version).encode() + b"\x00" + material,
        hashlib.sha256,
    ).hexdigest()
