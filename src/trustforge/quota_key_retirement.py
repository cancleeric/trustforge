"""Fail-closed durable proof that a previous quota key may be retired.

This authority never reads or deletes key material.  It only combines a
persisted low-cardinality waterline, the D2 recovery watermark, durable OPEN
admission control, and trusted time in one strongly consistent transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import threading
from typing import Protocol

from trustforge.preview_trusted_clock import PreviewTrustedClock
from trustforge.quota_key_lifecycle import (
    DurableQuotaKeyLifecycleAuthority,
    RetirementCapability,
    _RETIREMENT_TOKEN,
)


WATERLINE_KEY = {
    "pk": {"S": "PAP#1#QUOTA-KEY"},
    "sk": {"S": "RETIREMENT#WATERLINE"},
}
RECOVERY_KEY = {
    "pk": {"S": "PAP#1#RECOVERY"},
    "sk": {"S": "LEASE#WATERMARK"},
}
CONTROL_KEY = {
    "pk": {"S": "PAP#1#CONTROL"},
    "sk": {"S": "ADMISSION#QUARANTINE"},
}
LIFECYCLE_KEY = {
    "pk": {"S": "PAP#1#QUOTA-KEY"},
    "sk": {"S": "LIFECYCLE#CONTROL"},
}
_DECISION_TOKEN = object()
_PROPOSAL_TOKEN = object()


class RetirementDisposition(StrEnum):
    RETIRABLE = "retirable"
    NOT_RETIRABLE = "not_retirable"


@dataclass(frozen=True, slots=True, init=False)
class QuotaKeyRetirementDecision:
    disposition: RetirementDisposition
    lifecycle_generation: int | None = None
    previous_quota_key_version: int | None = None
    _authority: object | None = field(default=None, repr=False)
    _serial: object | None = field(default=None, repr=False)

    @classmethod
    def _mint(
        cls,
        token: object,
        disposition: RetirementDisposition,
        generation: int | None = None,
        version: int | None = None,
        authority: object | None = None,
        serial: object | None = None,
    ) -> QuotaKeyRetirementDecision:
        if token is not _DECISION_TOKEN:
            raise ValueError("invalid retirement decision")
        result = object.__new__(cls)
        object.__setattr__(result, "disposition", disposition)
        object.__setattr__(result, "lifecycle_generation", generation)
        object.__setattr__(result, "previous_quota_key_version", version)
        object.__setattr__(result, "_authority", authority)
        object.__setattr__(result, "_serial", serial)
        return result


class RetirementClient(Protocol):
    def transact_get_items(self, **kwargs: object) -> object: ...

    def transact_write_items(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True, init=False)
class QuotaKeyRetirementWaterline:
    lifecycle_generation: int
    previous_quota_key_version: int
    current_quota_key_version: int
    last_old_admission_upper: int
    last_old_expiry_shard: int
    required_recovery_version: int
    retire_not_before: float
    retention_until: float
    _authority: object = field(repr=False)
    _proof: _WriteProof = field(repr=False)

    @classmethod
    def _mint(
        cls,
        token: object,
        authority: object,
        proof: _WriteProof,
        *,
        last_old_admission_upper: int,
        last_old_expiry_shard: int,
        retire_not_before: float,
        retention_until: float,
    ) -> QuotaKeyRetirementWaterline:
        if token is not _PROPOSAL_TOKEN:
            raise ValueError("invalid retirement proposal")
        result = object.__new__(cls)
        lifecycle = proof.lifecycle
        values = {
            "lifecycle_generation": lifecycle.generation,
            "previous_quota_key_version": lifecycle.previous_version,
            "current_quota_key_version": lifecycle.current_version,
            "last_old_admission_upper": last_old_admission_upper,
            "last_old_expiry_shard": last_old_expiry_shard,
            "required_recovery_version": proof.recovery.version,
            "retire_not_before": retire_not_before,
            "retention_until": retention_until,
            "_authority": authority,
            "_proof": proof,
        }
        for name, value in values.items():
            object.__setattr__(result, name, value)
        return result


class WaterlineWriteDisposition(StrEnum):
    COMMITTED = "committed"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


class QuotaKeyRetirementWaterlineWriter:
    """Create or advance the metadata-only waterline with one exact CAS."""

    def __init__(
        self,
        *,
        dynamodb_client: RetirementClient,
        table_name: str,
        trusted_clock: PreviewTrustedClock,
    ) -> None:
        _require_bound_storage(dynamodb_client, table_name, trusted_clock)
        self._client = dynamodb_client
        self._table = table_name
        self._clock = trusted_clock
        self._nonce = object()

    def propose(
        self,
        *,
        last_old_admission_upper: int,
        last_old_expiry_shard: int,
        retire_not_before: float,
        retention_until: float,
    ) -> QuotaKeyRetirementWaterline:
        """Bind caller facts to one exact durable lifecycle/D2/OPEN snapshot."""

        responses = _strong_read(self._client, self._table)
        proof = _decode_write_proof(responses)
        return QuotaKeyRetirementWaterline._mint(
            _PROPOSAL_TOKEN,
            self._nonce,
            proof,
            last_old_admission_upper=last_old_admission_upper,
            last_old_expiry_shard=last_old_expiry_shard,
            retire_not_before=retire_not_before,
            retention_until=retention_until,
        )

    def write(
        self, proposal: QuotaKeyRetirementWaterline
    ) -> WaterlineWriteDisposition:
        try:
            if self._clock.needs_refresh():
                self._clock.refresh()
            now = self._clock.trusted_interval()
            if (
                type(proposal) is not QuotaKeyRetirementWaterline
                or proposal._authority is not self._nonce
            ):
                return WaterlineWriteDisposition.REJECTED
            desired = _waterline_from_proposal(proposal, version=0)
            if desired.retire_not_before <= now.latest:
                return WaterlineWriteDisposition.REJECTED
            responses = _strong_read(self._client, self._table)
            current = (
                None
                if responses[0].get("Item") is None
                else _decode_waterline(_decode_item(responses[0]))
            )
            proof = _decode_write_proof(responses)
            if proof != proposal._proof:
                return WaterlineWriteDisposition.REJECTED
            if current is not None:
                if not _strict_advance(current, desired):
                    return WaterlineWriteDisposition.REJECTED
                desired = _Waterline(
                    current.waterline_version + 1,
                    *desired.__dict_values_without_version__(),
                )
            item = _encode_waterline(desired)
            put: dict[str, object] = {"TableName": self._table, "Item": item}
            if current is None:
                put["ConditionExpression"] = (
                    "attribute_not_exists(pk) AND attribute_not_exists(sk)"
                )
            else:
                put.update(
                    {
                        "ConditionExpression": "#version=:expected",
                        "ExpressionAttributeNames": {
                            "#version": "waterline_version"
                        },
                        "ExpressionAttributeValues": {
                            ":expected": {"N": str(current.waterline_version)}
                        },
                    }
                )
            try:
                self._client.transact_write_items(
                    TransactItems=[
                        {"Put": put},
                        {"ConditionCheck": _recovery_condition(self._table, proof)},
                        {"ConditionCheck": _control_condition(self._table, proof)},
                        {"ConditionCheck": _lifecycle_condition(self._table, proof)},
                    ]
                )
                return WaterlineWriteDisposition.COMMITTED
            except Exception:
                return (
                    WaterlineWriteDisposition.COMMITTED
                    if self._prove_exact(desired, proof)
                    else WaterlineWriteDisposition.UNRESOLVED
                )
        except Exception:
            return WaterlineWriteDisposition.REJECTED

    def _prove_exact(self, desired: _Waterline, desired_proof: _WriteProof) -> bool:
        try:
            responses = _strong_read(self._client, self._table)
            actual = _decode_waterline(_decode_item(responses[0]))
            return actual == desired and _decode_write_proof(responses) == desired_proof
        except Exception:
            return False


class QuotaKeyRetirementAuthority:
    """Evaluate retirement from durable metadata without key material."""

    def __init__(
        self,
        *,
        dynamodb_client: RetirementClient,
        table_name: str,
        trusted_clock: PreviewTrustedClock,
        lifecycle_authority: DurableQuotaKeyLifecycleAuthority | None = None,
    ) -> None:
        _require_bound_storage(dynamodb_client, table_name, trusted_clock)
        self._client = dynamodb_client
        self._table = table_name
        self._clock = trusted_clock
        self._lifecycle_authority = lifecycle_authority
        self._nonce = object()
        self._consumed: set[object] = set()
        self._consume_lock = threading.Lock()

    def evaluate(self) -> QuotaKeyRetirementDecision:
        """Return RETIRABLE only when every strict durable proof agrees."""

        try:
            if self._clock.needs_refresh():
                self._clock.refresh()
            now = self._clock.trusted_interval()
            response = self._client.transact_get_items(
                TransactItems=[
                    {"Get": {"TableName": self._table, "Key": WATERLINE_KEY}},
                    {"Get": {"TableName": self._table, "Key": RECOVERY_KEY}},
                    {"Get": {"TableName": self._table, "Key": CONTROL_KEY}},
                    {"Get": {"TableName": self._table, "Key": LIFECYCLE_KEY}},
                ]
            )
            responses = response["Responses"]
            if type(responses) is not list or len(responses) != 4:
                raise ValueError
            waterline = _decode_waterline(_decode_item(responses[0]))
            recovery = _decode_recovery(_decode_item(responses[1]))
            _require_open(_decode_item(responses[2]))
            lifecycle = _decode_lifecycle(_decode_item(responses[3]))
            if (
                now.earliest <= waterline.retire_not_before
                or now.earliest > waterline.retention_until
                or recovery.version < waterline.required_recovery_version
                or recovery.shard <= waterline.last_old_expiry_shard
                or lifecycle.generation != waterline.lifecycle_generation
                or lifecycle.current_version != waterline.current_quota_key_version
                or lifecycle.previous_version
                != waterline.previous_quota_key_version
            ):
                raise ValueError
            serial = object()
            return QuotaKeyRetirementDecision._mint(
                _DECISION_TOKEN,
                RetirementDisposition.RETIRABLE,
                waterline.lifecycle_generation,
                waterline.previous_quota_key_version,
                self._nonce,
                serial,
            )
        except Exception:
            return QuotaKeyRetirementDecision._mint(
                _DECISION_TOKEN, RetirementDisposition.NOT_RETIRABLE
            )

    def consume(
        self, decision: QuotaKeyRetirementDecision
    ) -> RetirementCapability:
        """Consume one authority-minted RETIRABLE result exactly once."""

        with self._consume_lock:
            if (
                type(decision) is not QuotaKeyRetirementDecision
                or getattr(decision, "disposition", None)
                is not RetirementDisposition.RETIRABLE
                or getattr(decision, "_authority", None) is not self._nonce
                or getattr(decision, "_serial", None) is None
                or getattr(decision, "_serial", None) in self._consumed
                or self._lifecycle_authority is None
            ):
                raise ValueError("invalid retirement decision")
            self._consumed.add(decision._serial)
        return RetirementCapability._mint(
            _RETIREMENT_TOKEN,
            decision.lifecycle_generation,
            decision.previous_quota_key_version,
            self._lifecycle_authority._nonce,
        )


@dataclass(frozen=True, slots=True)
class _Waterline:
    waterline_version: int
    lifecycle_generation: int
    previous_quota_key_version: int
    current_quota_key_version: int
    last_old_admission_upper: int
    last_old_expiry_shard: int
    required_recovery_version: int
    retire_not_before: float
    retention_until: float

    def __dict_values_without_version__(self) -> tuple[object, ...]:
        return (
            self.lifecycle_generation,
            self.previous_quota_key_version,
            self.current_quota_key_version,
            self.last_old_admission_upper,
            self.last_old_expiry_shard,
            self.required_recovery_version,
            self.retire_not_before,
            self.retention_until,
        )


@dataclass(frozen=True, slots=True)
class _Recovery:
    version: int
    shard: int


@dataclass(frozen=True, slots=True)
class _Control:
    generation: int
    version: int


@dataclass(frozen=True, slots=True)
class _Lifecycle:
    generation: int
    current_version: int
    previous_version: int
    config_fingerprint: str


@dataclass(frozen=True, slots=True)
class _WriteProof:
    recovery: _Recovery
    control: _Control
    lifecycle: _Lifecycle


def _decode_item(response: object) -> dict[str, object]:
    if type(response) is not dict or set(response) != {"Item"}:
        raise ValueError
    item = response["Item"]
    if type(item) is not dict:
        raise ValueError
    return {key: _decode_value(value) for key, value in item.items()}


def _decode_value(value: object) -> object:
    if type(value) is not dict or len(value) != 1:
        raise ValueError
    if "S" in value and type(value["S"]) is str:
        return value["S"]
    if "N" in value and type(value["N"]) is str:
        text = value["N"]
        return float(text) if "." in text else int(text)
    raise ValueError


def _decode_waterline(item: dict[str, object]) -> _Waterline:
    expected = {
        "pk",
        "sk",
        "kind",
        "schema_version",
        "waterline_version",
        "lifecycle_generation",
        "previous_quota_key_version",
        "current_quota_key_version",
        "last_old_admission_upper",
        "last_old_expiry_shard",
        "required_recovery_version",
        "retire_not_before",
        "retention_until",
    }
    if (
        set(item) != expected
        or item["pk"] != "PAP#1#QUOTA-KEY"
        or item["sk"] != "RETIREMENT#WATERLINE"
        or item["kind"] != "quota_key_retirement_waterline"
        or item["schema_version"] != 1
    ):
        raise ValueError
    numeric = [item[name] for name in expected - {"pk", "sk", "kind"}]
    if any(type(value) not in (int, float) or value < 0 for value in numeric):
        raise ValueError
    result = _Waterline(
        item["waterline_version"],
        item["lifecycle_generation"],
        item["previous_quota_key_version"],
        item["current_quota_key_version"],
        item["last_old_admission_upper"],
        item["last_old_expiry_shard"],
        item["required_recovery_version"],
        item["retire_not_before"],
        item["retention_until"],
    )
    if (
        type(result.lifecycle_generation) is not int
        or type(result.waterline_version) is not int
        or type(result.previous_quota_key_version) is not int
        or type(result.current_quota_key_version) is not int
        or result.lifecycle_generation < 1
        or result.waterline_version < 0
        or result.previous_quota_key_version < 1
        or result.current_quota_key_version != result.previous_quota_key_version + 1
        or result.retention_until <= result.retire_not_before
    ):
        raise ValueError
    return result


def _waterline_from_proposal(
    proposal: QuotaKeyRetirementWaterline, *, version: int
) -> _Waterline:
    if type(proposal) is not QuotaKeyRetirementWaterline:
        raise ValueError
    result = _Waterline(
        version,
        proposal.lifecycle_generation,
        proposal.previous_quota_key_version,
        proposal.current_quota_key_version,
        proposal.last_old_admission_upper,
        proposal.last_old_expiry_shard,
        proposal.required_recovery_version,
        proposal.retire_not_before,
        proposal.retention_until,
    )
    # Round-trip the canonical shape to share the reader's strict contract.
    return _decode_waterline(
        {key: _decode_value(value) for key, value in _encode_waterline(result).items()}
    )


def _strict_advance(current: _Waterline, desired: _Waterline) -> bool:
    return (
        desired.lifecycle_generation == current.lifecycle_generation + 1
        and desired.previous_quota_key_version == current.current_quota_key_version
        and desired.current_quota_key_version
        == desired.previous_quota_key_version + 1
        and desired.last_old_admission_upper > current.last_old_admission_upper
        and desired.last_old_expiry_shard > current.last_old_expiry_shard
        and desired.required_recovery_version > current.required_recovery_version
        and desired.retire_not_before > current.retire_not_before
        and desired.retention_until > current.retention_until
    )


def _encode_waterline(waterline: _Waterline) -> dict[str, object]:
    item = {
        "pk": "PAP#1#QUOTA-KEY",
        "sk": "RETIREMENT#WATERLINE",
        "kind": "quota_key_retirement_waterline",
        "schema_version": 1,
        "waterline_version": waterline.waterline_version,
        "lifecycle_generation": waterline.lifecycle_generation,
        "previous_quota_key_version": waterline.previous_quota_key_version,
        "current_quota_key_version": waterline.current_quota_key_version,
        "last_old_admission_upper": waterline.last_old_admission_upper,
        "last_old_expiry_shard": waterline.last_old_expiry_shard,
        "required_recovery_version": waterline.required_recovery_version,
        "retire_not_before": waterline.retire_not_before,
        "retention_until": waterline.retention_until,
    }
    return {
        key: {"S": value} if type(value) is str else {"N": str(value)}
        for key, value in item.items()
    }


def _strong_read(client: RetirementClient, table: str) -> list[dict[str, object]]:
    response = client.transact_get_items(
        TransactItems=[
            {"Get": {"TableName": table, "Key": WATERLINE_KEY}},
            {"Get": {"TableName": table, "Key": RECOVERY_KEY}},
            {"Get": {"TableName": table, "Key": CONTROL_KEY}},
            {"Get": {"TableName": table, "Key": LIFECYCLE_KEY}},
        ]
    )
    responses = response["Responses"]
    if (
        type(responses) is not list
        or len(responses) != 4
        or any(type(item) is not dict for item in responses)
    ):
        raise ValueError
    return responses


def _require_bound_storage(
    client: RetirementClient, table: str, clock: PreviewTrustedClock
) -> None:
    if (
        type(table) is not str
        or not table
        or table != table.strip()
        or getattr(clock, "_client", None) is not client
        or getattr(clock, "_table_name", None) != table
    ):
        raise ValueError("retirement authority must share trusted storage")


def _decode_recovery(item: dict[str, object]) -> _Recovery:
    if (
        set(item) not in (
            {"pk", "sk", "kind", "schema_version", "version", "shard"},
            {"pk", "sk", "kind", "schema_version", "version", "shard", "last_sk"},
        )
        or item.get("pk") != "PAP#1#RECOVERY"
        or item.get("sk") != "LEASE#WATERMARK"
        or item.get("kind") != "preview_recovery_watermark"
        or item.get("schema_version") != 1
        or type(item.get("version")) is not int
        or type(item.get("shard")) is not int
        or item["version"] < 0
        or item["shard"] < 0
    ):
        raise ValueError
    return _Recovery(item["version"], item["shard"])


def _require_open(item: dict[str, object]) -> _Control:
    if (
        set(item)
        != {"pk", "sk", "kind", "schema_version", "state", "generation", "version"}
        or item.get("pk") != "PAP#1#CONTROL"
        or item.get("sk") != "ADMISSION#QUARANTINE"
        or item.get("kind") != "preview_admission_quarantine"
        or item.get("schema_version") != 1
        or item.get("state") != "open"
        or type(item.get("generation")) is not int
        or type(item.get("version")) is not int
    ):
        raise ValueError
    return _Control(item["generation"], item["version"])


def _decode_lifecycle(item: dict[str, object]) -> _Lifecycle:
    if (
        item.get("pk") != "PAP#1#QUOTA-KEY"
        or item.get("sk") != "LIFECYCLE#CONTROL"
        or item.get("kind") != "quota_key_lifecycle_control"
        or item.get("schema_version") != 1
        or item.get("mode") != "overlap"
        or type(item.get("generation")) is not int
        or type(item.get("current_version")) is not int
        or type(item.get("previous_version")) is not int
        or item["generation"] < 1
        or item["previous_version"] < 1
        or item["current_version"] != item["previous_version"] + 1
        or type(item.get("config_fingerprint")) is not str
        or not item["config_fingerprint"]
    ):
        raise ValueError
    return _Lifecycle(
        item["generation"],
        item["current_version"],
        item["previous_version"],
        item["config_fingerprint"],
    )


def _decode_write_proof(responses: list[dict[str, object]]) -> _WriteProof:
    return _WriteProof(
        _decode_recovery(_decode_item(responses[1])),
        _require_open(_decode_item(responses[2])),
        _decode_lifecycle(_decode_item(responses[3])),
    )


def _recovery_condition(table: str, proof: _WriteProof) -> dict[str, object]:
    return {
        "TableName": table,
        "Key": RECOVERY_KEY,
        "ConditionExpression": "#version=:version AND #shard=:shard",
        "ExpressionAttributeNames": {"#version": "version", "#shard": "shard"},
        "ExpressionAttributeValues": {
            ":version": {"N": str(proof.recovery.version)},
            ":shard": {"N": str(proof.recovery.shard)},
        },
    }


def _control_condition(table: str, proof: _WriteProof) -> dict[str, object]:
    return {
        "TableName": table,
        "Key": CONTROL_KEY,
        "ConditionExpression": (
            "#state=:open AND #generation=:generation AND #version=:version"
        ),
        "ExpressionAttributeNames": {
            "#state": "state",
            "#generation": "generation",
            "#version": "version",
        },
        "ExpressionAttributeValues": {
            ":open": {"S": "open"},
            ":generation": {"N": str(proof.control.generation)},
            ":version": {"N": str(proof.control.version)},
        },
    }


def _lifecycle_condition(table: str, proof: _WriteProof) -> dict[str, object]:
    lifecycle = proof.lifecycle
    return {
        "TableName": table,
        "Key": LIFECYCLE_KEY,
        "ConditionExpression": (
            "#mode=:overlap AND #generation=:generation "
            "AND #current=:current AND #previous=:previous "
            "AND #fingerprint=:fingerprint"
        ),
        "ExpressionAttributeNames": {
            "#mode": "mode",
            "#generation": "generation",
            "#current": "current_version",
            "#previous": "previous_version",
            "#fingerprint": "config_fingerprint",
        },
        "ExpressionAttributeValues": {
            ":overlap": {"S": "overlap"},
            ":generation": {"N": str(lifecycle.generation)},
            ":current": {"N": str(lifecycle.current_version)},
            ":previous": {"N": str(lifecycle.previous_version)},
            ":fingerprint": {"S": lifecycle.config_fingerprint},
        },
    }
