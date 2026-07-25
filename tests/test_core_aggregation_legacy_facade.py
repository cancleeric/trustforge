"""Legacy facade regression tests for pure core aggregation delegation."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from trustforge.ingestion.base import Document
from trustforge.trust import scoring


def _item(claim_id: str, trust: float, source: str) -> scoring.ScoredClaim:
    document = Document(
        id=f"doc-{claim_id}",
        kind="news",
        source=source,
        text="generic market update",
        ts=1.0,
        meta={},
    )
    return scoring.ScoredClaim(scoring.Claim(claim_id, document.text, document), trust)


def test_aggregate_calls_core_once_and_maps_original_objects(monkeypatch) -> None:
    first = _item("a", 0.8, "one")
    second = _item("b", 0.8, "two")
    real = scoring._core_aggregate_scored_claims
    calls = 0

    def counted(**kwargs: object):
        nonlocal calls
        calls += 1
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(scoring, "_core_aggregate_scored_claims", counted)
    monkeypatch.setattr(scoring, "_load_cached_calibration_model", lambda: None)
    result = scoring.aggregate([first, second], query=None, coin=None)
    assert calls == 1
    assert result.query == ""
    assert result.supporting[0] is first
    assert result.supporting[1] is second


def test_app_loader_resolves_fixed_and_exact_isotonic_values(monkeypatch) -> None:
    monkeypatch.setattr(scoring, "_load_cached_calibration_model", lambda: None)
    assert scoring._aggregate_calibration_spec() == ("fixed-heuristic-v1", ())
    model = [
        {"confidence": 0.0, "calibrated": 0.1},
        {"confidence": 1.0, "calibrated": 0.9},
    ]
    monkeypatch.setattr(scoring, "_load_cached_calibration_model", lambda: model)
    assert scoring._aggregate_calibration_spec() == (
        "isotonic-v1",
        ((0.0, 0.1), (1.0, 0.9)),
    )
    assert scoring._calibrate_confidence(0.58) == 0.564


def test_mapper_ignores_hostile_metadata_without_invoking_hooks() -> None:
    calls = {name: 0 for name in ("eq", "repr", "float")}

    class HostileKey:
        def __hash__(self) -> int:
            return hash("coin")

        def __eq__(self, other: object) -> bool:
            calls["eq"] += 1
            raise AssertionError

        def __repr__(self) -> str:
            calls["repr"] += 1
            raise AssertionError

        def __float__(self) -> float:
            calls["float"] += 1
            raise AssertionError

    key = HostileKey()
    metadata = {key: key}
    calls = {name: 0 for name in calls}
    item = _item("a", 0.8, "one")
    item.claim.doc.meta = metadata
    mapped = scoring._to_core_aggregate_scored(item)
    assert mapped.claim.document.metadata == ()
    assert calls == {name: 0 for name in calls}


def test_mapper_normalizes_hostile_timestamp_without_numeric_hooks() -> None:
    calls = 0

    class Hostile:
        def __float__(self) -> float:
            nonlocal calls
            calls += 1
            raise AssertionError

    item = _item("a", 0.8, "one")
    item.claim.doc.ts = Hostile()  # type: ignore[assignment]
    mapped = scoring._to_core_aggregate_scored(item)
    assert mapped.claim.document.timestamp == 0.0
    assert calls == 0


@pytest.mark.parametrize(
    "timestamp",
    [True, "1.0", 10**10000, float("nan"), float("inf"), float("-inf")],
    ids=("bool", "string", "huge-int", "nan", "positive-inf", "negative-inf"),
)
def test_mapper_normalizes_irrelevant_unsafe_timestamps(timestamp: object) -> None:
    item = _item("a", 0.8, "one")
    item.claim.doc.ts = timestamp  # type: ignore[assignment]
    mapped = scoring._to_core_aggregate_scored(item)
    assert mapped.claim.document.timestamp == 0.0


def test_mapper_normalizes_numeric_subclasses_without_hooks() -> None:
    calls = 0

    class HostileFloat(float):
        def __float__(self) -> float:
            nonlocal calls
            calls += 1
            raise AssertionError

    item = _item("a", 0.8, "one")
    item.claim.doc.ts = HostileFloat(1.0)
    mapped = scoring._to_core_aggregate_scored(item)
    assert mapped.claim.document.timestamp == 0.0
    assert calls == 0


def test_aggregate_ignores_bad_timestamp_and_preserves_original_identity(monkeypatch) -> None:
    item = _item("a", 0.8, "one")
    item.claim.doc.ts = object()  # type: ignore[assignment]
    monkeypatch.setattr(scoring, "_load_cached_calibration_model", lambda: None)
    result = scoring.aggregate([item], query="", coin=None)
    assert result.supporting == [item]
    assert result.supporting[0] is item


def test_core_aggregation_module_has_zero_runtime_provider_surface() -> None:
    path = Path(__file__).parents[1] / "src" / "trustforge_core" / "aggregation.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name.startswith("trustforge.") for name in imports)
    assert "provider" not in source.casefold()
