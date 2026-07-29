from __future__ import annotations

import ast
import json
import random
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trustforge.asset_intrinsic import IntrinsicDimensionName
from trustforge.control_plane_pit import (
    ConsensusKind,
    ControlPlane,
    ControlPlaneRepository,
    EvidenceKind,
    PlaneObservation,
    PlaneStatus,
    SourceWithdrawal,
)

CUTOFF = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
DIGEST = "sha256:" + "a" * 64


def observation(
    observation_id: str,
    plane: ControlPlane,
    value: float,
    *,
    source_id: str,
    family: str,
    entity: str,
    revision: int = 1,
    observed_at: datetime = CUTOFF - timedelta(days=1),
    fetched_at: datetime | None = None,
    valid_until: datetime | None = None,
    host: str | None = None,
    digest: str = DIGEST,
) -> PlaneObservation:
    kind = {
        ControlPlane.VALIDATOR: EvidenceKind.ENTITY_MEASUREMENT,
        ControlPlane.MINER_POOL: EvidenceKind.ENTITY_MEASUREMENT,
        ControlPlane.NODE_CLIENT: EvidenceKind.CLIENT_TELEMETRY,
        ControlPlane.GOVERNANCE: EvidenceKind.GOVERNANCE_RECORD,
    }[plane]
    return PlaneObservation(
        observation_id=observation_id,
        plane=plane,
        source_id=source_id,
        source_family=family,
        source_url=f"https://{host or family}.example/evidence/{observation_id}",
        control_entity_id=entity,
        revision=revision,
        evidence_kind=kind,
        evidence_digest=digest,
        value=value,
        observed_at=observed_at,
        fetched_at=observed_at if fetched_at is None else fetched_at,
        valid_until=valid_until,
    )


def withdrawal(
    target: PlaneObservation,
    *,
    effective_at: datetime,
    fetched_at: datetime | None = None,
) -> SourceWithdrawal:
    return SourceWithdrawal(
        withdrawal_id=f"withdraw-{target.observation_id}",
        observation_id=target.observation_id,
        source_id=target.source_id,
        effective_at=effective_at,
        fetched_at=effective_at if fetched_at is None else fetched_at,
        reason_code="upstream_retracted",
    )


def complete_observations() -> tuple[PlaneObservation, ...]:
    return (
        observation(
            "validator-a",
            ControlPlane.VALIDATOR,
            0.8,
            source_id="validator-registry",
            family="validator-registry",
            entity="validator-entities",
        ),
        observation(
            "miner-a",
            ControlPlane.MINER_POOL,
            0.7,
            source_id="pool-measurement",
            family="pool-measurement",
            entity="pool-entities",
        ),
        observation(
            "node-a",
            ControlPlane.NODE_CLIENT,
            0.6,
            source_id="node-telemetry",
            family="node-telemetry",
            entity="client-entities",
        ),
        observation(
            "gov-a",
            ControlPlane.GOVERNANCE,
            0.75,
            source_id="governance-records-a",
            family="governance-records-a",
            entity="governance-body-a",
        ),
        observation(
            "gov-b",
            ControlPlane.GOVERNANCE,
            0.70,
            source_id="governance-records-b",
            family="governance-records-b",
            entity="governance-body-b",
        ),
    )


def test_four_planes_are_typed_attributable_and_independently_replayable() -> None:
    replay = ControlPlaneRepository(complete_observations()).replay(
        pit_cutoff=CUTOFF, consensus=ConsensusKind.HYBRID
    )
    assert tuple(result.plane for result in replay.planes) == tuple(ControlPlane)
    for result in replay.planes:
        assert result.status is PlaneStatus.KNOWN
        assert result.contribution_ids
        assert result.source_families
        assert result.control_entities
    assert tuple(result.name for result in replay.dimensions) == (
        IntrinsicDimensionName.CONTROL_DISPERSION,
        IntrinsicDimensionName.GOVERNANCE_CAPTURE_RESISTANCE,
    )
    assert all(result.status is PlaneStatus.KNOWN for result in replay.dimensions)


@pytest.mark.parametrize(
    ("plane", "wrong_kind"),
    [
        (ControlPlane.VALIDATOR, EvidenceKind.GOVERNANCE_RECORD),
        (ControlPlane.MINER_POOL, EvidenceKind.CLIENT_TELEMETRY),
        (ControlPlane.NODE_CLIENT, EvidenceKind.ENTITY_MEASUREMENT),
        (ControlPlane.GOVERNANCE, EvidenceKind.ENTITY_MEASUREMENT),
    ],
)
def test_provenance_prose_or_wrong_typed_evidence_cannot_prove_plane(
    plane: ControlPlane, wrong_kind: EvidenceKind
) -> None:
    base = observation(
        "typed",
        plane,
        0.5,
        source_id="source",
        family="family",
        entity="entity",
    )
    with pytest.raises(ValueError, match="cannot prove"):
        replace(base, evidence_kind=wrong_kind)


def test_plain_string_plane_and_evidence_kind_are_rejected() -> None:
    base = observation(
        "typed",
        ControlPlane.NODE_CLIENT,
        0.5,
        source_id="source",
        family="family",
        entity="entity",
    )
    with pytest.raises(ValueError, match="ControlPlane"):
        replace(base, plane="node_client")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="EvidenceKind"):
        replace(base, evidence_kind="client_telemetry")  # type: ignore[arg-type]


def test_multiple_hosts_in_one_family_cannot_satisfy_independence() -> None:
    records = (
        observation(
            "miner",
            ControlPlane.MINER_POOL,
            0.7,
            source_id="same-provider-miner",
            family="same-provider",
            entity="pool",
            host="host-a",
        ),
        observation(
            "node",
            ControlPlane.NODE_CLIENT,
            0.7,
            source_id="same-provider-node",
            family="same-provider",
            entity="client",
            host="host-b",
        ),
    )
    result = ControlPlaneRepository(records).replay(
        pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_WORK
    )
    control = result.dimension(IntrinsicDimensionName.CONTROL_DISPERSION)
    assert control.status is PlaneStatus.UNKNOWN
    assert control.reason_code == "insufficient_independent_source_families"
    assert control.value is None


def test_family_alias_count_is_byte_and_value_invariant() -> None:
    baseline_records = (
        observation(
            "node-family-a",
            ControlPlane.NODE_CLIENT,
            0.6,
            source_id="node-a",
            family="family-a",
            entity="client-a",
        ),
        observation(
            "node-family-b",
            ControlPlane.NODE_CLIENT,
            0.7,
            source_id="node-b",
            family="family-b",
            entity="client-b",
        ),
    )
    baseline = ControlPlaneRepository(baseline_records).replay(
        pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_WORK
    )
    aliases = tuple(
        observation(
            f"node-alias-{index}",
            ControlPlane.NODE_CLIENT,
            0.6,
            source_id=f"node-a-alias-{index}",
            family="family-a",
            entity="client-a",
            host=f"alias-{index}",
            digest="sha256:" + f"{index + 1:x}" * 64,
        )
        for index in range(8)
    )
    expanded = ControlPlaneRepository(baseline_records + aliases).replay(
        pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_WORK
    )
    assert expanded == baseline

    def serialize(value) -> bytes:
        return json.dumps(
            asdict(value),
            default=lambda item: item.isoformat(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    assert serialize(expanded) == serialize(baseline)


def test_conflicting_aliases_in_one_family_fail_closed_instead_of_voting() -> None:
    records = (
        observation(
            "node-low",
            ControlPlane.NODE_CLIENT,
            0.1,
            source_id="node-low",
            family="same-family",
            entity="client",
        ),
        observation(
            "node-high",
            ControlPlane.NODE_CLIENT,
            0.9,
            source_id="node-high",
            family="same-family",
            entity="client",
        ),
    )
    node = (
        ControlPlaneRepository(records)
        .replay(pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_WORK)
        .plane(ControlPlane.NODE_CLIENT)
    )
    assert node.status is PlaneStatus.CONFLICT
    assert node.reason_code == "conflicting_family_aliases"
    assert node.value is None


@pytest.mark.parametrize("missing", list(ControlPlane))
def test_missing_plane_remains_unknown(missing: ControlPlane) -> None:
    records = tuple(
        item for item in complete_observations() if item.plane is not missing
    )
    replay = ControlPlaneRepository(records).replay(
        pit_cutoff=CUTOFF, consensus=ConsensusKind.HYBRID
    )
    assert replay.plane(missing).status is PlaneStatus.UNKNOWN
    affected = (
        IntrinsicDimensionName.GOVERNANCE_CAPTURE_RESISTANCE
        if missing is ControlPlane.GOVERNANCE
        else IntrinsicDimensionName.CONTROL_DISPERSION
    )
    assert replay.dimension(affected).status is PlaneStatus.UNKNOWN


def test_conflicting_plane_is_conflict_and_never_averaged_into_dimension() -> None:
    records = complete_observations() + (
        observation(
            "node-conflict",
            ControlPlane.NODE_CLIENT,
            0.1,
            source_id="independent-node",
            family="independent-node",
            entity="other-client",
        ),
    )
    replay = ControlPlaneRepository(records).replay(
        pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_STAKE
    )
    assert replay.plane(ControlPlane.NODE_CLIENT).status is PlaneStatus.CONFLICT
    control = replay.dimension(IntrinsicDimensionName.CONTROL_DISPERSION)
    assert control.status is PlaneStatus.CONFLICT
    assert control.value is None


def test_stale_and_expired_plane_inputs_remain_unknown() -> None:
    stale = observation(
        "stale-node",
        ControlPlane.NODE_CLIENT,
        0.8,
        source_id="node",
        family="node",
        entity="client",
        observed_at=CUTOFF - timedelta(days=31),
    )
    expired = observation(
        "expired-validator",
        ControlPlane.VALIDATOR,
        0.8,
        source_id="validator",
        family="validator",
        entity="validator",
        valid_until=CUTOFF,
    )
    replay = ControlPlaneRepository((stale, expired)).replay(
        pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_STAKE
    )
    assert replay.plane(ControlPlane.NODE_CLIENT).status is PlaneStatus.UNKNOWN
    assert replay.plane(ControlPlane.NODE_CLIENT).reason_code == "stale"
    assert replay.plane(ControlPlane.VALIDATOR).status is PlaneStatus.UNKNOWN


def test_freshness_boundary_before_at_after_is_deterministic() -> None:
    node = observation(
        "freshness-node",
        ControlPlane.NODE_CLIENT,
        0.8,
        source_id="node",
        family="node",
        entity="client",
        observed_at=CUTOFF - timedelta(days=30),
    )
    repository = ControlPlaneRepository((node,))
    before = repository.replay(
        pit_cutoff=CUTOFF - timedelta(microseconds=1),
        consensus=ConsensusKind.PROOF_OF_WORK,
    )
    exact = repository.replay(pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_WORK)
    after = repository.replay(
        pit_cutoff=CUTOFF + timedelta(microseconds=1),
        consensus=ConsensusKind.PROOF_OF_WORK,
    )
    assert before.plane(ControlPlane.NODE_CLIENT).status is PlaneStatus.KNOWN
    assert exact.plane(ControlPlane.NODE_CLIENT).status is PlaneStatus.KNOWN
    assert after.plane(ControlPlane.NODE_CLIENT).status is PlaneStatus.UNKNOWN
    assert after.plane(ControlPlane.NODE_CLIENT).reason_code == "stale"


def test_source_withdrawal_before_at_after_cutoff_is_canonical() -> None:
    node = observation(
        "node-withdraw",
        ControlPlane.NODE_CLIENT,
        0.8,
        source_id="node-source",
        family="node-family",
        entity="client",
    )
    at = withdrawal(node, effective_at=CUTOFF)
    repository = ControlPlaneRepository((node,), (at,))
    before = repository.replay(
        pit_cutoff=CUTOFF - timedelta(microseconds=1),
        consensus=ConsensusKind.PROOF_OF_WORK,
    )
    exact = repository.replay(pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_WORK)
    after = repository.replay(
        pit_cutoff=CUTOFF + timedelta(microseconds=1),
        consensus=ConsensusKind.PROOF_OF_WORK,
    )
    assert before.plane(ControlPlane.NODE_CLIENT).status is PlaneStatus.KNOWN
    assert exact.plane(ControlPlane.NODE_CLIENT).status is PlaneStatus.UNKNOWN
    assert exact.plane(ControlPlane.NODE_CLIENT).reason_code == "source_withdrawn"
    assert after.plane(ControlPlane.NODE_CLIENT) == exact.plane(
        ControlPlane.NODE_CLIENT
    )


def test_withdrawal_known_after_cutoff_does_not_rewrite_prior_pit() -> None:
    node = observation(
        "node-late-withdraw",
        ControlPlane.NODE_CLIENT,
        0.8,
        source_id="node-source",
        family="node-family",
        entity="client",
    )
    late = withdrawal(
        node,
        effective_at=CUTOFF - timedelta(hours=1),
        fetched_at=CUTOFF + timedelta(seconds=1),
    )
    repository = ControlPlaneRepository((node,), (late,))
    at = repository.replay(pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_WORK)
    after = repository.replay(
        pit_cutoff=CUTOFF + timedelta(seconds=1),
        consensus=ConsensusKind.PROOF_OF_WORK,
    )
    assert at.plane(ControlPlane.NODE_CLIENT).status is PlaneStatus.KNOWN
    assert after.plane(ControlPlane.NODE_CLIENT).status is PlaneStatus.UNKNOWN


def test_withdrawal_cannot_cross_source_boundary() -> None:
    node = observation(
        "node",
        ControlPlane.NODE_CLIENT,
        0.8,
        source_id="owner",
        family="node",
        entity="client",
    )
    forged = replace(
        withdrawal(node, effective_at=CUTOFF),
        source_id="different-source",
    )
    with pytest.raises(ValueError, match="does not bind"):
        ControlPlaneRepository((node,), (forged,))


def test_latest_source_revision_is_single_weight_and_withdrawal_does_not_fallback() -> (
    None
):
    old = observation(
        "node-r1",
        ControlPlane.NODE_CLIENT,
        0.1,
        source_id="node-source",
        family="node-family",
        entity="client",
        revision=1,
        observed_at=CUTOFF - timedelta(days=2),
    )
    latest = observation(
        "node-r2",
        ControlPlane.NODE_CLIENT,
        0.9,
        source_id="node-source",
        family="node-family",
        entity="client",
        revision=2,
    )
    repository = ControlPlaneRepository((latest, old))
    replay = repository.replay(pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_WORK)
    node = replay.plane(ControlPlane.NODE_CLIENT)
    assert node.value == 0.9
    assert node.contribution_ids == ("family:node-family",)

    withdrawn = ControlPlaneRepository(
        (old, latest), (withdrawal(latest, effective_at=CUTOFF),)
    ).replay(pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_WORK)
    node_after = withdrawn.plane(ControlPlane.NODE_CLIENT)
    assert node_after.status is PlaneStatus.UNKNOWN
    assert node_after.reason_code == "source_withdrawn"
    assert node_after.contribution_ids == ()


def test_latest_expiring_revision_never_falls_back_before_at_after_boundary() -> None:
    old = observation(
        "node-old",
        ControlPlane.NODE_CLIENT,
        0.1,
        source_id="node-source",
        family="node-family",
        entity="client",
        revision=1,
        observed_at=CUTOFF - timedelta(days=2),
    )
    latest = observation(
        "node-latest",
        ControlPlane.NODE_CLIENT,
        0.9,
        source_id="node-source",
        family="node-family",
        entity="client",
        revision=2,
        valid_until=CUTOFF,
    )
    repository = ControlPlaneRepository((old, latest))
    before = repository.replay(
        pit_cutoff=CUTOFF - timedelta(microseconds=1),
        consensus=ConsensusKind.PROOF_OF_WORK,
    )
    exact = repository.replay(pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_WORK)
    after = repository.replay(
        pit_cutoff=CUTOFF + timedelta(microseconds=1),
        consensus=ConsensusKind.PROOF_OF_WORK,
    )
    assert before.plane(ControlPlane.NODE_CLIENT).value == 0.9
    for replay in (exact, after):
        node = replay.plane(ControlPlane.NODE_CLIENT)
        assert node.status is PlaneStatus.UNKNOWN
        assert node.value is None
        assert node.reason_code == "stale"


def test_latest_stale_revision_never_falls_back_to_fresh_old_revision() -> None:
    old = observation(
        "node-old-fresh",
        ControlPlane.NODE_CLIENT,
        0.1,
        source_id="node-source",
        family="node-family",
        entity="client",
        revision=1,
        observed_at=CUTOFF - timedelta(days=1),
    )
    latest = observation(
        "node-latest-stale",
        ControlPlane.NODE_CLIENT,
        0.9,
        source_id="node-source",
        family="node-family",
        entity="client",
        revision=2,
        observed_at=CUTOFF - timedelta(days=31),
    )
    node = (
        ControlPlaneRepository((old, latest))
        .replay(pit_cutoff=CUTOFF, consensus=ConsensusKind.PROOF_OF_WORK)
        .plane(ControlPlane.NODE_CLIENT)
    )
    assert node.status is PlaneStatus.UNKNOWN
    assert node.reason_code == "stale"
    assert node.value is None


def test_replay_is_order_deterministic_and_symbol_blind() -> None:
    records = list(complete_observations())
    baseline = ControlPlaneRepository(records).replay(
        pit_cutoff=CUTOFF, consensus=ConsensusKind.HYBRID
    )
    for seed in range(10):
        shuffled = records[:]
        random.Random(seed).shuffle(shuffled)
        assert (
            ControlPlaneRepository(shuffled).replay(
                pit_cutoff=CUTOFF, consensus=ConsensusKind.HYBRID
            )
            == baseline
        )
    source = Path("src/trustforge/control_plane_pit.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = {
        node.value.casefold()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not {"btc", "eth", "bnb", "bitcoin", "ethereum"} & literals
    assert "asset_id" not in source


def test_signed_promotion_gate_block_is_unchanged() -> None:
    receipt = json.loads(
        Path("data/intrinsic_promotion/receipt-current.json").read_text(
            encoding="utf-8"
        )
    )
    before = Path("data/intrinsic_promotion/receipt-current.json").read_bytes()
    replay = ControlPlaneRepository(complete_observations()).replay(
        pit_cutoff=CUTOFF, consensus=ConsensusKind.HYBRID
    )
    assert all(result.status is PlaneStatus.KNOWN for result in replay.dimensions)
    assert receipt["receipt"]["decision"] == "block"
    assert Path("data/intrinsic_promotion/receipt-current.json").read_bytes() == before
