"""Fail-closed, secret-safe data contracts for the Hermes production audit.

This module deliberately has no AWS clients or mutation APIs. It is the pure,
stdlib-only boundary used by later audit collectors before they write local
evidence. Remote payloads must be reduced in memory; raw SSM output and
DynamoDB items are not valid contract fields.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

AUDIT_SCHEMA_VERSION = "hermes-production-audit.v1"
AUDIT_DIGEST_DOMAIN = b"trustforge.hermes-production-audit.v1\x00"
MAX_CANONICAL_BYTES = 1_000_000
MAX_TEXT_BYTES = 4_096
MAX_COLLECTION_ITEMS = 1_000
MAX_NESTING_DEPTH = 16
MAX_NODES = 20_000
MAX_INTEGER = 2**63 - 1

SYSTEMD_UNIT_ALLOWLIST = (
    "hermes-cycle.timer",
    "hermes-cycle.service",
    "trustforge-analysis-flow.service",
    "fetch-scheduler.timer",
    "fetch-scheduler.service",
)
TABLE_TYPE_ALLOWLIST = (
    "admin-config",
    "scheduler-run",
    "connector-cache",
    "cost-ledger",
    "durable-dead-letter",
)
EXIT_CODES = MappingProxyType({
    "complete": 0,
    "blocked": 2,
    "partial": 3,
    "insufficient-evidence": 3,
    "integrity-failure": 4,
    "internal-failure": 5,
})

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_AUDIT_ID_RE = re.compile(r"audit-[a-z0-9][a-z0-9-]{7,63}\Z")
_REGION_RE = re.compile(r"[a-z]{2}-[a-z]+-\d\Z")
_INSTANCE_ID_RE = re.compile(r"i-[0-9a-f]{8,17}\Z")
_SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}\Z")
_COIN_RE = re.compile(r"[A-Z]{2,16}\Z")
# Shared by hermes_audit.py (expected_release CLI grammar) and
# hermes_audit_signing.py (ApprovalAttestationV1.expected_release binding) so
# both modules enforce the exact same release-identity grammar.
SAFE_RELEASE_RE = re.compile(r"[A-Za-z0-9._@+-]{1,256}\Z")
_SECRET_KEY_RE = re.compile(
    r"(?:^|[_\-])(api[_\-]?key|access[_\-]?key|secret|token|password|"
    r"credential|authorization|private[_\-]?key|ssm[_\-]?prefix|parameter[_\-]?prefix)(?:$|[_\-])",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ASIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\b(?:ghp|ghs|github_pat)_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(
        r"\b(?:api[_-]?key|token|secret|password|authorization|x-api-key)\b"
        r"\s*(?:=|:|\s)\s*['\"]?[^\s'\"]{6,}",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:ssm|parameter)[_-]?prefix\b\s*(?:=|:)\s*\S+", re.IGNORECASE),
)


class AuditContractError(ValueError):
    """Raised when audit evidence is malformed, unsafe, or outside bounds."""


class AuditStatus(str, Enum):
    COMPLETE = "complete"
    BLOCKED = "blocked"
    PARTIAL = "partial"
    INSUFFICIENT_EVIDENCE = "insufficient-evidence"
    INTEGRITY_FAILURE = "integrity-failure"
    INTERNAL_FAILURE = "internal-failure"


class ReleaseComparisonStatus(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class ControlState(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


def _require_text(value: str, name: str, *, max_bytes: int = MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > max_bytes:
        raise AuditContractError(f"{name} must be non-empty and bounded")
    return value


def _require_safe_identifier(value: str, name: str) -> str:
    _require_text(value, name, max_bytes=256)
    if _SAFE_IDENTIFIER_RE.fullmatch(value) is None or has_secret_like_value(value):
        raise AuditContractError(f"{name} must be a secret-free safe identifier")
    return value


def _require_digest(value: str, name: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise AuditContractError(f"{name} must be a lowercase sha256 digest")
    return value


def _require_status(value: AuditStatus | str, name: str) -> AuditStatus:
    try:
        return AuditStatus(value)
    except (TypeError, ValueError) as exc:
        raise AuditContractError(f"{name} is not a supported audit status") from exc


def _parse_utc(value: str, name: str) -> str:
    _require_text(value, name, max_bytes=64)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuditContractError(f"{name} must be ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise AuditContractError(f"{name} must be UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise AuditContractError(f"{name} fields do not exactly match its schema")


def _bounded_nonnegative_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > MAX_INTEGER:
        raise AuditContractError(f"{name} must be a bounded non-negative integer")
    return value


def has_secret_like_value(value: str) -> bool:
    """Return true for known credential material and credential-style assignments."""
    return isinstance(value, str) and any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def secret_like_paths(value: Any) -> tuple[str, ...]:
    """Find secret-bearing paths without returning any sensitive value."""
    found: list[str] = []
    seen: set[int] = set()

    def visit(node: Any, path: str, depth: int) -> None:
        if depth > MAX_NESTING_DEPTH:
            raise AuditContractError("secret scan exceeds maximum nesting depth")
        if isinstance(node, str):
            if has_secret_like_value(node):
                found.append(path)
            return
        if isinstance(node, Mapping):
            marker = id(node)
            if marker in seen:
                raise AuditContractError("secret scan detected a cycle")
            seen.add(marker)
            if len(node) > MAX_COLLECTION_ITEMS:
                raise AuditContractError("secret scan collection exceeds limit")
            for key, item in node.items():
                if not isinstance(key, str):
                    raise AuditContractError("secret scan requires string object keys")
                child = f"{path}.{key}" if path else key
                if _SECRET_KEY_RE.search(key):
                    found.append(child)
                visit(item, child, depth + 1)
            seen.remove(marker)
            return
        if isinstance(node, (list, tuple)):
            marker = id(node)
            if marker in seen:
                raise AuditContractError("secret scan detected a cycle")
            seen.add(marker)
            if len(node) > MAX_COLLECTION_ITEMS:
                raise AuditContractError("secret scan collection exceeds limit")
            for index, item in enumerate(node):
                visit(item, f"{path}[{index}]", depth + 1)
            seen.remove(marker)

    visit(value, "", 0)
    return tuple(found)


def redact_secrets(value: Any) -> Any:
    """Return a shape-preserving redaction suitable for local audit evidence."""
    seen: set[int] = set()

    def redact(node: Any, depth: int) -> Any:
        if depth > MAX_NESTING_DEPTH:
            raise AuditContractError("redaction exceeds maximum nesting depth")
        if isinstance(node, str):
            return "[REDACTED]" if has_secret_like_value(node) else node
        if isinstance(node, Mapping):
            marker = id(node)
            if marker in seen:
                raise AuditContractError("redaction detected a cycle")
            seen.add(marker)
            if len(node) > MAX_COLLECTION_ITEMS:
                raise AuditContractError("redaction collection exceeds limit")
            result: dict[str, Any] = {}
            for key, item in node.items():
                if not isinstance(key, str):
                    raise AuditContractError("redaction requires string object keys")
                result[key] = "[REDACTED]" if _SECRET_KEY_RE.search(key) else redact(item, depth + 1)
            seen.remove(marker)
            return result
        if isinstance(node, (list, tuple)):
            marker = id(node)
            if marker in seen:
                raise AuditContractError("redaction detected a cycle")
            seen.add(marker)
            if len(node) > MAX_COLLECTION_ITEMS:
                raise AuditContractError("redaction collection exceeds limit")
            result = [redact(item, depth + 1) for item in node]
            seen.remove(marker)
            return result
        return node

    return redact(value, 0)


def canonical_json(value: Any) -> bytes:
    """Serialize bounded JSON deterministically, rejecting unsafe structures."""
    seen: set[int] = set()
    nodes = 0

    def validate(node: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_NESTING_DEPTH:
            raise AuditContractError("canonical JSON exceeds structural limits")
        if isinstance(node, str):
            if len(node.encode("utf-8")) > MAX_TEXT_BYTES:
                raise AuditContractError("canonical JSON string exceeds size limit")
        elif node is None or isinstance(node, bool):
            return
        elif isinstance(node, int):
            if abs(node) > MAX_INTEGER:
                raise AuditContractError("canonical JSON integer exceeds range")
        elif isinstance(node, float):
            if not math.isfinite(node):
                raise AuditContractError("canonical JSON number must be finite")
        elif isinstance(node, Mapping):
            marker = id(node)
            if marker in seen:
                raise AuditContractError("canonical JSON cycle detected")
            seen.add(marker)
            if len(node) > MAX_COLLECTION_ITEMS or any(not isinstance(key, str) for key in node):
                raise AuditContractError("canonical JSON object is invalid or oversized")
            for key, item in node.items():
                validate(key, depth + 1)
                validate(item, depth + 1)
            seen.remove(marker)
        elif isinstance(node, (list, tuple)):
            marker = id(node)
            if marker in seen:
                raise AuditContractError("canonical JSON cycle detected")
            seen.add(marker)
            if len(node) > MAX_COLLECTION_ITEMS:
                raise AuditContractError("canonical JSON collection exceeds size limit")
            for item in node:
                validate(item, depth + 1)
            seen.remove(marker)
        else:
            raise AuditContractError("value is not canonical JSON")

    validate(value, 0)
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise AuditContractError("value is not canonical JSON") from exc
    if len(encoded) > MAX_CANONICAL_BYTES:
        raise AuditContractError("canonical payload exceeds size limit")
    return encoded


def sha256_digest(value: Any, *, domain: bytes = AUDIT_DIGEST_DOMAIN) -> str:
    """Return a domain-separated digest of canonical audit data."""
    return "sha256:" + hashlib.sha256(domain + canonical_json(value)).hexdigest()


def redact_or_reject_remote_payload(value: Any) -> None:
    """Reject remote payloads before they could be copied into an evidence bundle."""
    if secret_like_paths(value):
        raise AuditContractError("secret-like remote payload must not be persisted")


def format_safe_error(*, operation: str, resource: str, error: BaseException) -> dict[str, str]:
    """Format only safe, structured error metadata; never serialize exception text."""
    _require_safe_identifier(operation, "operation")
    _require_safe_identifier(resource, "resource")
    error_class = type(error).__name__
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", error_class):
        error_class = "UnhandledError"
    return {"operation": operation, "resource": resource, "error_class": error_class}


@dataclass(frozen=True, slots=True)
class ReadBudget:
    request_limit: int
    item_limit: int
    byte_limit: int
    timeout_seconds: int
    retry_limit: int

    def __post_init__(self) -> None:
        for name in ("request_limit", "item_limit", "byte_limit", "timeout_seconds", "retry_limit"):
            _bounded_nonnegative_int(getattr(self, name), name)
        if not self.request_limit or not self.timeout_seconds:
            raise AuditContractError("read budgets require positive request and timeout limits")


DEFAULT_READ_BUDGETS = MappingProxyType({
    "ssm": ReadBudget(request_limit=12, item_limit=16, byte_limit=262_144, timeout_seconds=15, retry_limit=2),
    "dynamodb": ReadBudget(request_limit=24, item_limit=200, byte_limit=524_288, timeout_seconds=10, retry_limit=2),
    "local-io": ReadBudget(request_limit=12, item_limit=32, byte_limit=1_048_576, timeout_seconds=5, retry_limit=1),
})
DEFAULT_AUDIT_DEADLINE_SECONDS = 300


@dataclass(frozen=True, slots=True)
class AuditLimits:
    overall_deadline_seconds: int
    read_budgets: Mapping[str, ReadBudget]

    def __post_init__(self) -> None:
        if not isinstance(self.overall_deadline_seconds, int) or not 0 < self.overall_deadline_seconds <= DEFAULT_AUDIT_DEADLINE_SECONDS:
            raise AuditContractError("overall deadline must be positive and may only be lowered")
        _require_exact_keys(self.read_budgets, set(DEFAULT_READ_BUDGETS), "read_budgets")
        for name, budget in self.read_budgets.items():
            if not isinstance(budget, ReadBudget):
                raise AuditContractError("read_budgets must contain ReadBudget values")
            maximum = DEFAULT_READ_BUDGETS[name]
            if any(getattr(budget, field.name) > getattr(maximum, field.name) for field in fields(ReadBudget)):
                raise AuditContractError("read budgets may only be lowered")

    @classmethod
    def defaults(cls) -> "AuditLimits":
        return cls(DEFAULT_AUDIT_DEADLINE_SECONDS, dict(DEFAULT_READ_BUDGETS))


@dataclass(frozen=True, slots=True)
class AuditTarget:
    region: str
    instance_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.region, str) or _REGION_RE.fullmatch(self.region) is None:
            raise AuditContractError("target region is invalid")
        if not isinstance(self.instance_id, str) or _INSTANCE_ID_RE.fullmatch(self.instance_id) is None:
            raise AuditContractError("target instance id is invalid")


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    acquisition_method: str
    acquired_at: str
    reduction_rule: str
    truncated: bool
    content_sha256: str

    def __post_init__(self) -> None:
        _require_safe_identifier(self.acquisition_method, "acquisition_method")
        _parse_utc(self.acquired_at, "acquired_at")
        _require_safe_identifier(self.reduction_rule, "reduction_rule")
        if not isinstance(self.truncated, bool):
            raise AuditContractError("truncated must be bool")
        _require_digest(self.content_sha256, "content_sha256")


@dataclass(frozen=True, slots=True)
class ReleaseComparison:
    field: str
    expected: str | None
    deployed: str | None
    status: ReleaseComparisonStatus

    def __post_init__(self) -> None:
        _require_safe_identifier(self.field, "release comparison field")
        for name in ("expected", "deployed"):
            value = getattr(self, name)
            if value is not None:
                _require_safe_identifier(value, name)
        try:
            status = ReleaseComparisonStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise AuditContractError("release comparison status is invalid") from exc
        if self.expected is None or self.deployed is None:
            expected_status = ReleaseComparisonStatus.UNKNOWN
        elif self.expected == self.deployed:
            expected_status = ReleaseComparisonStatus.MATCH
        else:
            expected_status = ReleaseComparisonStatus.MISMATCH
        if status is not expected_status:
            raise AuditContractError("release comparison status conflicts with evidence")


@dataclass(frozen=True, slots=True)
class DeadLetterSummary:
    job_id_sha256: str
    coin: str
    stage: str
    attempt: int
    error_class: str
    retry_state: str
    release_identity: str | None

    def __post_init__(self) -> None:
        _require_digest(self.job_id_sha256, "job_id_sha256")
        if not isinstance(self.coin, str) or _COIN_RE.fullmatch(self.coin) is None:
            raise AuditContractError("dead-letter coin is invalid")
        _require_safe_identifier(self.stage, "dead-letter stage")
        _bounded_nonnegative_int(self.attempt, "dead-letter attempt")
        _require_safe_identifier(self.error_class, "dead-letter error class")
        _require_safe_identifier(self.retry_state, "dead-letter retry state")
        if self.release_identity is not None:
            _require_safe_identifier(self.release_identity, "dead-letter release identity")


@dataclass(frozen=True, slots=True)
class UnitEvidence:
    unit: str
    status: AuditStatus
    enabled: ControlState
    active: ControlState
    result: str
    unit_sha256: str | None
    drop_in_sha256: str | None
    timer_next_monotonic_usec: int | None = None
    timer_last_trigger_monotonic_usec: int | None = None
    journal_error_count: int | None = None

    def __post_init__(self) -> None:
        if self.unit not in SYSTEMD_UNIT_ALLOWLIST:
            raise AuditContractError("unit is not allowlisted")
        _require_status(self.status, "unit status")
        for name in ("enabled", "active"):
            try:
                ControlState(getattr(self, name))
            except (TypeError, ValueError) as exc:
                raise AuditContractError(f"unit {name} is invalid") from exc
        _require_safe_identifier(self.result, "unit result")
        for name in ("unit_sha256", "drop_in_sha256"):
            value = getattr(self, name)
            if value is not None:
                _require_digest(value, name)
        for name in ("timer_next_monotonic_usec", "timer_last_trigger_monotonic_usec", "journal_error_count"):
            value = getattr(self, name)
            if value is not None:
                _bounded_nonnegative_int(value, name)


@dataclass(frozen=True, slots=True)
class EffectiveControl:
    runtime_guard: ControlState
    dynamodb_config: ControlState
    unit_env: ControlState
    production_default: ControlState
    effective: ControlState

    def __post_init__(self) -> None:
        states: list[ControlState] = []
        for field in fields(self):
            try:
                states.append(ControlState(getattr(self, field.name)))
            except (TypeError, ValueError) as exc:
                raise AuditContractError(f"{field.name} control state is invalid") from exc
        # UNKNOWN means that this source did not provide a value, not that it
        # asserted an unsafe value.  Mirror hermes.autonomy_enabled(): an
        # explicit runtime guard wins, then admin config, then the unit/env
        # value, and finally the production default.  If every source is
        # unknown, the result remains unknown and callers fail closed.
        higher_priority = states[:-1]
        expected = next(
            (state for state in higher_priority if state is not ControlState.UNKNOWN),
            ControlState.UNKNOWN,
        )
        if ControlState(self.effective) is not expected:
            raise AuditContractError("effective control must preserve precedence and unknown state")


@dataclass(frozen=True, slots=True)
class TableAudit:
    table_type: str
    status: AuditStatus
    requests_used: int
    items_used: int
    bytes_used: int
    truncated: bool
    ttl_status: ControlState = ControlState.UNKNOWN

    def __post_init__(self) -> None:
        if self.table_type not in TABLE_TYPE_ALLOWLIST:
            raise AuditContractError("table type is not allowlisted")
        _require_status(self.status, "table status")
        for name in ("requests_used", "items_used", "bytes_used"):
            _bounded_nonnegative_int(getattr(self, name), name)
        if not isinstance(self.truncated, bool):
            raise AuditContractError("table truncated must be bool")
        try:
            ControlState(self.ttl_status)
        except (TypeError, ValueError) as exc:
            raise AuditContractError("table TTL state is invalid") from exc


@dataclass(frozen=True, slots=True)
class AggregateSummary:
    status: AuditStatus
    count: int
    truncated: bool

    def __post_init__(self) -> None:
        _require_status(self.status, "aggregate status")
        _bounded_nonnegative_int(self.count, "aggregate count")
        if not isinstance(self.truncated, bool):
            raise AuditContractError("aggregate truncated must be bool")


@dataclass(frozen=True, slots=True)
class ControlPlane:
    units: tuple[UnitEvidence, ...]
    effective_control: EffectiveControl
    redacted_config: Mapping[str, str]
    release_comparison: tuple[ReleaseComparison, ...]

    def __post_init__(self) -> None:
        if tuple(unit.unit for unit in self.units) != SYSTEMD_UNIT_ALLOWLIST:
            raise AuditContractError("control-plane units must exactly match the allowlist order")
        if not isinstance(self.effective_control, EffectiveControl):
            raise AuditContractError("effective_control has an invalid type")
        if not isinstance(self.redacted_config, Mapping) or len(self.redacted_config) > MAX_COLLECTION_ITEMS:
            raise AuditContractError("redacted_config is invalid")
        for key, value in self.redacted_config.items():
            _require_safe_identifier(key, "config key")
            _require_safe_identifier(value, "config value")
        if len({item.field for item in self.release_comparison}) != len(self.release_comparison):
            raise AuditContractError("release comparison fields must be unique")
        if any(not isinstance(item, ReleaseComparison) for item in self.release_comparison):
            raise AuditContractError("release comparison entries are invalid")


@dataclass(frozen=True, slots=True)
class DataPlane:
    table_audits: tuple[TableAudit, ...]
    scheduler_summary: AggregateSummary
    cache_summary: AggregateSummary
    cost_summary: AggregateSummary

    def __post_init__(self) -> None:
        if any(not isinstance(item, TableAudit) for item in self.table_audits):
            raise AuditContractError("table audit entries are invalid")
        if {item.table_type for item in self.table_audits} != set(TABLE_TYPE_ALLOWLIST):
            raise AuditContractError("data-plane table audits must cover each allowlisted type once")
        for name in ("scheduler_summary", "cache_summary", "cost_summary"):
            if not isinstance(getattr(self, name), AggregateSummary):
                raise AuditContractError(f"{name} is invalid")


def _primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {item.name: _primitive(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {key: _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class AuditBundle:
    schema_version: str
    audit_id: str
    captured_at: str
    target: AuditTarget
    invoker_identity_hash: str
    limits: AuditLimits
    overall_status: AuditStatus
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]
    control_plane: ControlPlane
    data_plane: DataPlane
    dead_letters: tuple[DeadLetterSummary, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    canonical_payload_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != AUDIT_SCHEMA_VERSION:
            raise AuditContractError("unsupported audit schema version")
        if not isinstance(self.audit_id, str) or _AUDIT_ID_RE.fullmatch(self.audit_id) is None:
            raise AuditContractError("audit id is invalid")
        _parse_utc(self.captured_at, "captured_at")
        if not isinstance(self.target, AuditTarget) or not isinstance(self.limits, AuditLimits):
            raise AuditContractError("audit target or limits are invalid")
        _require_digest(self.invoker_identity_hash, "invoker_identity_hash")
        status = _require_status(self.overall_status, "overall_status")
        for name in ("warnings", "blockers"):
            messages = getattr(self, name)
            if not isinstance(messages, tuple) or len(messages) > MAX_COLLECTION_ITEMS:
                raise AuditContractError(f"{name} must be a bounded tuple")
            for message in messages:
                _require_safe_identifier(message, name)
        if status is AuditStatus.COMPLETE and self.blockers:
            raise AuditContractError("complete audit cannot contain blockers")
        if status is AuditStatus.INTEGRITY_FAILURE and not self.blockers:
            raise AuditContractError("integrity failure must contain a blocker")
        if not isinstance(self.control_plane, ControlPlane) or not isinstance(self.data_plane, DataPlane):
            raise AuditContractError("audit planes are invalid")
        if any(not isinstance(item, DeadLetterSummary) for item in self.dead_letters):
            raise AuditContractError("dead-letter summaries are invalid")
        if any(not isinstance(item, EvidenceRef) for item in self.evidence_refs):
            raise AuditContractError("evidence refs are invalid")
        _require_digest(self.canonical_payload_sha256, "canonical_payload_sha256")
        if secret_like_paths(self.payload_dict()):
            raise AuditContractError("audit bundle contains secret-like material")
        if self.canonical_payload_sha256 != sha256_digest(self.payload_dict()):
            raise AuditContractError("audit bundle digest does not match canonical payload")

    @classmethod
    def build(
        cls,
        *,
        audit_id: str,
        captured_at: str,
        target: AuditTarget,
        invoker_identity_hash: str,
        limits: AuditLimits,
        overall_status: AuditStatus,
        warnings: tuple[str, ...] = (),
        blockers: tuple[str, ...] = (),
        control_plane: ControlPlane,
        data_plane: DataPlane,
        dead_letters: tuple[DeadLetterSummary, ...] = (),
        evidence_refs: tuple[EvidenceRef, ...] = (),
    ) -> "AuditBundle":
        raw = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "audit_id": audit_id,
            "captured_at": captured_at,
            "target": target,
            "invoker_identity_hash": invoker_identity_hash,
            "limits": limits,
            "overall_status": overall_status,
            "warnings": warnings,
            "blockers": blockers,
            "control_plane": control_plane,
            "data_plane": data_plane,
            "dead_letters": dead_letters,
            "evidence_refs": evidence_refs,
        }
        digest = sha256_digest(_primitive(raw))
        return cls(canonical_payload_sha256=digest, **raw)

    def payload_dict(self) -> dict[str, Any]:
        result = _primitive(self)
        del result["canonical_payload_sha256"]
        return result

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


def exit_code_for(status: AuditStatus | str) -> int:
    """Map every auditable terminal status to the fixed CLI exit contract."""
    value = _require_status(status, "status")
    return EXIT_CODES[value.value]
