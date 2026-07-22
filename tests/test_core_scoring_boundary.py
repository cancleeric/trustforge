"""Parity and dependency-boundary tests for pure scoring primitives (#442)."""
from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from trustforge.ingestion.base import Document
from trustforge.trust import scoring as legacy
from trustforge_core.scoring import (
    interpolate_calibration,
    recency_decay,
    reputation_floor,
    source_reputation,
    stable_sigmoid,
)


CORE_FILE = Path(__file__).resolve().parents[1] / "src" / "trustforge_core" / "scoring.py"


def _claim(**document_fields: object) -> legacy.Claim:
    fields = {"id": "d", "kind": "news", "source": "Example", "text": "text"}
    fields.update(document_fields)
    return legacy.Claim(id="c", text="claim", doc=Document(**fields))


@pytest.mark.parametrize("kind", ["social", "news", "price", "unknown"])
def test_reputation_floor_matches_legacy(kind: str):
    assert reputation_floor(kind, legacy.KIND_REPUTATION) == legacy._reputation_floor(
        kind
    )


def test_source_reputation_matches_legacy_alias_and_unverified_guard():
    claim = _claim(
        kind="celebrity_trade",
        source="  X.COM  ",
        meta={"verified_onchain": False, "reputation": 0.9},
    )
    dynamic = {legacy._canonical_source(claim.doc.source): 0.99}
    assert source_reputation(
        kind=claim.doc.kind,
        source_key=legacy._canonical_source(claim.doc.source),
        metadata=claim.doc.meta,
        reputations=legacy.KIND_REPUTATION,
        dynamic=dynamic,
    ) == legacy._source_reputation(claim, dynamic)


@pytest.mark.parametrize(
    ("timestamp", "now"),
    [(0.0, 100.0), (100.0, 100.0), (100.0, 3700.0), (200.0, 100.0), (math.nan, 100.0)],
)
def test_recency_decay_matches_legacy(timestamp: float, now: float):
    claim = _claim(ts=timestamp)
    assert recency_decay(
        timestamp=timestamp, now=now, half_life_hours=12.0
    ) == legacy._recency_decay(claim, now, half_life_h=12.0)


@pytest.mark.parametrize("value", [-1000.0, -1.0, 0.0, 1.0, 1000.0])
def test_stable_sigmoid_matches_legacy(value: float):
    assert stable_sigmoid(value) == legacy._stable_sigmoid(value)


def test_fixed_calibration_interpolation_matches_legacy(monkeypatch):
    monkeypatch.setattr(legacy, "_load_cached_calibration_model", lambda: None)
    for raw in (-1.0, 0.0, 0.437, 0.55, 2.0):
        assert interpolate_calibration(
            raw, legacy._CALIBRATION_TABLE
        ) == legacy._calibrate_confidence(raw)


def test_core_scoring_imports_standard_library_only():
    tree = ast.parse(CORE_FILE.read_text(encoding="utf-8"), filename=str(CORE_FILE))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    assert imports == ["__future__", "math", "collections.abc"]
