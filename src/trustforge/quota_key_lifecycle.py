"""Fail-closed quota-key lifecycle and purpose-separated digest authority."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
import hashlib
import hmac
import threading

from trustforge.preview_admission_compiler import AdmissionCompileRequest
from trustforge.preview_trusted_clock import PreviewTrustedClock, TrustedUtcInterval


MAX_SNAPSHOT_AGE_SECONDS = 90
MIN_OVERLAP_SECONDS = 24 * 60 * 60 + MAX_SNAPSHOT_AGE_SECONDS
_QUOTA_DOMAIN = b"TrustForge/PAP1/quota-identity/v1\x00"
_OBS_DOMAIN = b"TrustForge/PAP1/observability/v1\x00"
_SNAPSHOT_TOKEN = object()
_DIGEST_TOKEN = object()


class LifecycleMode(StrEnum):
    SINGLE = "single"
    OVERLAP = "overlap"


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
class ObservabilityDigest:
    value: str = field(repr=False)
    version: int

    @classmethod
    def derive(cls, material: bytes, key: QuotaKey) -> "ObservabilityDigest":
        if type(material) is not bytes or not material:
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
    ) -> AdmissionCompileRequest:
        if (
            type(request) is not AdmissionCompileRequest
            or type(digests) is not QuotaIdentityDigests
            or not self.commit_bound(digests._snapshot)
        ):
            raise ValueError("quota admission unavailable")
        return replace(
            request,
            identity_digest=digests.current,
            previous_identity_digest=digests.previous,
        )

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


def _quota_hmac(key: QuotaKey, material: bytes) -> str:
    return hmac.new(
        key.key_bytes,
        _QUOTA_DOMAIN + str(key.version).encode() + b"\x00" + material,
        hashlib.sha256,
    ).hexdigest()
