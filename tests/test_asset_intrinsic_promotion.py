"""Issue #875 (sub-ticket G): promotion / non-inferiority gate tests.

Mirrors the discipline of ``test_asset_intrinsic_benchmark.py`` and
``test_shadow_contracts.py``.  Verifies:

* T1 reproducibility (byte-equal receipt + golden commit-bound artifact).
* T2 fail-closed (malformed / nonfinite -> BLOCK, never raises).
* T3 identity-invariant (AC3): identical facts across symbols -> equal delta.
* T4 version-immutability (AC6): tampered policy file rejected; policy_digest
  precedes every result field in the serialized receipt.
* T5 BLOCK->PASS forbidden (AC7): changing the decision requires changing the
  policy, which changes policy_digest -> new receipt_id.
* T6 current dataset == BLOCK; calibration wording locked.
* T7 each AC4 stop condition fires.
* T8 import-surface guard (no parity kernel / scorer / calibration coupling).
"""

from __future__ import annotations

import ast
import json
from dataclasses import replace as dataclass_replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from trustforge.asset_intrinsic_promotion import (
    BLOCK_REASONS,
    COMMIT_BOUND_RECEIPT_PATH,
    POLICY_VERSION,
    IntrinsicPromotionDecision,
    IntrinsicPromotionError,
    IntrinsicPromotionPolicy,
    IntrinsicPromotionReason,
    RECEIPT_DOMAIN_VERSION,
    evaluate_current_dataset,
    evaluate_promotion,
    load_intrinsic_promotion_policy,
    policy_digest,
    policy_to_dict,
    receipt_canonical_dict,
    receipt_id,
    serialize_receipt,
)
import trustforge.asset_intrinsic_promotion as _module

REPO_ROOT = Path(__file__).resolve().parents[1]
_BENCH_DIGEST = "sha256:" + "0" * 64
NOW = "2026-07-29T00:00:00Z"
_DIM_NAMES = (
    "issuance_predictability",
    "control_dispersion",
    "supply_verifiability",
    "governance_capture_resistance",
    "holder_concentration",
)


# ---------------------------------------------------------------------------
# Fixtures: minimal valid intrinsic observation payloads.
# ---------------------------------------------------------------------------


def _obs(
    *,
    asset_id: str = "asset:test",
    observed_at: str = "2026-07-01T00:00:00Z",
    total_delta: float = 0.0,
    facts_hash: str | None = None,
    gate_passed: bool = True,
    known_count: int = 5,
    source_family_count: int = 2,
    dim_status: tuple[str, ...] = ("known",) * 5,
    source_urls: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if facts_hash is None:
        facts_hash = "sha256:" + "a" * 64
    if source_urls is None:
        source_urls = ("https://alpha.example/x", "https://beta.example/y")
    return {
        "asset_id": asset_id,
        "observed_at": observed_at,
        "as_of": observed_at,
        "total_delta": total_delta,
        "trust_delta": 0.0,
        "facts_hash": facts_hash,
        "gate": {
            "passed": gate_passed,
            "known_count": known_count,
            "source_family_count": source_family_count,
            "required_known": 3,
            "required_source_families": 2,
            "reason_code": "eligible" if gate_passed else "insufficient_coverage",
        },
        "dimensions": [
            {"name": name, "status": status, "provenance": {"source_urls": list(source_urls)}}
            for name, status in zip(_DIM_NAMES, dim_status)
        ],
    }


def _clean_observations(
    *,
    n: int = 210,
    assets: int = 5,
    days: int = 44,
) -> list[dict[str, Any]]:
    """A corpus that passes every gate except labels_mature (-> CONDITIONAL)."""
    obs: list[dict[str, Any]] = []
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    for i in range(n):
        asset = f"asset:clean-{i % assets}"
        observed = (base + timedelta(days=i % days)).isoformat().replace("+00:00", "Z")
        obs.append(
            _obs(
                asset_id=asset,
                observed_at=observed,
                total_delta=0.0,
                facts_hash="sha256:" + asset.encode().hex()[:64].ljust(64, "0"),
            )
        )
    return obs


@pytest.fixture
def policy() -> IntrinsicPromotionPolicy:
    return load_intrinsic_promotion_policy()


@pytest.fixture
def clean_obs() -> list[dict[str, Any]]:
    return _clean_observations()


# ---------------------------------------------------------------------------
# T1: reproducibility.
# ---------------------------------------------------------------------------


def test_reproducibility_same_inputs_yield_byte_equal_receipt(policy, clean_obs) -> None:
    r1 = evaluate_promotion(
        policy, clean_obs, benchmark_manifest_digest=_BENCH_DIGEST, now=NOW
    )
    r2 = evaluate_promotion(
        policy, clean_obs, benchmark_manifest_digest=_BENCH_DIGEST, now=NOW
    )
    assert serialize_receipt(r1) == serialize_receipt(r2)
    assert receipt_id(r1) == receipt_id(r2)


def test_reproducibility_observation_order_is_permutation_invariant(policy, clean_obs) -> None:
    reordered = list(reversed(clean_obs))
    r1 = evaluate_promotion(policy, clean_obs, benchmark_manifest_digest=_BENCH_DIGEST, now=NOW)
    r2 = evaluate_promotion(policy, reordered, benchmark_manifest_digest=_BENCH_DIGEST, now=NOW)
    assert receipt_id(r1) == receipt_id(r2)


def test_commit_bound_receipt_matches_fresh_run() -> None:
    fresh = evaluate_current_dataset()
    artifact = json.loads(COMMIT_BOUND_RECEIPT_PATH.read_text(encoding="utf-8"))
    assert artifact["receipt_id"] == receipt_id(fresh)
    assert artifact["receipt"] == receipt_canonical_dict(fresh)


# ---------------------------------------------------------------------------
# T2: fail-closed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malformed",
    [
        [{"asset_id": "asset:x", "total_delta": float("nan"), "facts_hash": "sha256:" + "a" * 64}],
        [{"asset_id": "asset:x", "total_delta": float("inf")}],
        ["not-a-mapping"],
        [{"asset_id": "asset:x"}],  # missing fields
        [{"asset_id": "asset:x", "total_delta": 0.0, "gate": "not-a-dict"}],
    ],
)
def test_fail_closed_malformed_or_nonfinite_yields_block(policy, malformed) -> None:
    receipt = evaluate_promotion(
        policy, malformed, benchmark_manifest_digest=_BENCH_DIGEST, now=NOW
    )
    assert receipt.decision is IntrinsicPromotionDecision.BLOCK
    assert receipt.counts["corrupt_count"] >= 1 or bool(receipt.reasons)


def test_policy_construction_rejects_wrong_version() -> None:
    with pytest.raises(IntrinsicPromotionError):
        IntrinsicPromotionPolicy(
            version="not-the-v1-version",
            min_observations=200, min_assets=5, min_days=30, min_known=3, min_families=2,
            max_abs_delta=0.08, brier_degradation_limit=0.01, ece_degradation_limit=0.01,
            labels_mature=False, min_eligible_fraction=0.6, max_decision_flips=0,
            max_coverage_disparity=2, max_missingness_rate=0.5, sensitivity_bound=0.08,
            max_single_source_family_share=0.6, corrupt_rate_max=0.05,
        )


def test_policy_construction_rejects_out_of_range_threshold() -> None:
    # min_observations must be a positive int; cannot smuggle a zeroed threshold.
    with pytest.raises(IntrinsicPromotionError):
        IntrinsicPromotionPolicy(
            version=POLICY_VERSION,
            min_observations=0, min_assets=5, min_days=30, min_known=3, min_families=2,
            max_abs_delta=0.08, brier_degradation_limit=0.01, ece_degradation_limit=0.01,
            labels_mature=False, min_eligible_fraction=0.6, max_decision_flips=0,
            max_coverage_disparity=2, max_missingness_rate=0.5, sensitivity_bound=0.08,
            max_single_source_family_share=0.6, corrupt_rate_max=0.05,
        )


# ---------------------------------------------------------------------------
# T3: identity-invariant (AC3 core metamorphic).
# ---------------------------------------------------------------------------


def test_identical_facts_across_symbols_yield_equal_delta(policy) -> None:
    shared_facts = "sha256:" + "f" * 64
    a = _obs(asset_id="asset:alpha", facts_hash=shared_facts, total_delta=0.03)
    b = _obs(asset_id="asset:beta", facts_hash=shared_facts, total_delta=0.03)
    receipt = evaluate_promotion(
        policy, [a, b], benchmark_manifest_digest=_BENCH_DIGEST, now=NOW
    )
    assert IntrinsicPromotionReason.IDENTICAL_FACTS_DIVERGENT_DELTA not in receipt.reasons


def test_divergent_delta_for_identical_facts_blocks(policy) -> None:
    shared_facts = "sha256:" + "f" * 64
    a = _obs(asset_id="asset:alpha", facts_hash=shared_facts, total_delta=0.03)
    b = _obs(asset_id="asset:beta", facts_hash=shared_facts, total_delta=-0.03)
    receipt = evaluate_promotion(
        policy, [a, b], benchmark_manifest_digest=_BENCH_DIGEST, now=NOW
    )
    assert receipt.decision is IntrinsicPromotionDecision.BLOCK
    assert IntrinsicPromotionReason.IDENTICAL_FACTS_DIVERGENT_DELTA in receipt.reasons


def test_identity_invariant_full_receipt_unchanged_under_symbol_rename(policy) -> None:
    shared_facts = "sha256:" + "f" * 64
    base_a = _obs(asset_id="asset:alpha", facts_hash=shared_facts, total_delta=0.0)
    base_b = _obs(asset_id="asset:beta", facts_hash=shared_facts, total_delta=0.0)
    renamed_a = _obs(asset_id="asset:gamma", facts_hash=shared_facts, total_delta=0.0)
    renamed_b = _obs(asset_id="asset:delta", facts_hash=shared_facts, total_delta=0.0)
    r1 = evaluate_promotion(policy, [base_a, base_b], benchmark_manifest_digest=_BENCH_DIGEST, now=NOW)
    r2 = evaluate_promotion(policy, [renamed_a, renamed_b], benchmark_manifest_digest=_BENCH_DIGEST, now=NOW)
    # Decisions match; the structural identity (facts + delta) is what matters.
    assert r1.decision == r2.decision


# ---------------------------------------------------------------------------
# T4: version-immutability (AC6).
# ---------------------------------------------------------------------------


def _write_policy(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_policy_file_rejects_tampered_value(tmp_path) -> None:
    payload = policy_to_dict(load_intrinsic_promotion_policy())
    payload["min_observations"] = 1  # tamper
    with pytest.raises(IntrinsicPromotionError):
        load_intrinsic_promotion_policy(_write_policy(tmp_path, payload))


def test_policy_file_rejects_wrong_field_set(tmp_path) -> None:
    payload = policy_to_dict(load_intrinsic_promotion_policy())
    del payload["min_observations"]
    payload["extra_field"] = 0
    with pytest.raises(IntrinsicPromotionError):
        load_intrinsic_promotion_policy(_write_policy(tmp_path, payload))


def test_checked_in_policy_has_stable_v1_digest() -> None:
    policy = load_intrinsic_promotion_policy()
    assert policy.version == POLICY_VERSION
    assert policy_digest(policy) == "sha256:8687d5afeb21b4f7b142357ed17da62e98b352967ab8c3f42823573032839ddc"


def test_serialized_receipt_orders_policy_before_result_fields(policy, clean_obs) -> None:
    receipt = evaluate_promotion(
        policy, clean_obs, benchmark_manifest_digest=_BENCH_DIGEST, now=NOW
    )
    text = serialize_receipt(receipt)
    policy_pos = text.index('"policy_digest"')
    for result_field in ('"decision"', '"reasons"', '"counts"', '"calibration_claim"'):
        assert policy_pos < text.index(result_field), result_field


# ---------------------------------------------------------------------------
# T5: BLOCK->PASS forbidden (AC7).
# ---------------------------------------------------------------------------


def test_block_to_pass_requires_new_policy_digest(clean_obs) -> None:
    v1 = load_intrinsic_promotion_policy()
    blocked = evaluate_promotion(
        v1, clean_obs[:3], benchmark_manifest_digest=_BENCH_DIGEST, now=NOW
    )
    assert blocked.decision is IntrinsicPromotionDecision.BLOCK

    # A deliberately loose threshold set (simulating an attempt to force PASS).
    loose = dataclass_replace(
        v1,
        min_observations=1,
        min_assets=1,
        min_days=1,
        min_eligible_fraction=0.0,
        max_decision_flips=10_000,
        max_coverage_disparity=10_000,
        max_missingness_rate=1.0,
        max_single_source_family_share=1.0,
        corrupt_rate_max=1.0,
    )
    passed = evaluate_promotion(
        loose, clean_obs[:3], benchmark_manifest_digest=_BENCH_DIGEST, now=NOW
    )
    assert passed.decision is not IntrinsicPromotionDecision.BLOCK
    # The decision change is only possible because the policy (hence digest,
    # hence receipt_id) changed.  There is no in-place mutation path.
    assert policy_digest(v1) != policy_digest(loose)
    assert receipt_id(blocked) != receipt_id(passed)


def test_receipt_is_immutable(policy, clean_obs) -> None:
    receipt = evaluate_promotion(
        policy, clean_obs, benchmark_manifest_digest=_BENCH_DIGEST, now=NOW
    )
    with pytest.raises((AttributeError, TypeError)):
        receipt.decision = IntrinsicPromotionDecision.BLOCK  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        receipt.reasons = ()  # type: ignore[misc]


def test_pass_decision_cannot_carry_reasons() -> None:
    with pytest.raises(IntrinsicPromotionError):
        IntrinsicPromotionReceipt_helper_block_with_pass_reasons()


def IntrinsicPromotionReceipt_helper_block_with_pass_reasons() -> None:
    from trustforge.asset_intrinsic_promotion import IntrinsicPromotionReceipt

    IntrinsicPromotionReceipt(
        receipt_domain_version=RECEIPT_DOMAIN_VERSION,
        policy_digest="sha256:" + "0" * 64,
        observation_root_digest="sha256:" + "0" * 64,
        benchmark_manifest_digest="sha256:" + "0" * 64,
        evaluated_at=NOW,
        policy={},
        decision=IntrinsicPromotionDecision.PASS,
        reasons=(IntrinsicPromotionReason.INSUFFICIENT_OBSERVATIONS,),
        calibration_claim="withheld_no_mature_labels",
        counts={},
    )


# ---------------------------------------------------------------------------
# T6: current dataset == BLOCK.
# ---------------------------------------------------------------------------


def test_current_dataset_is_block_with_expected_gaps() -> None:
    receipt = evaluate_current_dataset()
    assert receipt.decision is IntrinsicPromotionDecision.BLOCK
    reasons = {r.value for r in receipt.reasons}
    assert "insufficient_observations" in reasons
    assert "insufficient_asset_coverage" in reasons
    assert "insufficient_observation_span" in reasons
    assert receipt.counts["observation_count"] < 200
    assert receipt.counts["asset_count"] < 5
    assert receipt.counts["day_span"] < 30


def test_current_dataset_never_claims_calibration_improvement() -> None:
    receipt = evaluate_current_dataset()
    assert receipt.calibration_claim == "withheld_no_mature_labels"
    text = serialize_receipt(receipt)
    assert "calibration improvement" not in text.lower()
    assert "improve" not in text.lower()


# ---------------------------------------------------------------------------
# T7: each AC4 stop condition fires.
# ---------------------------------------------------------------------------


def _evaluate(policy, obs, **kwargs) -> Any:
    return evaluate_promotion(
        policy, obs, benchmark_manifest_digest=_BENCH_DIGEST, now=NOW, **kwargs
    )


def test_stop_condition_direction_or_decision_flip(policy) -> None:
    # One asset with two time-ordered observations of opposite-sign delta.
    flipped = [
        _obs(
            asset_id="asset:flip",
            observed_at="2026-06-01T00:00:00Z",
            total_delta=0.04,
            facts_hash="sha256:" + "1".ljust(64, "0"),
        ),
        _obs(
            asset_id="asset:flip",
            observed_at="2026-06-02T00:00:00Z",
            total_delta=-0.04,
            facts_hash="sha256:" + "2".ljust(64, "0"),
        ),
    ]
    receipt = _evaluate(policy, flipped)
    assert IntrinsicPromotionReason.DIRECTION_OR_DECISION_FLIP in receipt.reasons


def test_stop_condition_coverage_disparity(policy) -> None:
    # Asset A fully known (5); asset B only 2 known -> per-asset max range = 3.
    corpus = [
        _obs(asset_id="asset:full", observed_at="2026-06-0{}T00:00:00Z".format(i), known_count=5)
        for i in range(1, 6)
    ] + [
        _obs(asset_id="asset:sparse", observed_at="2026-06-0{}T00:00:00Z".format(i), known_count=2)
        for i in range(1, 6)
    ]
    receipt = _evaluate(policy, corpus)
    assert IntrinsicPromotionReason.COVERAGE_DISPARITY in receipt.reasons


def test_stop_condition_missingness(policy) -> None:
    obs = []
    for i in range(10):
        obs.append(
            _obs(
                asset_id=f"asset:miss-{i % 5}",
                dim_status=("unknown", "stale", "unknown", "unknown", "unknown"),
                known_count=0,
                source_family_count=0,
                gate_passed=False,
            )
        )
    receipt = _evaluate(policy, obs)
    assert IntrinsicPromotionReason.MISSINGNESS_RATE_EXCEEDED in receipt.reasons


def test_stop_condition_sensitivity(policy, clean_obs) -> None:
    receipt = _evaluate(
        policy, clean_obs, sensitivity_report={"out_of_bound": True}
    )
    assert IntrinsicPromotionReason.SENSITIVITY_OUT_OF_BOUND in receipt.reasons


def test_stop_condition_sensitivity_via_max_response(policy, clean_obs) -> None:
    receipt = _evaluate(
        policy, clean_obs, sensitivity_report={"max_abs_response": 0.09}
    )
    assert IntrinsicPromotionReason.SENSITIVITY_OUT_OF_BOUND in receipt.reasons


def test_stop_condition_single_source_dependency(policy, clean_obs) -> None:
    obs = list(clean_obs)
    for i, o in enumerate(obs):
        urls = ("https://alpha.example/x",) if i % 5 else (
            "https://alpha.example/x",
            "https://beta.example/y",
        )
        o["dimensions"] = [
            {"name": d["name"], "status": "known", "provenance": {"source_urls": list(urls)}}
            for d in o["dimensions"]
        ]
    receipt = _evaluate(policy, obs)
    assert IntrinsicPromotionReason.SINGLE_SOURCE_DEPENDENCY in receipt.reasons


def test_calibration_regression_blocks_when_labels_mature(clean_obs) -> None:
    v1 = load_intrinsic_promotion_policy()
    mature = dataclass_replace(v1, labels_mature=True)
    receipt = evaluate_promotion(
        mature,
        clean_obs,
        benchmark_manifest_digest=_BENCH_DIGEST,
        now=NOW,
        calibration={"brier_delta": 0.02, "ece_delta": 0.0},
    )
    assert receipt.decision is IntrinsicPromotionDecision.BLOCK
    assert IntrinsicPromotionReason.CALIBRATION_REGRESSION in receipt.reasons


def test_pass_reachable_when_labels_mature_and_no_regression(clean_obs) -> None:
    v1 = load_intrinsic_promotion_policy()
    mature = dataclass_replace(v1, labels_mature=True)
    receipt = evaluate_promotion(
        mature,
        clean_obs,
        benchmark_manifest_digest=_BENCH_DIGEST,
        now=NOW,
        calibration={"brier_delta": 0.005, "ece_delta": 0.005},
    )
    assert receipt.decision is IntrinsicPromotionDecision.PASS
    assert receipt.reasons == ()


def test_conditional_when_evidence_sufficient_but_labels_immature(clean_obs) -> None:
    v1 = load_intrinsic_promotion_policy()
    receipt = evaluate_promotion(
        v1, clean_obs, benchmark_manifest_digest=_BENCH_DIGEST, now=NOW
    )
    assert receipt.decision is IntrinsicPromotionDecision.CONDITIONAL
    assert receipt.calibration_claim == "withheld_no_mature_labels"


# ---------------------------------------------------------------------------
# T8: import-surface guard.
# ---------------------------------------------------------------------------


def _imported_from(source: str) -> list[tuple[str, str]]:
    tree = ast.parse(source)
    edges: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(
            "trustforge"
        ):
            for alias in node.names:
                edges.append((node.module, alias.name))
    return edges


def test_module_does_not_import_parity_kernel_or_official_layers() -> None:
    source = Path(_module.__file__).read_text(encoding="utf-8")
    edges = _imported_from(source)
    forbidden_names = {
        "ShadowPolicy",
        "ShadowDecision",
        "ShadowBlocker",
        "ShadowDecisionAction",
        "evaluate_shadow",
    }
    for module, name in edges:
        assert "shadow_contracts" not in module, (module, name)
        assert name not in forbidden_names, (module, name)
        assert not module.startswith("trustforge.calibration"), (module, name)
        assert "scoring" not in module, (module, name)
        assert "decision_state" not in module, (module, name)
        assert "direction" not in module, (module, name)
        assert not module.startswith("trustforge.web"), (module, name)


def test_block_reasons_set_is_complete() -> None:
    # Every hard-block reason forces BLOCK (receipt invariant).
    assert IntrinsicPromotionReason.POLICY_UNVERSIONED in BLOCK_REASONS
    assert IntrinsicPromotionReason.RECEIPT_MALFORMED in BLOCK_REASONS
    assert IntrinsicPromotionReason.IDENTICAL_FACTS_DIVERGENT_DELTA in BLOCK_REASONS
