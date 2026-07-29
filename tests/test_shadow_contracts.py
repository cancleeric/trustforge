from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from trustforge.agent.shadow_contracts import (
    CONTRACT_VERSION,
    ShadowBlocker,
    ShadowAggregate,
    ShadowContractError,
    ShadowDecision,
    ShadowDecisionAction,
    ShadowInput,
    ShadowObservation,
    ShadowReleaseIdentity,
    canonical_json,
    evaluate_shadow,
    input_digest,
    load_policy,
    observation_digest,
    policy_digest,
    policy_digest_value,
    to_dict,
)


def _identity():
    policy = load_policy()
    return ShadowReleaseIdentity(
        active_release="release:legacy@1.2.3",
        candidate_release="release:kernel@2.0.0-rc1",
        active_artifact_digest="sha256:" + "a" * 64,
        candidate_artifact_digest="sha256:" + "b" * 64,
        policy_digest=policy_digest(policy),
        contract_version=CONTRACT_VERSION,
    )


def _observations():
    identity = _identity()
    base = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)
    result = []
    for index in range(30):
        canonical_input = ShadowInput(
            request_id=f"request-{index}",
            coin=("BTC", "ETH", "SOL")[index % 3],
            question_type=("analysis", "hypothesis")[index % 2],
            pit_epoch=base.timestamp() + index,
            query=f"outlook-{index}",
        )
        result.append(
            ShadowObservation(
                release_identity=identity,
                canonical_input=canonical_input,
                input_digest=input_digest(to_dict(canonical_input)),
                observed_at=(base + timedelta(minutes=index)).isoformat(),
                status="success",
                parity_passed=True,
                confidence_delta=0.01,
                trust_delta=0.01,
                supporting_jaccard=0.9,
                elapsed_ms=100,
                provider_calls=0,
                cost_usd=0,
                claim_ids=(f"claim-{index}",),
            )
        )
    return result


def test_checked_in_policy_has_fixed_p0_values():
    policy = load_policy()
    assert (
        policy.default_enabled,
        policy.minimum_observations,
        policy.window_hours,
        policy.minimum_coins,
        policy.minimum_question_types,
        policy.minimum_per_cell,
        policy.confidence_delta_max,
        policy.supporting_jaccard_min,
        policy.parity_rate_min,
        policy.terminal_failure_streak,
        policy.latency_p95_ms_max,
        policy.latency_each_ms_max,
        policy.provider_calls_max,
        policy.cost_usd_max,
    ) == (False, 30, 24, 3, 2, 2, 0.05, 0.7, 0.9, 3, 250, 1000, 0, 0)


def test_contract_roundtrip_is_canonical_and_domain_separated():
    observation = _observations()[0]
    payload = to_dict(observation)
    assert canonical_json(payload) == canonical_json(dict(reversed(list(payload.items()))))
    assert input_digest(payload) == input_digest(payload)
    assert input_digest(payload) != policy_digest_value(payload)
    assert input_digest(payload) != observation_digest(payload)
    assert input_digest(payload) != "sha256:" + __import__("hashlib").sha256(canonical_json(payload)).hexdigest()
    with pytest.raises(ShadowContractError):
        canonical_json({1: "non-string key"})
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ShadowContractError):
        canonical_json(cycle)
    with pytest.raises(ShadowContractError):
        canonical_json([[[[[[[[[[[[[[[[[1]]]]]]]]]]]]]]]]])
    with pytest.raises(ShadowContractError):
        canonical_json(2**64)


@pytest.mark.parametrize("hostile", [float("nan"), float("inf"), -1.0])
def test_hostile_nonfinite_and_negative_metrics_are_rejected(hostile):
    with pytest.raises(ShadowContractError):
        replace(_observations()[0], confidence_delta=hostile)


def test_oversized_duplicate_claim_payload_is_rejected():
    with pytest.raises(ShadowContractError):
        replace(_observations()[0], claim_ids=("duplicate", "duplicate"))
    with pytest.raises(ShadowContractError):
        replace(_observations()[0], claim_ids=tuple(f"c{i}" for i in range(1001)))


def test_passing_window_only_becomes_eligible_for_operator_review():
    observations = _observations()
    decision = evaluate_shadow(
        observations,
        load_policy(),
        now="2026-07-28T01:00:00+00:00",
    )
    assert decision.action is ShadowDecisionAction.ELIGIBLE_FOR_OPERATOR_REVIEW
    assert decision.aggregate.blockers == ()


@pytest.mark.parametrize(
    ("mutation", "expected_action", "blocker"),
    [
        ({"status": "timeout", "parity_passed": False}, ShadowDecisionAction.STOP, "terminal_timeout"),
        ({"cost_usd": 0.01}, ShadowDecisionAction.STOP, "nonzero_provider_or_cost"),
        ({"provider_calls": 1}, ShadowDecisionAction.STOP, "nonzero_provider_or_cost"),
        ({"elapsed_ms": 1001}, ShadowDecisionAction.CONTINUE_OBSERVATION, "latency_each_exceeded"),
    ],
)
def test_timeout_error_cost_and_latency_fail_closed(mutation, expected_action, blocker):
    observations = _observations()
    observations[-1] = replace(observations[-1], **mutation)
    decision = evaluate_shadow(observations, load_policy(), now="2026-07-28T01:00:00+00:00")
    assert decision.action is expected_action
    assert ShadowBlocker(blocker) in decision.aggregate.blockers


def test_observed_p95_above_policy_threshold_blocks_promotion():
    observations = [
        replace(observation, elapsed_ms=300.0)
        for observation in _observations()
    ]

    decision = evaluate_shadow(
        observations,
        load_policy(),
        now="2026-07-28T01:00:00+00:00",
    )

    assert decision.aggregate.latency_p95_ms == 300.0
    assert decision.action is ShadowDecisionAction.CONTINUE_OBSERVATION
    assert ShadowBlocker.LATENCY_P95 in decision.aggregate.blockers


def test_aggregate_parity_threshold_allows_ten_percent_failures():
    observations = _observations()
    for index in range(3):
        observations[index] = replace(
            observations[index],
            parity_passed=False,
            confidence_delta=0.06,
        )

    decision = evaluate_shadow(
        observations,
        load_policy(),
        now="2026-07-28T01:00:00+00:00",
    )

    assert decision.aggregate.parity_rate == 0.9
    assert decision.action is ShadowDecisionAction.ELIGIBLE_FOR_OPERATOR_REVIEW
    assert decision.aggregate.blockers == ()


def test_aggregate_parity_threshold_blocks_below_ninety_percent():
    observations = _observations()
    for index in range(4):
        observations[index] = replace(
            observations[index],
            parity_passed=True,
            supporting_jaccard=0.6,
        )

    decision = evaluate_shadow(
        observations,
        load_policy(),
        now="2026-07-28T01:00:00+00:00",
    )

    assert decision.aggregate.parity_rate < 0.9
    assert decision.action is ShadowDecisionAction.CONTINUE_OBSERVATION
    assert decision.aggregate.blockers == (ShadowBlocker.PARITY_RATE,)


def test_mixed_identity_and_stale_window_fail_closed():
    observations = _observations()
    other_identity = replace(
        observations[-1].release_identity,
        candidate_release="release:kernel@different",
    )
    observations[-1] = replace(observations[-1], release_identity=other_identity)
    mixed = evaluate_shadow(observations, load_policy(), now="2026-07-28T01:00:00+00:00")
    assert mixed.action is ShadowDecisionAction.STOP
    assert ShadowBlocker.MIXED_RELEASE_IDENTITY in mixed.aggregate.blockers
    stale = evaluate_shadow(_observations(), load_policy(), now="2026-07-30T01:00:00+00:00")
    assert stale.action is ShadowDecisionAction.STOP
    assert ShadowBlocker.MISSING_STALE_OR_FUTURE in stale.aggregate.blockers


def test_duplicate_observation_replay_stops_evaluation():
    observations = _observations()
    observations[-1] = observations[-2]
    decision = evaluate_shadow(observations, load_policy(), now="2026-07-28T01:00:00+00:00")
    assert decision.action is ShadowDecisionAction.STOP
    assert ShadowBlocker.DUPLICATE_OBSERVATION in decision.aggregate.blockers


def test_request_id_replay_stops_even_when_metrics_and_input_differ():
    observations = _observations()
    changed_input = replace(
        observations[-1].canonical_input,
        request_id=observations[-2].canonical_input.request_id,
    )
    observations[-1] = replace(
        observations[-1],
        canonical_input=changed_input,
        input_digest=input_digest(to_dict(changed_input)),
        confidence_delta=0.02,
    )
    decision = evaluate_shadow(observations, load_policy(), now="2026-07-28T01:00:00+00:00")
    assert decision.action is ShadowDecisionAction.STOP
    assert ShadowBlocker.REPLAY_REQUEST_ID in decision.aggregate.blockers


def test_input_digest_replay_stops_even_when_observation_differs():
    observations = _observations()
    replay_input = observations[-2].canonical_input
    observations[-1] = replace(
        observations[-1],
        canonical_input=replay_input,
        input_digest=input_digest(to_dict(replay_input)),
        observed_at="2026-07-28T00:59:00+00:00",
        elapsed_ms=101,
    )
    decision = evaluate_shadow(observations, load_policy(), now="2026-07-28T01:00:00+00:00")
    assert decision.action is ShadowDecisionAction.STOP
    assert ShadowBlocker.REPLAY_INPUT_DIGEST in decision.aggregate.blockers


@pytest.mark.parametrize(
    ("pit_epoch", "blocker"),
    [
        (
            datetime(2026, 7, 28, 0, 59, tzinfo=timezone.utc).timestamp(),
            ShadowBlocker.PIT_AFTER_OBSERVATION,
        ),
        (
            datetime(2026, 7, 26, 0, 0, tzinfo=timezone.utc).timestamp(),
            ShadowBlocker.PIT_OUTSIDE_WINDOW,
        ),
        (
            datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc).timestamp(),
            ShadowBlocker.PIT_OUTSIDE_WINDOW,
        ),
    ],
)
def test_future_and_stale_pit_fail_closed(pit_epoch, blocker):
    observations = _observations()
    changed_input = replace(observations[0].canonical_input, pit_epoch=pit_epoch)
    observations[0] = replace(
        observations[0],
        canonical_input=changed_input,
        input_digest=input_digest(to_dict(changed_input)),
    )
    decision = evaluate_shadow(observations, load_policy(), now="2026-07-28T01:00:00+00:00")
    assert decision.action is ShadowDecisionAction.STOP
    assert blocker in decision.aggregate.blockers


def test_input_digest_and_identity_grammar_are_verified():
    observation = _observations()[0]
    with pytest.raises(ShadowContractError):
        replace(observation, input_digest="sha256:" + "0" * 64)
    with pytest.raises(ShadowContractError):
        replace(observation.release_identity, active_release="../main")
    with pytest.raises(ShadowContractError):
        replace(
            observation.release_identity,
            candidate_artifact_digest=observation.release_identity.active_artifact_digest,
        )
    with pytest.raises(ShadowContractError):
        replace(observation.release_identity, policy_digest="sha256:" + "A" * 64)


def test_missing_cartesian_cell_blocks_readiness():
    observations = [
        item for item in _observations()
        if not (
            item.canonical_input.coin == "SOL"
            and item.canonical_input.question_type == "hypothesis"
        )
    ]
    decision = evaluate_shadow(observations, load_policy(), now="2026-07-28T01:00:00+00:00")
    assert ShadowBlocker.INCOMPLETE_SCENARIO_MATRIX in decision.aggregate.blockers


def test_aggregate_and_decision_reject_illegal_construction():
    identity = _identity()
    with pytest.raises(ShadowContractError):
        ShadowAggregate(
            release_identity=identity, observation_count=-1, coin_count=0,
            question_type_count=0, minimum_cell_count=0, parity_rate=0,
            terminal_failure_streak=0, latency_p95_ms=0, blockers=(),
        )
    aggregate = ShadowAggregate(
        release_identity=identity, observation_count=30, coin_count=3,
        question_type_count=2, minimum_cell_count=5, parity_rate=1,
        terminal_failure_streak=0, latency_p95_ms=100,
        blockers=(ShadowBlocker.PARITY_FAILURE,),
    )
    with pytest.raises(ShadowContractError):
        ShadowDecision(
            release_identity=identity,
            action=ShadowDecisionAction.ELIGIBLE_FOR_OPERATOR_REVIEW,
            aggregate=aggregate,
        )


# ---------------------------------------------------------------------------
# Issue #871: intrinsic_shadow observational context field on ShadowObservation.
# ---------------------------------------------------------------------------


def _observation_with_intrinsic(intrinsic_shadow):
    identity = _identity()
    canonical_input = ShadowInput(
        request_id="request-intrinsic",
        coin="BTC",
        question_type="analysis",
        pit_epoch=1_800_000_000.0,
        query="outlook",
    )
    return ShadowObservation(
        release_identity=identity,
        canonical_input=canonical_input,
        input_digest=input_digest(to_dict(canonical_input)),
        observed_at="2026-07-28T00:00:00Z",
        status="success",
        parity_passed=True,
        confidence_delta=0.01,
        trust_delta=0.01,
        supporting_jaccard=0.9,
        elapsed_ms=100,
        provider_calls=0,
        cost_usd=0,
        claim_ids=("claim-1",),
        intrinsic_shadow=intrinsic_shadow,
    )


def test_intrinsic_shadow_defaults_to_none_and_round_trips():
    observation = _observation_with_intrinsic(None)
    payload = to_dict(observation)
    assert payload["intrinsic_shadow"] is None
    assert canonical_json(payload)
    assert observation.intrinsic_shadow is None


def test_intrinsic_shadow_accepts_bounded_mapping_payload():
    payload_dict = {
        "schema_version": "1.0.0",
        "asset_id": "asset:btc",
        "total_delta": 0.0,
        "gate": {"passed": False, "known_count": 0, "source_family_count": 0},
        "dimensions": [],
    }
    observation = _observation_with_intrinsic(payload_dict)
    assert observation.intrinsic_shadow == payload_dict
    assert to_dict(observation)["intrinsic_shadow"] == payload_dict


@pytest.mark.parametrize("hostile", [["not", "a", "dict"], "string", 42, [1, 2, 3]])
def test_intrinsic_shadow_rejects_non_mapping(hostile):
    with pytest.raises(ShadowContractError):
        _observation_with_intrinsic(hostile)


def test_intrinsic_shadow_changes_observation_digest():
    base = _observation_with_intrinsic(None)
    enriched = _observation_with_intrinsic({"total_delta": 0.0})
    assert observation_digest(to_dict(base)) != observation_digest(to_dict(enriched))


def test_intrinsic_shadow_cannot_carry_nonfinite_values():
    with pytest.raises(ShadowContractError):
        _observation_with_intrinsic({"total_delta": float("inf")})


def test_intrinsic_shadow_does_not_affect_parity_or_decision_evaluation():
    without = _observations()
    identity = _identity()
    with_intrinsic = []
    for observation in without:
        with_intrinsic.append(
            replace(
                observation,
                intrinsic_shadow={"asset_id": "asset:btc", "total_delta": 0.08},
            )
        )
    policy = load_policy()
    now = "2026-07-28T00:30:00Z"
    plain = evaluate_shadow(without, policy, now=now)
    enriched = evaluate_shadow(with_intrinsic, policy, now=now)
    assert plain.action == enriched.action
    assert plain.aggregate == enriched.aggregate
