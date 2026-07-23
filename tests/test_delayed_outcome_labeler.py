from dataclasses import replace
import hashlib
import json
from datetime import date, timedelta

import pytest

from trustforge.analysis_quality_event import build_analysis_quality_event
from trustforge.delayed_outcome_labeler import (
    FixtureMarketData,
    FixtureOutcomeLedger,
    FixturePrice,
    FixtureVenueCalendar,
    VenueSession,
    canonical_fixture_price_content_hash,
    canonical_market_data_revision,
    validate_canonical_delayed_outcome,
)
from trustforge.learning_event_contract import (
    LearningEventError,
    canonical_integrity_checksum,
    make_learning_event,
)
from trustforge.learning_event_store import LearningEventAppendLog


def _analysis(*, tenant="tenant-a", available="2026-07-01T19:44:59Z", direction="bullish"):
    evidence = [
        {
            "source": f"source-{i}", "fetched_at": "2026-07-01T19:00:00.000000Z",
            "content_reference": f"sha256:content-{i}", "related_claim": f"claim-{i}",
            "schema_version": "evidence.v1", "trust": 0.8,
        }
        for i in range(4)
    ]
    pit = {
        "event_time": "2026-07-01T19:00:00Z", "available_time": available,
        "as_of_time": available, "source_available_times": ["2026-07-01T19:00:00Z"],
    }
    provenance = {"source": "analysis-flow", "collector": "unit-test", "observed_at": available}
    return build_analysis_quality_event(
        {
            "analysis_id": "an-1", "run_id": "run-1", "question_id": "q-1",
            "answer_id": "a-1", "evidence_snapshot_id": canonical_integrity_checksum(evidence),
            "evidence_snapshot": evidence, "question": "Will it rise?", "tenant_id": tenant,
            "coin": "XYZ", "mode": "formal", "question_type": "direction",
            "event_time": "2026-07-01T19:00:00Z", "available_time": available,
            "as_of_time": available, "source_available_times": ["2026-07-01T19:00:00Z"],
            "provenance": provenance, "confidence": {"raw": 0.7, "calibrated": 0.6},
            "decision": {"direction": direction, "state": "buy"},
            "evidence_stats": {"supporting_count": 3, "contrarian_count": 1, "evidence_count": 4,
                               "average_trust": 0.8, "independent_source_count": 3,
                               "source_distribution": {"news": 4}},
            "quality": {"freshness": "ok", "conflict": "low", "missingness": 0.0,
                        "completeness": "complete"},
            "versions": {"contract": "analysis-quality.v1", "schema": "analysis-quality.v1",
                         "kernel": "learning-event.v1", "scoring": "s1", "evidence": "e1",
                         "model": "m1", "prompt": "p1", "policy": "po1", "rule": "r1"},
            "stage_metrics": [{"stage": "kernel", "latency_ms": 1, "status": "complete",
                               "attempts": 1, "failure": None}],
            "failure": {"status": "complete", "failed_stage": None, "code": None,
                        "message": None, "retryable": False},
        },
        trusted_tenant_id=tenant, trusted_pit=pit, trusted_provenance=provenance,
    )


def _calendar():
    # Weekend/holiday/unknown price availability do not create sessions; Jul 3 is early close.
    return FixtureVenueCalendar(
        calendar_id="equity:XNYS:fixture-v1", timezone="America/New_York",
        version_available_at="2026-06-01T00:00:00Z", continuous_24_7=False,
        prediction_cutoff_minutes=15, publication_lag_hours=4,
        sessions=(
            VenueSession("2026-07-01", "open", "2026-07-01T20:00:00Z"),
            VenueSession("2026-07-02", "open", "2026-07-02T20:00:00Z"),
            VenueSession("2026-07-03", "open", "2026-07-03T17:00:00Z"),
            VenueSession("2026-07-04", "closed", None),
            VenueSession("2026-07-05", "closed", None),
            VenueSession("2026-07-06", "closed", None),
            VenueSession("2026-07-07", "open", "2026-07-07T20:00:00Z"),
            VenueSession("2026-07-08", "open", "2026-07-08T20:00:00Z"),
            VenueSession("2026-07-09", "open", "2026-07-09T20:00:00Z"),
            VenueSession("2026-07-10", "open", "2026-07-10T20:00:00Z"),
            VenueSession("2026-07-13", "open", "2026-07-13T20:00:00Z"),
            VenueSession("2026-07-14", "open", "2026-07-14T20:00:00Z"),
            VenueSession("2026-07-15", "open", "2026-07-15T20:00:00Z"),
            VenueSession("2026-07-16", "open", "2026-07-16T20:00:00Z"),
            VenueSession("2026-07-17", "open", "2026-07-17T20:00:00Z"),
            VenueSession("2026-07-20", "open", "2026-07-20T20:00:00Z"),
        ),
    )


def _price(label, close, available, *, revision="r1", method="split-v1"):
    close_at = next(
        s.scheduled_close_at for s in _calendar().sessions if s.label == label
    )
    item = FixturePrice(
        label, close, close_at, available, "fixture-provider",
        "fixture-dataset-v1", method,
        "sha256:" + "0" * 64,
    )
    return replace(item, content_hash=canonical_fixture_price_content_hash(item))


def _empty_revision(variant="as_first_known"):
    return canonical_market_data_revision(
        calendar=_calendar(),
        variant=variant,
        fixture=FixtureMarketData(()),
        start=None,
        target=None,
        visible_at="2026-07-03T00:00:00Z",
    )


def _build(*, horizon="T+1", as_of="2026-07-03T00:00:00Z", data=None,
           analysis=None, variant="as_first_known", labeled_at=None,
           ledger=None, dry_run=False):
    analysis = analysis or _analysis()
    fixture = FixtureMarketData(tuple(data or ()))
    ledger = ledger or FixtureOutcomeLedger(append=LearningEventAppendLog())
    return ledger.observe(
        analysis, trusted_tenant_id="tenant-a",
        trusted_as_of_time=as_of, trusted_labeled_at=labeled_at or as_of, calendar=_calendar(),
        market_data=fixture, horizon=horizon,
        market_data_variant=variant, dry_run=dry_run,
    )


def test_prediction_cutoff_selects_safe_start_and_calendar_counts_eligible_sessions():
    event = _build(
        horizon="T+7", as_of="2026-07-14T00:00:00Z",
        data=[_price("2026-07-01", "100.00000000", "2026-07-01T20:30:00Z"),
              _price("2026-07-13", "110.00000000", "2026-07-13T21:00:00Z")],
    )
    assert event.payload["start_session"] == "2026-07-01"
    assert event.payload["target_session"] == "2026-07-13"
    assert event.payload["maturity"] == "labeled"


def test_prediction_after_cutoff_moves_start_to_next_session():
    event = _build(
        analysis=_analysis(available="2026-07-01T19:45:01Z"),
        data=[_price("2026-07-02", "100.00000000", "2026-07-02T21:00:00Z"),
              _price("2026-07-03", "101.00000000", "2026-07-03T18:00:00Z")],
        as_of="2026-07-03T21:00:00Z",
    )
    assert (event.payload["start_session"], event.payload["target_session"]) == (
        "2026-07-02", "2026-07-03",
    )


def test_early_close_is_eligible_and_suspension_missing_bar_does_not_shift_target():
    event = _build(as_of="2026-07-05T21:00:00Z", data=[
        _price("2026-07-01", "100.00000000", "2026-07-01T21:00:00Z")
    ])
    assert event.payload["target_session"] == "2026-07-02"
    assert event.payload["maturity"] == "pending"
    assert event.payload["reason_code"] == "WAITING_LATE_DATA_CUTOFF"


def test_missing_bar_becomes_unavailable_only_after_elapsed_72_hour_cutoff():
    at_cutoff = _build(as_of="2026-07-06T00:00:00Z")
    after = _build(as_of="2026-07-06T00:00:00.000001Z")
    assert at_cutoff.payload["maturity"] == "pending"
    assert after.payload["maturity"] == "unavailable"
    assert after.payload["return_pct"] is None


def test_state_time_uses_labeled_at_not_later_report_as_of():
    event = _build(
        as_of="2026-07-06T00:00:00.000001Z",
        labeled_at="2026-07-02T23:59:59.999999Z",
    )
    assert event.payload["maturity"] == "pending"
    assert event.payload["reason_code"] == "WAITING_OFFICIAL_CLOSE"

    at_cutoff = _build(
        as_of="2026-07-07T00:00:00Z",
        labeled_at="2026-07-06T00:00:00Z",
    )
    after_cutoff = _build(
        as_of="2026-07-07T00:00:00Z",
        labeled_at="2026-07-06T00:00:00.000001Z",
    )
    assert at_cutoff.payload["maturity"] == "pending"
    assert after_cutoff.payload["maturity"] == "unavailable"


def test_publication_sla_boundary_never_labels_early_even_when_bars_exist():
    data = [
        _price("2026-07-01", "100.00000000", "2026-07-01T21:00:00Z"),
        _price("2026-07-02", "110.00000000", "2026-07-02T21:00:00Z"),
    ]
    before = _build(as_of="2026-07-02T23:59:59.999999Z", data=data)
    at = _build(as_of="2026-07-03T00:00:00Z", data=data)
    assert before.payload["maturity"] == "pending"
    assert before.payload["reason_code"] == "WAITING_OFFICIAL_CLOSE"
    assert before.payload["return_pct"] is None
    assert at.payload["maturity"] == "labeled"


def test_labeled_availability_cannot_precede_maturity_or_selected_sources():
    data = [
        _price("2026-07-01", "100.00000000", "2026-07-01T21:00:00Z"),
        _price("2026-07-02", "110.00000000", "2026-07-02T23:00:00Z"),
    ]
    pending = _build(
        as_of="2026-07-03T00:00:00Z",
        labeled_at="2026-07-02T22:00:00Z",
        data=data,
    )
    assert pending.payload["maturity"] == "pending"
    assert pending.payload["lineage"] is None
    with pytest.raises(LearningEventError, match="as_of cannot precede prediction"):
        _build(as_of="2026-07-01T19:44:58Z")


@pytest.mark.parametrize(
    ("direction", "expected_sign", "expected_hit"),
    [("bullish", 1, True), ("bearish", -1, False), ("neutral", 0, None), ("abstain", None, None)],
)
def test_decimal_metrics_direction_neutral_abstain_and_hit(direction, expected_sign, expected_hit):
    event = _build(
        analysis=_analysis(direction=direction),
        data=[_price("2026-07-01", "51.00000000", "2026-07-01T21:00:00Z"),
              _price("2026-07-02", "53.00000000", "2026-07-02T21:00:00Z")],
    )
    assert event.payload["return_pct"] == "3.92156863"
    assert event.payload["direction_sign"] == expected_sign
    assert event.payload["hit"] is expected_hit
    assert event.payload["reason_code"] == (
        "PREDICTION_NOT_DIRECTIONAL"
        if direction in {"neutral", "abstain"}
        else None
    )
    assert event.payload["risk_abs_move_pct"] == "3.92156863"
    assert event.payload["risk_downside_pct"] == "0.00000000"


def test_d6_requires_same_split_adjustment_lineage_and_excludes_dividend():
    good = _build(data=[
        _price("2026-07-01", "100.00000000", "2026-07-01T21:00:00Z"),
        _price("2026-07-02", "90.00000000", "2026-07-02T21:00:00Z"),
    ])
    assert good.payload["lineage"]["adjustment_basis"] == "split_adjusted_price_return"
    assert good.payload["lineage"]["cash_dividend_included"] is False
    with pytest.raises(LearningEventError, match="not allowlisted"):
        _build(data=[
            _price("2026-07-01", "100.00000000", "2026-07-01T21:00:00Z"),
            _price("2026-07-02", "90.00000000", "2026-07-02T21:00:00Z", method="raw-v1"),
        ])


def test_seven_key_sha_is_tenant_bound_and_outcome_is_never_evidence():
    first = _build()
    other_analysis = _analysis(tenant="tenant-b")
    other = FixtureOutcomeLedger(append=LearningEventAppendLog()).observe(
        other_analysis, trusted_tenant_id="tenant-b",
        trusted_as_of_time="2026-07-03T00:00:00Z",
        trusted_labeled_at="2026-07-03T00:00:00Z", calendar=_calendar(),
        market_data=FixtureMarketData(()), horizon="T+1",
        market_data_variant="as_first_known",
    )
    assert first.payload["outcome_id"] != other.payload["outcome_id"]
    assert len(first.payload["identity_inputs"]) == 7
    assert first.kind == "delayed_outcome"
    assert first.payload["eligible_as_evidence"] is False
    assert "evidence_id" not in first.payload


def test_latest_official_revision_is_append_only_same_tenant_supersession():
    log = LearningEventAppendLog()
    ledger = FixtureOutcomeLedger(append=log)
    first = _build(variant="latest_official", ledger=ledger)
    second = _build(
        variant="latest_official", ledger=ledger,
        as_of="2026-07-04T00:00:00Z", labeled_at="2026-07-04T00:00:00Z",
    )
    assert second.payload["supersedes_outcome_id"] == first.payload["outcome_id"]
    assert first.payload["supersedes_outcome_id"] is None
    assert len(log.replay()) == 2


def test_as_first_known_is_idempotent_and_later_fixture_data_needs_new_identity():
    log = LearningEventAppendLog()
    ledger = FixtureOutcomeLedger(append=log)
    first = _build(ledger=ledger)
    same = _build(ledger=ledger)
    assert same.identity == first.identity
    assert len(log.replay()) == 1


def test_d7_variants_choose_first_known_or_latest_available_revision():
    data = [
        _price("2026-07-01", "100.00000000", "2026-07-01T20:30:00Z"),
        _price("2026-07-01", "80.00000000", "2026-07-01T21:30:00Z"),
        _price("2026-07-02", "110.00000000", "2026-07-02T20:30:00Z"),
        _price("2026-07-02", "88.00000000", "2026-07-02T21:30:00Z"),
    ]
    first = _build(data=data, variant="as_first_known")
    latest = _build(data=data, variant="latest_official")
    assert first.payload["return_pct"] == "10.00000000"
    assert latest.payload["return_pct"] == "10.00000000"
    assert first.payload["lineage"]["start"]["available_at"] == "2026-07-01T20:30:00Z"
    assert latest.payload["lineage"]["start"]["available_at"] == "2026-07-01T21:30:00Z"


def test_market_data_revision_binds_selected_pair_and_rejects_tamper():
    data = [
        _price("2026-07-01", "100.00000000", "2026-07-01T21:00:00Z"),
        _price("2026-07-02", "110.00000000", "2026-07-02T21:00:00Z"),
    ]
    original = _build(data=data)
    tampered = list(data)
    tampered[1] = replace(tampered[1], content_hash="sha256:" + "a" * 64)
    with pytest.raises(LearningEventError, match="content hash"):
        _build(data=tampered)
    assert original.payload["market_data_revision"].startswith("sha256:")


def test_future_fixture_append_does_not_change_first_known_identity_or_manifest():
    visible = [
        _price("2026-07-01", "100.00000000", "2026-07-01T21:00:00Z"),
        _price("2026-07-02", "110.00000000", "2026-07-02T21:00:00Z"),
    ]
    original = _build(data=visible, variant="as_first_known")
    with_future = _build(
        data=visible + [
            _price("2026-07-01", "90.00000000", "2026-07-04T00:00:00Z"),
            _price("2026-07-02", "99.00000000", "2026-07-04T00:00:00Z"),
        ],
        variant="as_first_known",
        as_of="2026-07-05T00:00:00Z",
        labeled_at="2026-07-05T00:00:00Z",
    )
    assert with_future.payload["market_data_revision"] == original.payload[
        "market_data_revision"
    ]
    assert with_future.payload["outcome_id"] == original.payload["outcome_id"]


def test_same_available_time_ties_and_fixture_order_are_deterministic():
    tied = [
        _price("2026-07-01", "100.00000000", "2026-07-01T21:00:00Z", revision="a"),
        _price("2026-07-01", "101.00000000", "2026-07-01T21:00:00Z", revision="b"),
        _price("2026-07-02", "110.00000000", "2026-07-02T21:00:00Z", revision="a"),
        _price("2026-07-02", "111.00000000", "2026-07-02T21:00:00Z", revision="b"),
    ]
    forward = _build(data=tied, variant="as_first_known")
    reverse = _build(data=list(reversed(tied)), variant="as_first_known")
    assert forward.payload["market_data_revision"] == reverse.payload[
        "market_data_revision"
    ]
    assert forward.payload["outcome_id"] == reverse.payload["outcome_id"]


def test_d8_data_arriving_after_cutoff_requires_successor_revision():
    late_data = [
        _price("2026-07-01", "100.00000000", "2026-07-06T00:00:01Z"),
        _price("2026-07-02", "110.00000000", "2026-07-06T00:00:01Z"),
    ]
    with pytest.raises(LearningEventError, match="successor revision"):
        _build(as_of="2026-07-06T01:00:00Z", data=late_data)
    ledger = FixtureOutcomeLedger(append=LearningEventAppendLog())
    unavailable = _build(
        as_of="2026-07-06T00:00:00.000001Z",
        variant="latest_official", ledger=ledger,
    )
    recovered = _build(
        as_of="2026-07-06T01:00:00Z", data=late_data,
        variant="latest_official", ledger=ledger,
    )
    assert recovered.payload["maturity"] == "labeled"
    assert recovered.payload["supersedes_outcome_id"] == unavailable.payload["outcome_id"]


def test_dry_run_performs_zero_append():
    class ExplodingStore:
        def append(self, event):
            raise AssertionError("dry-run wrote")

    ledger = FixtureOutcomeLedger(append=ExplodingStore())
    planned = _build(ledger=ledger, dry_run=True)
    actual = _build(ledger=ledger, dry_run=True)
    assert planned.revision == actual.revision == 1


def test_ledger_append_failure_does_not_commit_version_and_retry_is_idempotent():
    class FailOnce:
        def __init__(self):
            self.calls = 0
            self.log = LearningEventAppendLog()

        def append(self, event):
            self.calls += 1
            if self.calls == 1:
                raise LearningEventError("fixture append failed")
            return self.log.append(event)

    port = FailOnce()
    ledger = FixtureOutcomeLedger(append=port)
    with pytest.raises(LearningEventError, match="append failed"):
        _build(ledger=ledger)
    created = _build(ledger=ledger)
    retry = _build(ledger=ledger)
    assert created.revision == retry.revision == 1
    assert created.identity == retry.identity
    assert len(port.log.replay()) == 1


def test_exact_retry_precedes_revision_budget_check():
    ledger = FixtureOutcomeLedger(
        append=LearningEventAppendLog(),
        maximum_revisions=1,
    )
    first = _build(ledger=ledger)
    retry = _build(ledger=ledger)
    assert retry.identity == first.identity
    with pytest.raises(LearningEventError, match="revision budget"):
        _build(
            ledger=ledger,
            as_of="2026-07-04T00:00:00Z",
            labeled_at="2026-07-04T00:00:00Z",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        {"classification": "evidentiary"},
        {"eligible_as_evidence": True},
        {"hit": True},
        {"outcome_id": "sha256:" + "f" * 64},
    ],
)
def test_public_validator_rejects_forged_delayed_outcome_payload(mutation):
    event = _build(analysis=_analysis(direction="neutral"))
    forged = replace(event, payload={**event.payload, **mutation})
    with pytest.raises(LearningEventError):
        validate_canonical_delayed_outcome(forged)


def test_public_validator_rejects_bullish_return_metric_mutation():
    event = _build(
        analysis=_analysis(direction="bullish"),
        data=[
            _price("2026-07-01", "100.00000000", "2026-07-01T21:00:00Z"),
            _price("2026-07-02", "110.00000000", "2026-07-02T21:00:00Z"),
        ],
    )
    payload = {
        **event.payload,
        "return_pct": "999.00000000",
    }
    source_record = {
        **event.provenance["source_record"],
        "payload_checksum": canonical_integrity_checksum(payload),
    }
    forged = replace(
        event,
        payload=payload,
        provenance={
            **event.provenance,
            "source_record": source_record,
            "checksum": canonical_integrity_checksum(source_record),
        },
    )
    with pytest.raises(LearningEventError, match="metrics are inconsistent"):
        validate_canonical_delayed_outcome(forged)


def test_public_validator_rejects_rechecksummed_all_metric_forgery():
    event = _build(
        analysis=_analysis(direction="bullish"),
        data=[
            _price("2026-07-01", "100.00000000", "2026-07-01T21:00:00Z"),
            _price("2026-07-02", "110.00000000", "2026-07-02T21:00:00Z"),
        ],
    )
    payload = {
        **event.payload,
        "return_pct": "999.00000000",
        "directional_return_pct": "999.00000000",
        "risk_abs_move_pct": "999.00000000",
        "risk_downside_pct": "0.00000000",
        "hit": True,
    }
    source_record = {
        **event.provenance["source_record"],
        "payload_checksum": canonical_integrity_checksum(payload),
    }
    forged = replace(
        event,
        payload=payload,
        provenance={
            **event.provenance,
            "source_record": source_record,
            "checksum": canonical_integrity_checksum(source_record),
        },
    )
    with pytest.raises(LearningEventError, match="do not match selected prices"):
        validate_canonical_delayed_outcome(forged)


def test_public_validator_rejects_rechecksummed_direction_sign_flip():
    event = _build(
        analysis=_analysis(direction="bullish"),
        data=[
            _price("2026-07-01", "100.00000000", "2026-07-01T21:00:00Z"),
            _price("2026-07-02", "110.00000000", "2026-07-02T21:00:00Z"),
        ],
    )
    payload = {
        **event.payload,
        "direction_sign": -1,
        "directional_return_pct": "-10.00000000",
        "hit": False,
    }
    source_record = {
        **event.provenance["source_record"],
        "payload_checksum": canonical_integrity_checksum(payload),
    }
    forged = replace(
        event,
        payload=payload,
        provenance={
            **event.provenance,
            "source_record": source_record,
            "checksum": canonical_integrity_checksum(source_record),
        },
    )
    with pytest.raises(
        LearningEventError,
        match="direction_sign does not match canonical prediction direction",
    ):
        validate_canonical_delayed_outcome(forged)


def test_public_validator_rejects_fully_readdressed_calendar_authority_forgery():
    event = _build(
        analysis=_analysis(direction="bullish"),
        data=[
            _price("2026-07-01", "100.00000000", "2026-07-01T21:00:00Z"),
            _price("2026-07-02", "110.00000000", "2026-07-02T21:00:00Z"),
        ],
    )
    original_source = event.provenance["source_record"]
    calendar_manifest = {
        **original_source["calendar_manifest"],
        "calendar_id": "forged:XNYS:fixture-v1",
    }
    revision_manifest = {
        "calendar": calendar_manifest,
        "variant": event.payload["market_data_variant"],
        "selected": original_source["selected_prices"],
    }
    market_revision = canonical_integrity_checksum(revision_manifest)
    identity_inputs = {
        **event.payload["identity_inputs"],
        "market_data_revision": market_revision,
    }
    outcome_id = "sha256:" + hashlib.sha256(
        json.dumps(
            identity_inputs,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    payload = {
        **event.payload,
        "outcome_id": outcome_id,
        "identity_inputs": identity_inputs,
        "market_data_revision": market_revision,
    }
    source_record = {
        **original_source,
        "calendar_id": calendar_manifest["calendar_id"],
        "calendar_manifest": calendar_manifest,
        "calendar_manifest_checksum": canonical_integrity_checksum(
            calendar_manifest
        ),
        "market_data_revision": market_revision,
        "identity_inputs": identity_inputs,
        "payload_checksum": canonical_integrity_checksum(payload),
    }
    with pytest.raises(
        LearningEventError,
        match="fixture calendar authority not allowlisted",
    ):
        forged = make_learning_event(
            kind=event.kind,
            tenant_id=event.tenant_id,
            entity_id=outcome_id,
            revision=event.revision,
            event_time=event.event_time,
            available_time=event.available_time,
            as_of_time=event.as_of_time,
            payload=payload,
            provenance={
                **event.provenance,
                "source_record": source_record,
                "checksum": canonical_integrity_checksum(source_record),
            },
        )
        validate_canonical_delayed_outcome(forged)


@pytest.mark.parametrize(
    "mutation",
    [
        {"source": "forged-labeler"},
        {"unexpected": "forged"},
        {"source_record": {"payload_checksum": "sha256:" + "f" * 64}},
    ],
)
def test_public_validator_rejects_provenance_mutation(mutation):
    event = _build()
    if "source_record" in mutation:
        source_record = {
            **event.provenance["source_record"],
            **mutation["source_record"],
        }
        mutation = {"source_record": source_record}
    with pytest.raises(LearningEventError):
        forged = replace(event, provenance={**event.provenance, **mutation})
        validate_canonical_delayed_outcome(forged)


def test_public_validator_rejects_forged_provenance_source_identity():
    event = _build()
    source_record = {
        **event.provenance["source_record"],
        "analysis_identity": "forged-analysis",
    }
    forged = replace(
        event,
        provenance={
            **event.provenance,
            "source_record": source_record,
            "checksum": canonical_integrity_checksum(source_record),
        },
    )
    with pytest.raises(LearningEventError, match="emission contract"):
        validate_canonical_delayed_outcome(forged)


def test_public_validator_rejects_semantic_provenance_mutation_after_rechecksum():
    event = _build()
    source_record = {
        **event.provenance["source_record"],
        "analysis_identity": "forged-analysis",
    }
    forged = replace(
        event,
        provenance={
            **event.provenance,
            "source_record": source_record,
            "checksum": canonical_integrity_checksum(source_record),
        },
    )
    with pytest.raises(LearningEventError, match="emission contract"):
        validate_canonical_delayed_outcome(forged)


@pytest.mark.parametrize("bad_status", ["conflict", "error", "unknown", None])
def test_ledger_rejects_unconfirmed_append_status_without_state_mutation(bad_status):
    class StatusPort:
        def __init__(self):
            self.first = True
            self.log = LearningEventAppendLog()

        def append(self, event):
            if self.first:
                self.first = False
                return bad_status
            return self.log.append(event)

    port = StatusPort()
    ledger = FixtureOutcomeLedger(append=port)
    with pytest.raises(LearningEventError, match="did not confirm"):
        _build(ledger=ledger)
    created = _build(ledger=ledger)
    assert created.revision == 1
    assert len(port.log.replay()) == 1


def test_fixture_only_authority_and_calendar_gaps_fail_closed():
    with pytest.raises(LearningEventError, match="trusted tenant"):
        FixtureOutcomeLedger(append=LearningEventAppendLog()).observe(
            _analysis(), trusted_tenant_id="tenant-b",
            trusted_as_of_time="2026-07-02T23:59:00Z",
            trusted_labeled_at="2026-07-02T23:59:00Z", calendar=_calendar(),
            market_data=FixtureMarketData(()), horizon="T+1",
            market_data_variant="as_first_known",
        )


def test_calendar_timezone_requires_iana_registry_identity():
    utc_calendar = replace(_calendar(), timezone="UTC")
    assert utc_calendar.timezone == "UTC"
    with pytest.raises(LearningEventError, match="valid IANA"):
        replace(_calendar(), timezone="Not/A_Real_Zone")
    after_target = replace(
        _calendar(),
        sessions=_calendar().sessions + (VenueSession("2026-07-21", "unknown", None),),
    )
    event = FixtureOutcomeLedger(append=LearningEventAppendLog()).observe(
        _analysis(), trusted_tenant_id="tenant-a",
        trusted_as_of_time="2026-07-03T00:00:00Z",
        trusted_labeled_at="2026-07-03T00:00:00Z", calendar=after_target,
        market_data=FixtureMarketData(()), horizon="T+1",
        market_data_variant="as_first_known",
    )
    assert event.payload["target_session"] == "2026-07-02"

    sessions = list(_calendar().sessions)
    sessions[1] = VenueSession("2026-07-02", "unknown", None)
    gap = replace(_calendar(), sessions=tuple(sessions))
    gap_event = FixtureOutcomeLedger(append=LearningEventAppendLog()).observe(
            _analysis(), trusted_tenant_id="tenant-a",
            trusted_as_of_time="2026-07-02T23:59:00Z",
            trusted_labeled_at="2026-07-02T23:59:00Z", calendar=gap,
            market_data=FixtureMarketData(()), horizon="T+1",
            market_data_variant="as_first_known",
        )
    assert gap_event.payload["maturity"] == "unavailable"
    assert gap_event.payload["reason_code"] == "CALENDAR_GAP"


def test_24_7_calendar_requires_contiguous_utc_daily_sessions():
    valid = FixtureVenueCalendar(
        calendar_id="crypto:UTC:fixture-v1", timezone="UTC",
        version_available_at="2026-06-01T00:00:00Z", continuous_24_7=True,
        prediction_cutoff_minutes=5, publication_lag_hours=1,
        sessions=(
            VenueSession("2026-07-01", "open", "2026-07-02T00:00:00Z"),
            VenueSession("2026-07-02", "open", "2026-07-03T00:00:00Z"),
        ),
    )
    assert valid.continuous_24_7 is True
    with pytest.raises(LearningEventError, match="timezone must be UTC"):
        replace(valid, timezone="Asia/Taipei")
    with pytest.raises(LearningEventError, match="must all be open"):
        replace(
            valid,
            sessions=(
                valid.sessions[0],
                VenueSession("2026-07-02", "closed", None),
            ),
        )
    with pytest.raises(LearningEventError, match="next UTC midnight"):
        replace(
            valid,
            sessions=(
                VenueSession("2026-07-01", "open", "2026-07-01T23:59:59Z"),
                valid.sessions[1],
            ),
        )
    with pytest.raises(LearningEventError, match="must be daily"):
        replace(
            valid,
            sessions=(
                valid.sessions[0],
                VenueSession("2026-07-03", "open", "2026-07-04T00:00:00Z"),
            ),
        )


def test_fixture_record_limits_fail_before_unbounded_processing():
    session = VenueSession("2026-07-01", "open", "2026-07-01T20:00:00Z")
    with pytest.raises(LearningEventError, match="session limit"):
        replace(_calendar(), sessions=(session,) * 10_001)
    price = _price("2026-07-01", "1.00000000", "2026-07-01T21:00:00Z")
    with pytest.raises(LearningEventError, match="price limit"):
        FixtureMarketData((price,) * 10_001)
    oversized = replace(
        price,
        provider="x" * 4000,
        content_hash="sha256:" + "0" * 64,
    )
    with pytest.raises(LearningEventError, match="aggregate byte limit"):
        FixtureMarketData((oversized,) * 300)
    with pytest.raises(LearningEventError, match="calendar calendar_id is invalid"):
        replace(_calendar(), calendar_id="x" * 4097)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"label": "x" * 33}, "label is invalid"),
        ({"status": "x" * 17}, "status is invalid"),
        ({"scheduled_close_at": "x" * 4097}, "scheduled_close_at is invalid"),
        ({"scheduled_close_at": 123}, "scheduled_close_at is invalid"),
    ],
)
def test_venue_session_fields_fail_before_calendar_parse(kwargs, message):
    values = {
        "label": "2026-07-01",
        "status": "open",
        "scheduled_close_at": "2026-07-01T20:00:00Z",
        **kwargs,
    }
    with pytest.raises(LearningEventError, match=message):
        VenueSession(**values)


def test_calendar_rejects_venue_session_subclass():
    class DerivedSession(VenueSession):
        pass

    derived = DerivedSession(
        "2026-07-01",
        "open",
        "2026-07-01T20:00:00Z",
    )
    with pytest.raises(LearningEventError, match="exact VenueSession"):
        replace(_calendar(), sessions=(derived,))


def test_calendar_streaming_canonical_budget_near_and_over_boundary():
    base = date(2020, 1, 1)

    def sessions(count):
        return tuple(
            VenueSession(
                (base + timedelta(days=index)).isoformat(),
                "open",
                (base + timedelta(days=index)).isoformat()
                + "T20:00:00."
                + "0" * 4000
                + "Z",
            )
            for index in range(count)
        )

    near = replace(_calendar(), sessions=sessions(250))
    assert len(near.sessions) == 250
    with pytest.raises(LearningEventError, match="aggregate byte limit"):
        replace(_calendar(), sessions=sessions(260))


def test_price_content_hash_requires_full_sha256_digest():
    bad = _price("2026-07-01", "1.00000000", "2026-07-01T21:00:00Z")
    bad = replace(bad, content_hash="sha256:not-a-digest")
    with pytest.raises(LearningEventError, match="PRICE_LINEAGE_MISSING"):
        _build(
            data=[
                bad,
                _price("2026-07-02", "2.00000000", "2026-07-02T21:00:00Z"),
            ]
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"content_hash": "sha256:short"}, "PRICE_LINEAGE_MISSING"),
        (
            {
                "event_at": "2026-07-01T22:00:00Z",
                "available_at": "2026-07-01T21:00:00Z",
            },
            "price timeline is invalid",
        ),
        ({"event_at": "2026-07-01T19:00:00Z"}, "calendar sessions"),
        ({"adjusted_close": "not-a-price"}, "price is invalid"),
    ],
)
def test_pending_rejects_invalid_known_start_price_before_manifest(mutation, message):
    start = _price(
        "2026-07-01",
        "100.00000000",
        "2026-07-01T21:00:00Z",
    )
    start = replace(start, **mutation)
    if "content_hash" not in mutation:
        start = replace(
            start,
            content_hash=canonical_fixture_price_content_hash(start),
        )
    with pytest.raises(LearningEventError, match=message):
        _build(
            as_of="2026-07-02T22:00:00Z",
            labeled_at="2026-07-02T22:00:00Z",
            data=[start],
        )
