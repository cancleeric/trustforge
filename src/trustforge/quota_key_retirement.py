"""Fail-closed durable proof that a previous quota key may be retired.

This authority never reads or deletes key material.  It only combines a
persisted low-cardinality waterline, the D2 recovery watermark, durable OPEN
admission control, and trusted time in one strongly consistent transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from trustforge.preview_trusted_clock import PreviewTrustedClock


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


class RetirementDisposition(StrEnum):
    RETIRABLE = "retirable"
    NOT_RETIRABLE = "not_retirable"


@dataclass(frozen=True, slots=True)
class QuotaKeyRetirementDecision:
    disposition: RetirementDisposition
    lifecycle_generation: int | None = None
    previous_quota_key_version: int | None = None


class RetirementClient(Protocol):
    def transact_get_items(self, **kwargs: object) -> object: ...


class QuotaKeyRetirementAuthority:
    """Evaluate retirement from durable metadata without key material."""

    def __init__(
        self,
        *,
        dynamodb_client: RetirementClient,
        table_name: str,
        trusted_clock: PreviewTrustedClock,
    ) -> None:
        if (
            type(table_name) is not str
            or not table_name
            or table_name != table_name.strip()
            or getattr(trusted_clock, "_client", None) is not dynamodb_client
            or getattr(trusted_clock, "_table_name", None) != table_name
        ):
            raise ValueError("retirement authority must share trusted storage")
        self._client = dynamodb_client
        self._table = table_name
        self._clock = trusted_clock

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
                ]
            )
            responses = response["Responses"]
            if type(responses) is not list or len(responses) != 3:
                raise ValueError
            waterline = _decode_waterline(_decode_item(responses[0]))
            recovery = _decode_recovery(_decode_item(responses[1]))
            _require_open(_decode_item(responses[2]))
            if (
                now.earliest <= waterline.retire_not_before
                or now.earliest > waterline.retention_until
                or recovery.version < waterline.required_recovery_version
                or recovery.shard <= waterline.last_old_expiry_shard
            ):
                raise ValueError
            return QuotaKeyRetirementDecision(
                RetirementDisposition.RETIRABLE,
                waterline.lifecycle_generation,
                waterline.previous_quota_key_version,
            )
        except Exception:
            return QuotaKeyRetirementDecision(RetirementDisposition.NOT_RETIRABLE)


@dataclass(frozen=True, slots=True)
class _Waterline:
    lifecycle_generation: int
    previous_quota_key_version: int
    current_quota_key_version: int
    last_old_admission_upper: int
    last_old_expiry_shard: int
    required_recovery_version: int
    retire_not_before: float
    retention_until: float


@dataclass(frozen=True, slots=True)
class _Recovery:
    version: int
    shard: int


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
        or type(result.previous_quota_key_version) is not int
        or type(result.current_quota_key_version) is not int
        or result.lifecycle_generation < 1
        or result.previous_quota_key_version < 1
        or result.current_quota_key_version != result.previous_quota_key_version + 1
        or result.retention_until <= result.retire_not_before
    ):
        raise ValueError
    return result


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


def _require_open(item: dict[str, object]) -> None:
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
