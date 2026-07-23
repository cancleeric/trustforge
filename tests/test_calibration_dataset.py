from dataclasses import replace
import hashlib

import pytest

from trustforge.analysis_quality_event import build_analysis_quality_event
from trustforge.calibration_dataset import CalibrationDatasetError, build_confidence_calibration_dataset
from trustforge.delayed_outcome_labeler import (
    FixtureMarketData,
    FixturePrice,
    FixtureVenueCalendar,
    VenueSession,
    build_delayed_outcome_observation,
    canonical_market_data_revision,
)
from trustforge.learning_event_contract import canonical_integrity_checksum


def _analysis(day: int, analysis_id=None):
    evidence_snapshot = [
        {
            "source": f"source-{index}",
            "fetched_at": f"2026-07-{day:02d}T00:00:00.000000Z",
            "content_reference": f"sha256:content-{day}-{index}",
            "related_claim": f"claim-{index}",
            "schema_version": "evidence.v1",
            "trust": 0.8,
        }
        for index in range(4)
    ]
    pit = {
        "event_time": f"2026-07-{day:02d}T00:00:00Z",
        "available_time": f"2026-07-{day:02d}T00:00:01Z",
        "as_of_time": f"2026-07-{day:02d}T00:00:01Z",
        "source_available_times": [f"2026-07-{day:02d}T00:00:00Z"],
    }
    provenance = {
        "source": "analysis-flow",
        "collector": "unit-test",
        "observed_at": f"2026-07-{day:02d}T00:00:01Z",
    }
    return build_analysis_quality_event(
        {
            "analysis_id": analysis_id or f"an-{day}",
            "run_id": f"run-{day}",
            "question_id": f"question-{day}",
            "answer_id": f"answer-{day}",
            "evidence_snapshot_id": canonical_integrity_checksum(evidence_snapshot),
            "evidence_snapshot": evidence_snapshot,
            "question": "Will BTC rise?",
            "tenant_id": "tenant-a",
            "coin": "BTC",
            "mode": "formal",
            "question_type": "direction",
            "event_time": f"2026-07-{day:02d}T00:00:00Z",
            "available_time": f"2026-07-{day:02d}T00:00:01Z",
            "as_of_time": f"2026-07-{day:02d}T00:00:01Z",
            "source_available_times": [f"2026-07-{day:02d}T00:00:00Z"],
            "provenance": {
                "source": "analysis-flow",
                "collector": "unit-test",
                "observed_at": f"2026-07-{day:02d}T00:00:01Z",
            },
            "confidence": {"raw": 0.7, "calibrated": 0.62},
            "decision": {"direction": "bullish", "state": "buy"},
            "evidence_stats": {
                "supporting_count": 3,
                "contrarian_count": 1,
                "evidence_count": 4,
                "average_trust": 0.8,
                "independent_source_count": 3,
                "source_distribution": {"exchange": 2, "news": 2},
            },
            "quality": {"freshness": "ok", "conflict": "low", "missingness": 0.0, "completeness": "complete"},
            "versions": {
                "contract": "analysis-quality.v1",
                "schema": "analysis-quality.v1",
                "kernel": "learning-event.v1",
                "scoring": "score-v1",
                "evidence": "evidence-v1",
                "model": "model-v1",
                "prompt": "prompt-v1",
                "policy": "policy-v1",
                "rule": "rule-v1",
            },
            "stage_metrics": [
                {
                    "stage": "kernel",
                    "latency_ms": 1,
                    "status": "complete",
                    "attempts": 1,
                    "failure": None,
                }
            ],
            "failure": {"status": "complete", "failed_stage": None, "code": None, "message": None, "retryable": False},
        },
        trusted_tenant_id="tenant-a",
        trusted_pit=pit,
        trusted_provenance=provenance,
    )


def _calendar():
    return FixtureVenueCalendar(
        calendar_id="fixture:XNYS:calibration-v1",
        timezone="America/New_York",
        version_available_at="2026-06-01T00:00:00Z",
        continuous_24_7=False,
        prediction_cutoff_minutes=15,
        publication_lag_hours=4,
        sessions=tuple(
            VenueSession(
                f"2026-07-{day:02d}",
                "open",
                f"2026-07-{day:02d}T20:00:00Z",
            )
            for day in range(1, 21)
        ),
    )


def _price(day, close):
    record = f"{day}:{close}".encode()
    return FixturePrice(
        session_label=f"2026-07-{day:02d}",
        adjusted_close=close,
        event_at=f"2026-07-{day:02d}T20:00:00Z",
        available_at=f"2026-07-{day:02d}T21:00:00Z",
        provider="fixture-provider",
        dataset_version="fixture-dataset-v1",
        methodology_version="split-v1",
        content_hash="sha256:" + hashlib.sha256(record).hexdigest(),
    )


def _outcome(analysis, end_day=2, revision=1, supersedes=None):
    start_day = int(analysis.event_time[8:10])
    fixture = FixtureMarketData((_price(start_day, "100.00000000"), _price(end_day, "110.00000000")))
    labeled_at = f"2026-07-{end_day + 1:02d}T00:00:00Z"
    start = fixture.prices[0]
    target = fixture.prices[1]
    market_revision = canonical_market_data_revision(
        calendar_id=_calendar().calendar_id,
        variant="latest_official",
        fixture=fixture,
        start=start,
        target=target,
        visible_at=labeled_at,
    )
    event = build_delayed_outcome_observation(
        analysis,
        trusted_tenant_id="tenant-a",
        horizon="T+1",
        trusted_as_of_time=labeled_at,
        trusted_labeled_at=labeled_at,
        calendar=_calendar(),
        market_data=fixture,
        market_data_variant="latest_official",
        market_data_revision=market_revision,
        trusted_outcome_version=revision,
        trusted_supersedes=supersedes,
    )
    # #508's legacy dataset projection still consumes these compatibility
    # columns. The outcome itself was built and validated through the #507
    # canonical contract; this test-only projection keeps #508 behavior scoped.
    return replace(
        event,
        payload={
            **event.payload,
            "revision": str(revision),
            "source_version": f"fixture-v{revision}",
            "outcome_pct": event.payload["return_pct"],
            "ground_truth_direction": "bullish",
        },
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
    revised = _outcome(analysis, revision=2, supersedes=old)

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
    fixture = FixtureMarketData(())
    labeled_at = "2026-07-03T01:00:00Z"
    market_revision = canonical_market_data_revision(
        calendar_id=_calendar().calendar_id,
        variant="as_first_known",
        fixture=fixture,
        start=None,
        target=None,
        visible_at=labeled_at,
    )
    pending = build_delayed_outcome_observation(
        analysis,
        trusted_tenant_id="tenant-a",
        horizon="T+7",
        trusted_as_of_time=labeled_at,
        trusted_labeled_at=labeled_at,
        calendar=_calendar(),
        market_data=fixture,
        market_data_variant="as_first_known",
        market_data_revision=market_revision,
        trusted_outcome_version=1,
    )

    manifest = build_confidence_calibration_dataset([analysis], [pending], producer_version="unit")

    assert manifest["row_count"] == 0
