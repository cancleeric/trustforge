from __future__ import annotations

from email.utils import formatdate

from trustforge.preview_trusted_clock import PreviewTrustedClock
from trustforge.quota_key_retirement import (
    QuotaKeyRetirementAuthority,
    RetirementDisposition,
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
            return {"Responses": [{}, {"Item": _ddb(recovery)}, {"Item": _ddb(control)}]}
        return {
            "Responses": [
                {"Item": _ddb(waterline)},
                {"Item": _ddb(recovery)},
                {"Item": _ddb(control)},
            ]
        }


def _authority(client: Client) -> QuotaKeyRetirementAuthority:
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
    )


def test_strong_three_record_proof_can_mark_previous_version_retirable() -> None:
    client = Client()
    decision = _authority(client).evaluate()

    assert decision.disposition is RetirementDisposition.RETIRABLE
    assert decision.lifecycle_generation == 2
    assert decision.previous_quota_key_version == 1
    request = client.calls[0]
    assert len(request["TransactItems"]) == 3
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
