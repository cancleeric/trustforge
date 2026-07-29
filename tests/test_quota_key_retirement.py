from __future__ import annotations

from email.utils import formatdate

import pytest

from trustforge.preview_trusted_clock import PreviewTrustedClock
from trustforge.quota_key_retirement import (
    QuotaKeyRetirementAuthority,
    QuotaKeyRetirementWaterline,
    QuotaKeyRetirementWaterlineWriter,
    RetirementDisposition,
    WaterlineWriteDisposition,
)


def _ddb(item: dict[str, object]) -> dict[str, object]:
    return {
        key: {"S": value} if type(value) is str else {"N": str(value)}
        for key, value in item.items()
    }


class Client:
    def __init__(self, *, now: int = 200, mutate: str | None = None) -> None:
        self.now = now
        self.mutate = mutate
        self.calls: list[object] = []

    def describe_table(self, **kwargs: object) -> dict[str, object]:
        return {
            "ResponseMetadata": {
                "HTTPHeaders": {"date": formatdate(self.now, usegmt=True)}
            }
        }

    def transact_get_items(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        waterline = {
            "pk": "PAP#1#QUOTA-KEY",
            "sk": "RETIREMENT#WATERLINE",
            "kind": "quota_key_retirement_waterline",
            "schema_version": 1,
            "waterline_version": 0,
            "lifecycle_generation": 2,
            "previous_quota_key_version": 1,
            "current_quota_key_version": 2,
            "last_old_admission_upper": 120,
            "last_old_expiry_shard": 150,
            "required_recovery_version": 4,
            "retire_not_before": 190,
            "retention_until": 300,
        }
        recovery = {
            "pk": "PAP#1#RECOVERY",
            "sk": "LEASE#WATERMARK",
            "kind": "preview_recovery_watermark",
            "schema_version": 1,
            "version": 4,
            "shard": 151,
        }
        control = {
            "pk": "PAP#1#CONTROL",
            "sk": "ADMISSION#QUARANTINE",
            "kind": "preview_admission_quarantine",
            "schema_version": 1,
            "state": "open",
            "generation": 1,
            "version": 1,
        }
        lifecycle = {
            "pk": "PAP#1#QUOTA-KEY",
            "sk": "LIFECYCLE#CONTROL",
            "kind": "quota_key_lifecycle_control",
            "schema_version": 1,
            "generation": 2,
            "mode": "overlap",
            "current_version": 2,
            "previous_version": 1,
            "config_fingerprint": "exact-transition",
        }
        if self.mutate == "equal_time":
            waterline["retire_not_before"] = 200
        elif self.mutate == "equal_shard":
            recovery["shard"] = 150
        elif self.mutate == "old_recovery":
            recovery["version"] = 3
        elif self.mutate == "closed":
            control["state"] = "quarantined"
        elif self.mutate == "secret":
            waterline["key_material"] = "must-not-be-stored"
        elif self.mutate == "missing":
            return {
                "Responses": [
                    {},
                    {"Item": _ddb(recovery)},
                    {"Item": _ddb(control)},
                    {"Item": _ddb(lifecycle)},
                ]
            }
        return {
            "Responses": [
                {"Item": _ddb(waterline)},
                {"Item": _ddb(recovery)},
                {"Item": _ddb(control)},
                {"Item": _ddb(lifecycle)},
            ]
        }

    def transact_write_items(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {}


def _authority(
    client: Client, lifecycle_authority: object | None = None
) -> QuotaKeyRetirementAuthority:
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="table",
        monotonic_clock=lambda: 1.0,
        wall_clock=lambda: float(client.now),
    )
    return QuotaKeyRetirementAuthority(
        dynamodb_client=client,
        table_name="table",
        trusted_clock=clock,
        lifecycle_authority=lifecycle_authority,
    )


def test_strong_three_record_proof_can_mark_previous_version_retirable() -> None:
    client = Client()
    decision = _authority(client).evaluate()

    assert decision.disposition is RetirementDisposition.RETIRABLE
    assert decision.lifecycle_generation == 2
    assert decision.previous_quota_key_version == 1
    request = client.calls[0]
    assert len(request["TransactItems"]) == 4
    assert "Delete" not in repr(request)


def test_every_boundary_or_malformed_proof_fails_closed() -> None:
    for mutation in (
        "equal_time",
        "equal_shard",
        "old_recovery",
        "closed",
        "secret",
        "missing",
    ):
        decision = _authority(Client(mutate=mutation)).evaluate()
        assert decision.disposition is RetirementDisposition.NOT_RETIRABLE
        assert decision.previous_quota_key_version is None


def test_repr_contains_no_secret_material() -> None:
    authority = _authority(Client())
    assert "key_material" not in repr(authority)
    assert "secret" not in repr(authority)


def test_clock_and_authority_must_share_exact_storage() -> None:
    first = Client()
    second = Client()
    clock = PreviewTrustedClock(
        dynamodb_client=first,
        table_name="table",
        monotonic_clock=lambda: 1.0,
        wall_clock=lambda: 200.0,
    )
    try:
        QuotaKeyRetirementAuthority(
            dynamodb_client=second,
            table_name="table",
            trusted_clock=clock,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unbound retirement authority")


def _proposal(
    writer: QuotaKeyRetirementWaterlineWriter, **changes: object
) -> QuotaKeyRetirementWaterline:
    values = {
        "last_old_admission_upper": 120,
        "last_old_expiry_shard": 150,
        "retire_not_before": 290,
        "retention_until": 400,
    }
    values.update(changes)
    return writer.propose(**values)


class StatefulClient(Client):
    def __init__(self, *, response_loss: str | None = None) -> None:
        super().__init__()
        self.response_loss = response_loss
        self.stored: dict[str, object] | None = None
        self.read_count = 0
        self.put_count = 0
        self.lifecycle_generation = 2

    def transact_get_items(self, **kwargs: object) -> dict[str, object]:
        response = super().transact_get_items(**kwargs)
        self.read_count += 1
        response["Responses"][0] = (
            {} if self.stored is None else {"Item": self.stored}
        )
        lifecycle = response["Responses"][3]["Item"]
        lifecycle["generation"] = {"N": str(self.lifecycle_generation)}
        lifecycle["current_version"] = {"N": str(self.lifecycle_generation)}
        lifecycle["previous_version"] = {
            "N": str(self.lifecycle_generation - 1)
        }
        return response

    def transact_write_items(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        self.put_count += 1
        if self.response_loss == "uncommitted":
            raise RuntimeError("lost")
        self.stored = kwargs["TransactItems"][0]["Put"]["Item"]
        if self.response_loss == "committed":
            raise RuntimeError("lost")
        return {}


def _writer(client: StatefulClient) -> QuotaKeyRetirementWaterlineWriter:
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="table",
        monotonic_clock=lambda: 1.0,
        wall_clock=lambda: 200.0,
    )
    return QuotaKeyRetirementWaterlineWriter(
        dynamodb_client=client,
        table_name="table",
        trusted_clock=clock,
    )


def test_writer_creates_metadata_once_without_secret_or_digest() -> None:
    client = StatefulClient()

    writer = _writer(client)
    assert writer.write(_proposal(writer)) is WaterlineWriteDisposition.COMMITTED
    writes = [call for call in client.calls if "TransactItems" in call and "Put" in call["TransactItems"][0]]
    assert len(writes) == 1
    assert writes[0]["TransactItems"][0]["Put"]["ConditionExpression"].startswith(
        "attribute_not_exists"
    )
    assert len(writes[0]["TransactItems"]) == 4
    checks = writes[0]["TransactItems"][1:]
    assert all(set(check) == {"ConditionCheck"} for check in checks)
    assert "config_fingerprint" in repr(checks)
    assert "state" in repr(checks)
    assert "shard" in repr(checks)
    assert "key_material" not in repr(writes[0])
    assert "digest" not in repr(writes[0])


def test_response_loss_accepts_only_exact_committed_strong_proof() -> None:
    committed = StatefulClient(response_loss="committed")
    committed_writer = _writer(committed)
    proposal = _proposal(committed_writer)
    assert committed_writer.write(proposal) is WaterlineWriteDisposition.COMMITTED
    assert (committed.put_count, committed.read_count) == (1, 3)

    uncommitted = StatefulClient(response_loss="uncommitted")
    uncommitted_writer = _writer(uncommitted)
    assert (
        uncommitted_writer.write(_proposal(uncommitted_writer))
        is WaterlineWriteDisposition.UNRESOLVED
    )
    assert (uncommitted.put_count, uncommitted.read_count) == (1, 3)


def test_proposal_and_retirable_result_are_nominal_and_consumed_once() -> None:
    client = StatefulClient()
    first = _writer(client)
    proposal = _proposal(first)
    assert (
        _writer(client).write(proposal) is WaterlineWriteDisposition.REJECTED
    )
    forged = QuotaKeyRetirementWaterline()
    assert first.write(forged) is WaterlineWriteDisposition.REJECTED

    class Lifecycle:
        _nonce = object()

    lifecycle = Lifecycle()
    authority = _authority(Client(), lifecycle)
    decision = authority.evaluate()
    capability = authority.consume(decision)
    assert capability.lifecycle_generation == 2
    with pytest.raises(ValueError):
        authority.consume(decision)
    with pytest.raises(ValueError):
        authority.consume(type(decision)())


def test_writer_rejects_trusted_time_equality_and_straddle_without_write() -> None:
    for boundary in (200, 200.5, 201):
        client = StatefulClient()
        writer = _writer(client)
        assert (
            writer.write(_proposal(writer, retire_not_before=boundary))
            is WaterlineWriteDisposition.REJECTED
        )
        assert client.put_count == 0


def _stored_waterline(*, recovery_version: int = 4) -> dict[str, object]:
    return _ddb(
        {
            "pk": "PAP#1#QUOTA-KEY",
            "sk": "RETIREMENT#WATERLINE",
            "kind": "quota_key_retirement_waterline",
            "schema_version": 1,
            "waterline_version": 7,
            "lifecycle_generation": 2,
            "previous_quota_key_version": 1,
            "current_quota_key_version": 2,
            "last_old_admission_upper": 120,
            "last_old_expiry_shard": 150,
            "required_recovery_version": recovery_version,
            "retire_not_before": 190,
            "retention_until": 300,
        }
    )


def _advance(
    writer: QuotaKeyRetirementWaterlineWriter, **changes: object
) -> QuotaKeyRetirementWaterline:
    values = {
        "last_old_admission_upper": 121,
        "last_old_expiry_shard": 151,
        "retire_not_before": 291,
        "retention_until": 401,
    }
    values.update(changes)
    return _proposal(writer, **values)


def test_advance_uses_exact_version_cas_and_rejects_equality() -> None:
    client = StatefulClient()
    client.stored = _stored_waterline(recovery_version=3)
    client.lifecycle_generation = 3
    writer = _writer(client)
    assert writer.write(_advance(writer)) is WaterlineWriteDisposition.COMMITTED
    assert client.put_count == 1
    put = client.calls[-1]["TransactItems"][0]["Put"]
    assert put["ExpressionAttributeValues"][":expected"] == {"N": "7"}

    equality_values = {
        "last_old_admission_upper": 120,
        "last_old_expiry_shard": 150,
        "retire_not_before": 290,
        "retention_until": 400,
    }
    for field, equality in equality_values.items():
        hostile = StatefulClient()
        hostile.stored = _stored_waterline()
        hostile.lifecycle_generation = 3
        writer = _writer(hostile)
        assert (
            writer.write(_advance(writer, **{field: equality}))
            is WaterlineWriteDisposition.REJECTED
        )
        assert hostile.put_count == 0
