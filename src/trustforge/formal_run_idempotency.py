"""Core contract for formal-run transport idempotency.

This module is deliberately transport- and provider-neutral.  It contains no
HTTP routing and performs no chargeable work.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import struct
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Literal, Mapping, Protocol, Sequence, runtime_checkable

CONTRACT_VERSION = "analysis-question/v1"
RECEIPT_SCHEMA_VERSION = "formal-run-receipt/v1"
_KEY_RE = re.compile(r"^tf1\.(\d{6})\.([A-Za-z0-9_-]{22})$")
_SAFE_REPLAY_HEADERS = frozenset({"Retry-After"})
_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_SCHEMA_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:/-]{0,127}$")
_HMAC_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("safe response body object keys must be strings")
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("safe response body must contain finite JSON numbers")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError("safe response body must contain JSON values only")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


class FormalRunIdempotencyError(Exception):
    """Base class with a fixed public error code."""

    code = "idempotency_unavailable"


class BadIdempotencyKey(FormalRunIdempotencyError):
    code = "bad_request"


class IdempotencyConflict(FormalRunIdempotencyError):
    code = "idempotency_conflict"


class IdempotencyInProgress(FormalRunIdempotencyError):
    code = "idempotency_request_in_progress"


class IdempotencyKeyUnavailable(FormalRunIdempotencyError):
    code = "idempotency_key_unavailable"


class IdempotencyUnavailable(FormalRunIdempotencyError):
    code = "idempotency_unavailable"


class StaleFencingToken(FormalRunIdempotencyError):
    code = "idempotency_unavailable"


@dataclass(frozen=True)
class ParsedIdempotencyKey:
    raw: str
    epoch: str

    def __post_init__(self) -> None:
        match = _KEY_RE.fullmatch(self.raw)
        if match is None or match.group(1) != self.epoch:
            raise ValueError("invalid parsed idempotency key")
        try:
            datetime.strptime(self.epoch, "%Y%m")
            decoded = base64.urlsafe_b64decode(match.group(2) + "==")
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid parsed idempotency key") from exc
        if (
            len(decoded) != 16
            or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != match.group(2)
        ):
            raise ValueError("invalid parsed idempotency key")


@dataclass(frozen=True)
class HmacValue:
    key_id: str
    digest: str

    def __post_init__(self) -> None:
        if _SAFE_ID.fullmatch(self.key_id) is None or _HMAC_DIGEST.fullmatch(self.digest) is None:
            raise ValueError("invalid HMAC value")


@dataclass(frozen=True)
class FormalRunIdentity:
    namespace: str
    scope_locator: str
    caller_scope_hmac: HmacValue
    key_hmac: HmacValue

    def __post_init__(self) -> None:
        if _SAFE_ID.fullmatch(self.namespace) is None:
            raise ValueError("invalid namespace")
        if _HMAC_DIGEST.fullmatch(self.scope_locator) is None:
            raise ValueError("invalid scope locator")


@dataclass(frozen=True)
class FormalRunLookup:
    parsed_key: ParsedIdempotencyKey
    primary_identity: FormalRunIdentity
    primary_fingerprint: HmacValue
    candidate_identities: tuple[FormalRunIdentity, ...] = ()
    candidate_fingerprints: tuple[HmacValue, ...] = ()

    def __post_init__(self) -> None:
        identities = (self.primary_identity, *self.candidate_identities)
        if any(item.namespace != self.primary_identity.namespace for item in identities):
            raise ValueError("lookup identities must share a namespace")
        if any(item.scope_locator != self.primary_identity.scope_locator for item in identities):
            raise ValueError("lookup identities must share a retention scope locator")
        identity_keys = {
            (
                item.caller_scope_hmac.key_id,
                item.caller_scope_hmac.digest,
                item.key_hmac.key_id,
                item.key_hmac.digest,
            )
            for item in identities
        }
        if len(identity_keys) != len(identities):
            raise ValueError("duplicate lookup identity")
        fingerprints = (self.primary_fingerprint, *self.candidate_fingerprints)
        if len({item.key_id for item in fingerprints}) != len(fingerprints):
            raise ValueError("duplicate fingerprint key id")


@dataclass(frozen=True)
class FormalRunReceipt:
    receipt_id: str
    question_id: str
    job_id: str
    result_id: str | None
    state: Literal["accepted", "execution_uncertain"]
    origin: Literal["manual"]
    disposition: Literal["created", "reused", "relocalized", "fresh-created"]
    locale: str
    created_at: str
    expires_at: str | None = None
    schema_version: str = RECEIPT_SCHEMA_VERSION
    fingerprint_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        for value in (self.receipt_id, self.question_id, self.job_id):
            if _SAFE_ID.fullmatch(value) is None:
                raise ValueError("invalid receipt identity")
        if self.result_id is not None and _SAFE_ID.fullmatch(self.result_id) is None:
            raise ValueError("invalid result identity")
        if self.state not in {"accepted", "execution_uncertain"}:
            raise ValueError("invalid receipt state")
        if self.origin != "manual" or self.disposition not in {
            "created", "reused", "relocalized", "fresh-created"
        }:
            raise ValueError("invalid receipt origin or disposition")
        if normalize_locale(self.locale) != self.locale:
            raise ValueError("receipt locale must be canonical")
        if self.schema_version != RECEIPT_SCHEMA_VERSION or self.fingerprint_version != CONTRACT_VERSION:
            raise ValueError("unsupported receipt schema")
        if self.expires_at is not None:
            raise ValueError("nonterminal receipt must not expire")
        _parse_utc_timestamp(self.created_at)

    def public_body(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TerminalSafeResponse:
    status: int
    code: str
    schema_version: str
    body: Mapping[str, object]
    replay_headers: Mapping[str, str]

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int) or not 400 <= self.status <= 599:
            raise ValueError("terminal status must be an HTTP error")
        if _SAFE_ID.fullmatch(self.code) is None or _SCHEMA_ID.fullmatch(self.schema_version) is None:
            raise ValueError("invalid terminal response metadata")
        if not isinstance(self.body, Mapping) or not isinstance(self.replay_headers, Mapping):
            raise ValueError("terminal response body and headers must be mappings")
        if any(key not in _SAFE_REPLAY_HEADERS for key in self.replay_headers):
            raise ValueError("terminal replay header is not allowlisted")
        retry_after = self.replay_headers.get("Retry-After")
        if retry_after is not None and (
            not isinstance(retry_after, str)
            or not re.fullmatch(r"[1-9]\d{0,3}", retry_after)
            or int(retry_after) > 3600
        ):
            raise ValueError("Retry-After must be bounded delta-seconds")
        # Defensive immutable copies prevent callers mutating what is meant to
        # become an exact durable replay after validation/digesting.
        object.__setattr__(self, "body", _freeze_json(dict(self.body)))
        object.__setattr__(self, "replay_headers", MappingProxyType(dict(self.replay_headers)))

    def canonical_body(self) -> str:
        return json.dumps(_thaw_json(self.body), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        artifact = json.dumps(
            {
                "body": _thaw_json(self.body),
                "code": self.code,
                "headers": dict(self.replay_headers),
                "schema_version": self.schema_version,
                "status": self.status,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(artifact.encode("utf-8")).hexdigest()


AcquireKind = Literal["owner", "replay", "terminal_replay", "in_progress", "conflict", "key_unavailable"]


@dataclass(frozen=True)
class AcquireResult:
    kind: AcquireKind
    fencing_token: int | None = None
    receipt: FormalRunReceipt | None = None
    terminal_response: TerminalSafeResponse | None = None
    authority_identity: FormalRunIdentity | None = None


@runtime_checkable
class FormalRunIdempotencyStore(Protocol):
    def acquire(
        self,
        *,
        lookup: FormalRunLookup,
        now: datetime,
        lease_seconds: int,
    ) -> AcquireResult: ...

    def bind(
        self,
        *,
        identity: FormalRunIdentity,
        fencing_token: int,
        receipt: FormalRunReceipt,
        operation_id: str,
        outbox_state: str,
        dispatch_state: str,
        reservation_id: str | None,
        max_reserved_cost: str | None,
        now: datetime,
        provider_operation_id: str | None = None,
        cost_policy_version: str | None = None,
        cost_policy_digest: str | None = None,
        settlement_state: str | None = None,
        reconciliation_state: str | None = None,
    ) -> None: ...

    def bind_with_content_decision(
        self,
        *,
        identity: FormalRunIdentity,
        fencing_token: int,
        receipt: FormalRunReceipt,
        operation_id: str,
        content: HmacValue,
        fresh: bool,
        now: datetime,
        reservation_id: str,
        max_reserved_cost: str,
        provider_operation_id: str,
        cost_policy_version: str,
        cost_policy_digest: str,
    ) -> FormalRunReceipt | None: ...

    def claim_dispatch(
        self, *, identity: FormalRunIdentity, fencing_token: int, now: datetime
    ) -> str: ...

    def mark_execution_uncertain(
        self, *, identity: FormalRunIdentity, fencing_token: int, now: datetime
    ) -> None: ...

    def pending_projection_token(self, *, identity: FormalRunIdentity) -> int | None: ...

    def complete_dispatch(
        self, *, identity: FormalRunIdentity, fencing_token: int, now: datetime
    ) -> None: ...

    def dispatch_resolution(
        self, *, identity: FormalRunIdentity, fencing_token: int
    ) -> Literal["pending", "claimed", "completed", "uncertain", "none"]: ...

    def provider_operation(
        self, *, identity: FormalRunIdentity, fencing_token: int
    ) -> str | None: ...

    def reservation_details(
        self, *, identity: FormalRunIdentity, fencing_token: int
    ) -> tuple[str, str] | None: ...

    def fail_terminal(
        self,
        *,
        identity: FormalRunIdentity,
        fencing_token: int,
        response: TerminalSafeResponse,
        now: datetime,
        expires_at: datetime,
    ) -> None: ...


def parse_idempotency_key(value: str | Sequence[str]) -> ParsedIdempotencyKey:
    if isinstance(value, str):
        raw = value
    else:
        if len(value) != 1:
            raise BadIdempotencyKey("exactly one Idempotency-Key is required")
        raw = value[0]
    if not isinstance(raw, str) or raw != raw.strip() or "," in raw:
        raise BadIdempotencyKey("malformed Idempotency-Key")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        raise BadIdempotencyKey("malformed Idempotency-Key")
    match = _KEY_RE.fullmatch(raw)
    if match is None:
        raise BadIdempotencyKey("malformed Idempotency-Key")
    epoch, random_part = match.groups()
    try:
        datetime.strptime(epoch, "%Y%m")
        decoded = base64.urlsafe_b64decode(random_part + "==")
    except (ValueError, TypeError) as exc:
        raise BadIdempotencyKey("malformed Idempotency-Key") from exc
    if len(decoded) != 16 or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != random_part:
        raise BadIdempotencyKey("noncanonical Idempotency-Key")
    return ParsedIdempotencyKey(raw=raw, epoch=epoch)


def normalize_locale(locale: str | None) -> str:
    value = (locale or "zh-Hant").strip()
    aliases = {"zh-TW": "zh-Hant", "zh-Hant": "zh-Hant", "en": "en"}
    if value not in aliases:
        raise ValueError("unsupported locale")
    return aliases[value]


def canonical_request_tuple(
    *, coin: str, mode: str, question: str, locale: str | None, fresh: bool = False
) -> bytes:
    values = (
        CONTRACT_VERSION,
        coin.strip().upper(),
        mode.strip(),
        question.strip(),
        normalize_locale(locale),
        "true" if fresh else "false",
    )
    encoded = bytearray()
    for value in values:
        field = value.encode("utf-8")
        encoded.extend(struct.pack(">Q", len(field)))
        encoded.extend(field)
    return bytes(encoded)


def _purpose_hmac(secret: bytes, purpose: bytes, payload: bytes) -> str:
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("HMAC secrets must contain at least 32 bytes")
    return hmac.new(secret, b"trustforge/formal-run/v1/" + purpose + b"\0" + payload, hashlib.sha256).hexdigest()


def request_fingerprint(
    secret: bytes,
    key_id: str,
    *,
    coin: str,
    mode: str,
    question: str,
    locale: str | None,
    fresh: bool = False,
) -> HmacValue:
    canonical = canonical_request_tuple(
        coin=coin, mode=mode, question=question, locale=locale, fresh=fresh
    )
    return HmacValue(key_id, _purpose_hmac(secret, b"fingerprint", canonical))


def content_fingerprint(
    secret: bytes,
    key_id: str,
    *,
    coin: str,
    mode: str,
    question: str,
) -> HmacValue:
    """Hash reusable analysis content without locale or freshness controls."""
    values = (CONTRACT_VERSION, coin.strip().upper(), mode.strip(), question.strip())
    encoded = bytearray()
    for value in values:
        field = value.encode("utf-8")
        encoded.extend(struct.pack(">Q", len(field)))
        encoded.extend(field)
    return HmacValue(key_id, _purpose_hmac(secret, b"content", bytes(encoded)))


def build_identity(
    *,
    namespace: str,
    caller_scope: str,
    parsed_key: ParsedIdempotencyKey,
    caller_secret: bytes,
    caller_key_id: str,
    idempotency_secret: bytes,
    idempotency_key_id: str,
    retention_locator_secret: bytes,
) -> FormalRunIdentity:
    if (
        not isinstance(caller_scope, str)
        or caller_scope != caller_scope.strip()
        or not caller_scope
        or len(caller_scope) > 512
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in caller_scope)
    ):
        raise IdempotencyUnavailable("trusted caller authority unavailable")
    if _SAFE_ID.fullmatch(namespace) is None:
        raise ValueError("invalid namespace")
    return FormalRunIdentity(
        namespace=namespace,
        scope_locator=_purpose_hmac(
            retention_locator_secret, b"scope-locator", caller_scope.encode("utf-8")
        ),
        caller_scope_hmac=HmacValue(
            caller_key_id, _purpose_hmac(caller_secret, b"caller-scope", caller_scope.encode("utf-8"))
        ),
        key_hmac=HmacValue(
            idempotency_key_id,
            _purpose_hmac(idempotency_secret, b"idempotency-key", parsed_key.raw.encode("ascii")),
        ),
    )


def accepted_acquisition_epochs(now: datetime) -> frozenset[str]:
    if now.tzinfo is None:
        raise IdempotencyUnavailable("trusted clock unavailable")
    utc = now.astimezone(timezone.utc)
    previous_year = utc.year if utc.month > 1 else utc.year - 1
    previous_month = utc.month - 1 if utc.month > 1 else 12
    return frozenset({f"{utc.year:04d}{utc.month:02d}", f"{previous_year:04d}{previous_month:02d}"})


def _parse_utc_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError("timestamp must be UTC")
    return parsed
