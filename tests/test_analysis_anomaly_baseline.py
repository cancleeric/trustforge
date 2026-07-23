from copy import deepcopy

from trustforge.analysis_anomaly_baseline import build_quality_anomaly_diagnostic
from trustforge.analysis_quality_event import build_analysis_quality_event


def _event(index, *, confidence=0.6, missingness=0.0, source_concentration=0.2, evidence=3):
    return build_analysis_quality_event(
        {
            "analysis_id": f"an-{index}",
            "tenant_id": "tenant-a",
            "coin": "BTC",
            "mode": "formal",
            "question_type": "direction",
            "event_time": f"2026-07-{index:02d}T00:00:00Z",
            "available_time": f"2026-07-{index:02d}T00:00:01Z",
            "as_of_time": f"2026-07-{index:02d}T00:00:00Z",
            "source_available_times": [f"2026-07-{index:02d}T00:00:00Z"],
            "provenance": {"source": "analysis-flow", "collector": "unit-test", "observed_at": f"2026-07-{index:02d}T00:00:01Z"},
            "confidence": {"raw": confidence, "calibrated": confidence},
            "decision": {"direction": "bullish", "abstain": False},
            "evidence_stats": {
                "supporting": evidence,
                "contrarian": 0,
                "missingness": missingness,
                "source_concentration": source_concentration,
            },
            "quality": {"freshness": "ok", "conflict": "low", "completeness": "complete"},
            "versions": {"kernel": "learning-event.v1"},
            "stage_metrics": [],
        }
    )


def test_anomaly_baseline_reports_insufficient_data_for_small_sample():
    diagnostic = build_quality_anomaly_diagnostic(
        [_event(1), _event(2)],
        baseline_version="rules-v1",
        as_of_time="2026-07-10T00:00:00Z",
    )

    assert diagnostic.kind == "candidate_diagnostic"
    assert diagnostic.payload["findings"] == [{"kind": "insufficient_data", "minimum_rows": 3, "observed_rows": 2}]
    assert "activation" not in diagnostic.payload


def test_anomaly_baseline_finds_known_quality_anomalies_without_approval_surface():
    events = [
        _event(1, confidence=0.6),
        _event(2, confidence=0.61),
        _event(3, confidence=0.62),
        _event(4, confidence=0.99, missingness=0.9, source_concentration=0.95, evidence=0),
    ]

    diagnostic = build_quality_anomaly_diagnostic(
        events,
        baseline_version="rules-v1",
        as_of_time="2026-07-10T00:00:00Z",
    )

    kinds = {finding["kind"] for finding in diagnostic.payload["findings"]}
    assert {"confidence_drift", "evidence_missingness", "source_concentration", "evidence_absent"} <= kinds
    assert "approval_action" not in diagnostic.payload


def test_anomaly_baseline_has_stable_versioned_checksum_and_rerun_identity():
    events = [_event(1), _event(2), _event(3)]
    first = build_quality_anomaly_diagnostic(deepcopy(events), baseline_version="rules-v1", as_of_time="2026-07-10T00:00:00Z")
    second = build_quality_anomaly_diagnostic(reversed(events), baseline_version="rules-v1", as_of_time="2026-07-10T00:00:00Z")

    assert first.identity == second.identity
    assert first.payload["baseline_sha256"] == second.payload["baseline_sha256"]
    assert first.payload["reproducible_query"]["baseline_version"] == "rules-v1"
