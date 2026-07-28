"""Production single-truth boundary regressions for #734."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from trustforge.agent import authoritative_kernel_mapper as kernel_mapper
from trustforge.direction_resolution import (
    DIRECTION_POLICY_VERSION,
    ResolvedDirection,
)
from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim


def _claim(timestamp=100.0) -> Claim:
    return Claim(
        "claim-1",
        "BTC demand increased",
        Document(
            "doc-1",
            "news",
            "coindesk",
            "BTC demand increased",
            ts=timestamp,
            meta={"coin": "BTC"},
        ),
        "fact",
        "bullish",
    )


def _direction() -> ResolvedDirection:
    return ResolvedDirection(
        value="bullish",
        policy_version=DIRECTION_POLICY_VERSION,
        method="no-signal",
        input_ids=(),
        reason="test boundary",
    )


def test_authoritative_boundary_passes_non_null_resolution_and_calls_kernel_once(
    monkeypatch,
) -> None:
    calls = []
    real_run_kernel = kernel_mapper.run_kernel

    def spy(kernel_input):
        calls.append(kernel_input)
        return real_run_kernel(kernel_input)

    monkeypatch.setattr(kernel_mapper, "run_kernel", spy)
    output, _, _, judgment = kernel_mapper.run_authoritative_judgment(
        [_claim()],
        pit_epoch=100.0,
        coin="BTC",
        query="BTC",
        direction=_direction(),
        offline=True,
    )

    assert len(calls) == 1
    assert calls[0].resolution is not None
    assert judgment.direction == output.direction
    assert judgment.confidence == output.confidence
    assert judgment.abstain == output.abstain


def test_authoritative_boundary_propagates_kernel_failure_without_fallback(
    monkeypatch,
) -> None:
    def fail(_kernel_input):
        raise RuntimeError("injected kernel failure")

    monkeypatch.setattr(kernel_mapper, "run_kernel", fail)
    with pytest.raises(RuntimeError, match="injected kernel failure"):
        kernel_mapper.run_authoritative_judgment(
            [_claim()],
            pit_epoch=100.0,
            coin="BTC",
            query="BTC",
            direction=_direction(),
            offline=True,
        )


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), "not-a-time"])
def test_authoritative_boundary_rejects_invalid_timestamp_before_kernel(
    monkeypatch, timestamp,
) -> None:
    called = False

    def forbidden(_kernel_input):
        nonlocal called
        called = True
        raise AssertionError("kernel must not run for malformed evidence")

    monkeypatch.setattr(kernel_mapper, "run_kernel", forbidden)
    with pytest.raises(ValueError, match="timestamp must be a finite number"):
        kernel_mapper.run_authoritative_judgment(
            [_claim(timestamp)],
            pit_epoch=100.0,
            coin="BTC",
            query="BTC",
            direction=_direction(),
            offline=True,
        )
    assert called is False


def test_production_entrypoints_do_not_call_legacy_judgment_producers() -> None:
    root = Path(__file__).parents[1] / "src" / "trustforge"
    for relative in ("agent/orchestrator.py", "analysis_flow.py"):
        tree = ast.parse((root / relative).read_text())
        forbidden = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                forbidden.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                forbidden.add(node.func.attr)
        assert "score" not in forbidden, relative
        assert "aggregate" not in forbidden, relative
