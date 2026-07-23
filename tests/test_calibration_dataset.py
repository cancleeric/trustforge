from dataclasses import replace

import pytest

from trustforge.analysis_quality_event import build_analysis_quality_event
from trustforge.calibration_dataset import CalibrationDatasetError, build_confidence_calibration_dataset
from trustforge.delayed_outcome_labeler import (
    FixtureAuthorityRegistry,
    FixtureMarketData,
    FixtureOutcomeLedger,
    FixturePrice,
    FixtureVenueCalendar,
    VenueSession,
    canonical_fixture_price_content_hash,
)
from trustforge.learning_event_contract import (
    canonical_integrity_checksum,
    make_learning_event,
)
from trustforge.learning_event_store import LearningEventAppendLog


def _analysis(day: int, analysis_id=None, tenant="tenant-a"):
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
            "tenant_id": tenant,
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
        trusted_tenant_id=tenant,
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
    item = FixturePrice(
        session_label=f"2026-07-{day:02d}",
        adjusted_close=close,
        event_at=f"2026-07-{day:02d}T20:00:00Z",
        available_at=f"2026-07-{day:02d}T21:00:00Z",
        provider="fixture-provider",
        dataset_version="fixture-dataset-v1",
        methodology_version="split-v1",
        content_hash="sha256:" + "0" * 64,
    )
    return replace(item, content_hash=canonical_fixture_price_content_hash(item))


def _outcome(
    analysis,
    end_day=2,
    ledger=None,
    variant="latest_official",
    labeled_at_override=None,
):
    start_day = int(analysis.event_time[8:10])
    fixture = FixtureMarketData(
        (
            _price(start_day, f"{100 + start_day}.00000000"),
            _price(end_day, f"{100 + end_day}.00000000"),
        )
    )
    labeled_at = labeled_at_override or f"2026-07-{end_day + 1:02d}T00:00:00Z"
    ledger = ledger or FixtureOutcomeLedger(append=LearningEventAppendLog())
    return ledger.observe(
        analysis,
        trusted_tenant_id=analysis.tenant_id,
        horizon="T+1",
        trusted_as_of_time=labeled_at,
        trusted_labeled_at=labeled_at,
        calendar=_calendar(),
        market_data=fixture,
        trusted_authority_registry=FixtureAuthorityRegistry.from_fixture(
            instrument=analysis.payload["coin"],
            calendar=_calendar(),
            market_data=fixture,
        ),
        market_data_variant=variant,
    )


def _registry_for(analyses, outcomes):
    analyses = list(analyses)
    prices = {}
    for outcome in outcomes:
        if outcome.kind != "delayed_outcome":
            continue
        for raw in outcome.provenance["source_record"]["selected_prices"].values():
            if raw is not None:
                price = FixturePrice(**dict(raw))
                prices[canonical_integrity_checksum(dict(raw))] = price
    return FixtureAuthorityRegistry.from_fixture(
        instrument=analyses[0].payload["coin"],
        calendar=_calendar(),
        market_data=FixtureMarketData(tuple(prices.values())),
    )


def _dataset(analyses, outcomes, *, variant="latest_official", registry=None):
    analyses = list(analyses)
    outcomes = list(outcomes)
    registry = registry or _registry_for(analyses, outcomes)
    return build_confidence_calibration_dataset(
        analyses,
        outcomes,
        producer_version="unit",
        trusted_tenant_id="tenant-a",
        market_data_variant=variant,
        trusted_authority_registry=registry,
    )


def test_calibration_dataset_joins_analysis_and_mature_outcome_with_traceability():
    analysis = _analysis(1)
    manifest = _dataset([analysis], [_outcome(analysis)])

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
        _dataset([no_id], [])
    with pytest.raises(CalibrationDatasetError, match="OHLCV"):
        _dataset([ohlcv], [])


def test_calibration_dataset_uses_latest_outcome_revision_without_rewrite():
    analysis = _analysis(1)
    ledger = FixtureOutcomeLedger(append=LearningEventAppendLog())
    old = _outcome(analysis, ledger=ledger)
    revised = _outcome(
        analysis,
        ledger=ledger,
        labeled_at_override="2026-07-04T00:00:00Z",
    )

    manifest = _dataset([analysis], [old, revised])

    assert manifest["rows"][0]["outcome_source_version"] == revised.payload["market_data_revision"]
    assert manifest["rows"][0]["outcome_identity"].endswith("/v2")


def test_calibration_dataset_temporal_split_is_chronological_and_reproducible():
    analyses = [_analysis(day) for day in range(1, 6)]
    outcomes = [_outcome(analysis, end_day=day + 1) for day, analysis in enumerate(analyses, start=1)]

    first = _dataset(analyses, outcomes)
    second = _dataset(reversed(analyses), reversed(outcomes))

    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert [row["split"] for row in first["rows"]] == ["train", "train", "train", "validation", "test"]
    assert [row["analysis_id"] for row in first["rows"]] == [f"an-{day}" for day in range(1, 6)]


def test_calibration_dataset_skips_pending_or_unavailable_outcomes():
    analysis = _analysis(1)
    fixture = FixtureMarketData(())
    labeled_at = "2026-07-03T01:00:00Z"
    pending = FixtureOutcomeLedger(append=LearningEventAppendLog()).observe(
        analysis,
        trusted_tenant_id="tenant-a",
        horizon="T+7",
        trusted_as_of_time=labeled_at,
        trusted_labeled_at=labeled_at,
        calendar=_calendar(),
        market_data=fixture,
        trusted_authority_registry=FixtureAuthorityRegistry.from_fixture(
            instrument=analysis.payload["coin"],
            calendar=_calendar(),
            market_data=fixture,
        ),
        market_data_variant="as_first_known",
    )

    manifest = _dataset([analysis], [pending], variant="as_first_known")

    assert manifest["row_count"] == 0


def test_calibration_dataset_requires_variant_and_isolates_tenant_and_variant():
    analysis = _analysis(1)
    first_known = _outcome(analysis, variant="as_first_known")
    latest = _outcome(analysis, variant="latest_official")
    other_analysis = _analysis(1, tenant="tenant-b")
    other = _outcome(other_analysis, variant="latest_official")

    manifest = _dataset(
        [analysis, other_analysis],
        [first_known, latest, other],
        variant="latest_official",
    )
    assert manifest["row_count"] == 1
    assert manifest["rows"][0]["outcome_identity"] == latest.identity

    with pytest.raises(CalibrationDatasetError, match="selected explicitly"):
        build_confidence_calibration_dataset(
            [analysis],
            [latest],
            producer_version="unit",
            trusted_tenant_id="tenant-a",
            market_data_variant="",
            trusted_authority_registry=_registry_for([analysis], [latest]),
        )


def test_calibration_join_binds_exact_analysis_identity_not_reused_analysis_id():
    first = _analysis(1, analysis_id="duplicate")
    raw_second = _analysis(2, analysis_id="duplicate")
    second = make_learning_event(
        kind=raw_second.kind,
        tenant_id=raw_second.tenant_id,
        entity_id=raw_second.entity_id,
        revision=2,
        event_time=raw_second.event_time,
        available_time=raw_second.available_time,
        as_of_time=raw_second.as_of_time,
        provenance=raw_second.provenance,
        payload=raw_second.payload,
    )
    outcome = _outcome(first)
    manifest = _dataset([first, second], [outcome])
    assert manifest["row_count"] == 1
    assert manifest["rows"][0]["analysis_identity"] == first.identity
    assert manifest["rows"][0]["source_event_identity"] == first.identity
    assert manifest["tenant_id"] == "tenant-a"
    assert manifest["market_data_variant"] == "latest_official"


def test_calibration_rejects_outcome_before_analysis_availability():
    analysis = _analysis(1)
    outcome = _outcome(analysis)
    forged_time = "2026-07-01T00:00:00.500000Z"
    payload = {
        **outcome.payload,
        "labeled_at": forged_time,
        "canonical_as_of": forged_time,
    }
    source_record = {
        **outcome.provenance["source_record"],
        "payload_checksum": canonical_integrity_checksum(payload),
    }
    forged = replace(
        outcome,
        available_time=forged_time,
        as_of_time=forged_time,
        payload=payload,
        provenance={
            **outcome.provenance,
            "observed_at": forged_time,
            "source_record": source_record,
            "checksum": canonical_integrity_checksum(source_record),
        },
    )
    with pytest.raises(CalibrationDatasetError, match="canonical validation"):
        _dataset([analysis], [forged])


@pytest.mark.parametrize("direction", ["neutral", "abstain"])
def test_calibration_excludes_non_directional_labeled_outcomes(direction):
    analysis = _analysis(1)
    analysis = replace(
        analysis,
        payload={
            **analysis.payload,
            "decision": {
                **analysis.payload["decision"],
                "direction": direction,
            },
        },
    )
    outcome = _outcome(analysis)
    manifest = _dataset([analysis], [outcome])
    assert outcome.payload["maturity"] == "labeled"
    assert outcome.payload["reason_code"] == "PREDICTION_NOT_DIRECTIONAL"
    assert manifest["row_count"] == 0


def test_other_tenant_non_outcome_event_is_filtered_before_validation():
    analysis = _analysis(1)
    outcome = _outcome(analysis)
    foreign_analysis = _analysis(2, tenant="tenant-b")
    manifest = _dataset([analysis], [foreign_analysis, outcome])
    assert manifest["row_count"] == 1


def test_higher_revision_wrong_source_cannot_shadow_legitimate_outcome():
    analysis = _analysis(1)
    ledger = FixtureOutcomeLedger(append=LearningEventAppendLog())
    legitimate = _outcome(analysis, ledger=ledger)
    successor = _outcome(
        analysis,
        ledger=ledger,
        labeled_at_override="2026-07-04T00:00:00Z",
    )
    payload = {
        **successor.payload,
        "source_event_identity": "forged-analysis-identity",
    }
    source_record = {
        **successor.provenance["source_record"],
        "analysis_identity": "forged-analysis-identity",
    }
    forged = replace(
        successor,
        payload=payload,
        provenance={
            **successor.provenance,
            "source_record": source_record,
            "checksum": canonical_integrity_checksum(source_record),
        },
    )
    with pytest.raises(CalibrationDatasetError, match="does not match analysis"):
        _dataset([analysis], [legitimate, forged])
