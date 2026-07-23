from dataclasses import replace

import pytest

from trustforge.analysis_quality_event import build_analysis_quality_event
from trustforge.calibration_dataset import CalibrationDatasetError, build_confidence_calibration_dataset
from trustforge.delayed_outcome_labeler import build_delayed_outcome_observation


def _analysis(day: int, analysis_id=None):
    return build_analysis_quality_event(
        {
            "analysis_id": analysis_id or f"an-{day}",
            "tenant_id": "tenant-a",
            "coin": "BTC",
            "mode": "formal",
            "question_type": "direction",
            "event_time": f"2026-07-{day:02d}T00:00:00Z",
            "available_time": f"2026-07-{day:02d}T00:00:01Z",
            "as_of_time": f"2026-07-{day:02d}T00:00:01Z",
            "source_available_times": [f"2026-07-{day:02d}T00:00:00Z"],
            "provenance": {"source": "analysis-flow", "collector": "unit-test", "observed_at": f"2026-07-{day:02d}T00:00:01Z"},
            "confidence": {"raw": 0.7, "calibrated": 0.62},
            "decision": {"direction": "bullish", "abstain": False},
            "evidence_stats": {"supporting": 3, "contrarian": 1, "missingness": 0.0},
            "quality": {"freshness": "ok", "conflict": "low", "completeness": "complete"},
            "versions": {"kernel": "learning-event.v1"},
            "stage_metrics": [],
        },
        trusted_tenant_id="tenant-a",
    )


def _outcome(analysis, end_day=2, revision=1):
    prices = {
        analysis.event_time[:10]: {"close": 100, "available_time": analysis.available_time, "source_id": "start"},
        f"2026-07-{end_day:02d}": {"close": 110, "available_time": f"2026-07-{end_day:02d}T01:00:00Z", "source_id": "end"},
    }
    return build_delayed_outcome_observation(
        analysis,
        horizon="T+1",
        as_of_time=f"2026-07-{end_day:02d}T01:00:00Z",
        prices=prices,
        source_version=f"fixture-v{revision}",
        revision=revision,
    )


def test_calibration_dataset_joins_analysis_and_mature_outcome_with_traceability():
    analysis = _analysis(1)
    manifest = build_confidence_calibration_dataset([analysis], [_outcome(analysis)], producer_version="unit")

    assert manifest["row_count"] == 1
    row = manifest["rows"][0]
    assert row["analysis_id"] == "an-1"
    assert row["analysis_identity"] == analysis.identity
    assert row["outcome_identity"].endswith("/v1")
    assert row["schema_version"] == "learning-event.v1"
    assert len(manifest["rows_sha256"]) == 64
    assert len(manifest["manifest_sha256"]) == 64


def test_calibration_dataset_requires_analysis_id_and_rejects_ohlcv_expansion():
    no_id = _analysis(1)
    no_id = replace(no_id, payload={**no_id.payload, "analysis_id": ""})
    ohlcv = _analysis(2)
    ohlcv = replace(ohlcv, payload={**ohlcv.payload, "source_kind": "five_year_ohlcv"})

    with pytest.raises(CalibrationDatasetError, match="analysis_id"):
        build_confidence_calibration_dataset([no_id], [], producer_version="unit")
    with pytest.raises(CalibrationDatasetError, match="OHLCV"):
        build_confidence_calibration_dataset([ohlcv], [], producer_version="unit")


def test_calibration_dataset_uses_latest_outcome_revision_without_rewrite():
    analysis = _analysis(1)
    old = _outcome(analysis, revision=1)
    revised = _outcome(analysis, revision=2)

    manifest = build_confidence_calibration_dataset([analysis], [old, revised], producer_version="unit")

    assert manifest["rows"][0]["outcome_source_version"] == "fixture-v2"
    assert manifest["rows"][0]["outcome_identity"].endswith("/v2")


def test_calibration_dataset_temporal_split_is_chronological_and_reproducible():
    analyses = [_analysis(day) for day in range(1, 6)]
    outcomes = [_outcome(analysis, end_day=day + 1) for day, analysis in enumerate(analyses, start=1)]

    first = build_confidence_calibration_dataset(analyses, outcomes, producer_version="unit")
    second = build_confidence_calibration_dataset(reversed(analyses), reversed(outcomes), producer_version="unit")

    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert [row["split"] for row in first["rows"]] == ["train", "train", "train", "validation", "test"]
    assert [row["analysis_id"] for row in first["rows"]] == [f"an-{day}" for day in range(1, 6)]


def test_calibration_dataset_skips_pending_or_unavailable_outcomes():
    analysis = _analysis(1)
    pending = build_delayed_outcome_observation(
        analysis,
        horizon="T+7",
        as_of_time="2026-07-03T01:00:00Z",
        prices={},
        source_version="fixture-v1",
    )

    manifest = build_confidence_calibration_dataset([analysis], [pending], producer_version="unit")

    assert manifest["row_count"] == 0
