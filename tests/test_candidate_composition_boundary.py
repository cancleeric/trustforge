"""Adversarial ownership and idempotency tests for #1036."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from trustforge_core import (
    IntrinsicCandidateFacts,
    KernelClaim,
    KernelDocument,
    KernelInput,
    compose_intrinsic_candidate,
    run_kernel,
)

ROOT = Path(__file__).resolve().parents[1]


def _baseline():
    first = KernelDocument("d1", "news", "reuters.com", "BTC evidence", 990.0)
    second = KernelDocument("d2", "onchain", "glassnode.com", "BTC evidence", 991.0)
    claims = (
        KernelClaim("c1", "BTC evidence", first, "fact", "bullish"),
        KernelClaim("c2", "BTC evidence", second, "fact", "bullish"),
    )
    return run_kernel(KernelInput(claims, 1000.0, "BTC", "BTC"))


def test_repeated_composition_is_idempotent_not_double_applied():
    baseline = _baseline()
    facts = IntrinsicCandidateFacts(0.08, "sha256:" + "a" * 64)
    first = compose_intrinsic_candidate(baseline, facts)
    second = compose_intrinsic_candidate(baseline, facts)
    assert first == second
    assert first.shadow.candidate_raw == min(1.0, baseline.trust_score + 0.08)
    assert first.official_output is baseline
    assert first.promoted is False


def test_prior_composition_cannot_be_used_as_a_baseline():
    baseline = _baseline()
    facts = IntrinsicCandidateFacts(0.01, "")
    prior = compose_intrinsic_candidate(baseline, facts)
    with pytest.raises(TypeError):
        compose_intrinsic_candidate(prior, facts)  # type: ignore[arg-type]


def test_signed_pass_bit_cannot_bypass_unimplemented_activation():
    baseline = _baseline()
    result = compose_intrinsic_candidate(
        baseline,
        IntrinsicCandidateFacts(0.08, ""),
        signed_promotion_passed=True,
    )
    assert result.official_output is baseline
    assert result.promoted is False
    assert result.promotion_reason == "activation_not_implemented"


@pytest.mark.parametrize(
    "relative",
    [
        "src/trustforge/web.py",
        "src/trustforge/agent/orchestrator.py",
        "src/trustforge/agent/shadow_runtime.py",
    ],
)
def test_application_layers_do_not_apply_intrinsic_delta(relative):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add):
            continue
        source = ast.unparse(node)
        assert not (
            "trust_score" in source
            and ("total_delta" in source or "signed_delta" in source)
        ), f"{relative} applies candidate delta directly: {source}"


def test_core_candidate_module_has_no_application_imports():
    path = ROOT / "src/trustforge_core/candidate_composition.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(
        name == "trustforge" or name.startswith("trustforge.") for name in imports
    )
