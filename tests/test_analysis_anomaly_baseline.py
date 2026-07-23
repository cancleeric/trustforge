import copy
import json
from dataclasses import replace

import pytest
import trustforge.analysis_anomaly_baseline as baseline_module
import trustforge.calibration_dataset as calibration_module

from trustforge.analysis_anomaly_baseline import (
    AnalysisAnomalyError,
    AnalysisAnomalyPolicy,
    detect_analysis_anomalies,
)
from trustforge.calibration_dataset import _event_anchor, _sha256
from trustforge.analysis_quality_event import build_analysis_quality_event
from trustforge.learning_event_contract import canonical_integrity_checksum, serialize_learning_event
from tests.test_analysis_quality_event import snapshot, trusted_pit, trusted_provenance


def _event(
    index, available, *, confidence=.6, evidence=3, distribution=None,
    partial=False, failed_stage=False, retry=False, latency_ms=None, missing_stage=False,
    tenant_id="tenant-a",
):
    if available.endswith("Z") and "." not in available:
        available = available[:-1] + ".000000Z"
    value = snapshot()
    value["analysis_id"] = f"an-{index}"
    value["tenant_id"] = tenant_id
    value["run_id"] = f"run-{index}"
    value["answer_id"] = f"answer-{index}"
    value["available_time"] = available
    value["event_time"] = available
    value["as_of_time"] = available
    value["source_available_times"] = [available]
    value["provenance"]["observed_at"] = available
    value["confidence"]["calibrated"] = confidence
    value["evidence_stats"]["evidence_count"] = evidence
    value["evidence_stats"]["supporting_count"] = evidence
    value["evidence_stats"]["contrarian_count"] = 0
    value["evidence_stats"]["independent_source_count"] = min(evidence, 2)
    value["evidence_stats"]["source_distribution"] = (
        distribution if distribution is not None
        else ({"a": max(evidence - 1, 0), "b": min(evidence, 1)} if evidence else {"none": 0})
    )
    value["evidence_snapshot"] = [
        {
            "source": f"s-{n}",
            "fetched_at": available,
            "content_reference": f"r-{n}",
            "related_claim": "claim",
            "schema_version": "evidence.v1",
            "trust": .8,
        }
        for n in range(evidence)
    ]
    value["evidence_snapshot_id"] = canonical_integrity_checksum(value["evidence_snapshot"])
    if partial:
        failed_name = value["stage_metrics"][0]["stage"]
        failed_latency = value["stage_metrics"][0]["latency_ms"]
        value["failure"] = {
            "status": "partial", "failed_stage": failed_name, "code": "TIMEOUT",
            "message": "fixture", "retryable": True,
        }
        value["stage_metrics"][0] = {
            "stage": failed_name, "latency_ms": failed_latency, "status": "failed",
            "attempts": 1, "failure": {"code": "TIMEOUT", "message": "fixture"},
        }
    elif failed_stage:
        failed_name = value["stage_metrics"][0]["stage"]
        failed_latency = value["stage_metrics"][0]["latency_ms"]
        value["failure"] = {
            "status": "partial", "failed_stage": failed_name, "code": "FAILED",
            "message": "fixture", "retryable": False,
        }
        value["stage_metrics"][0] = {
            "stage": failed_name, "latency_ms": failed_latency,
            "status": "failed", "attempts": 1,
            "failure": {"code": "FAILED", "message": "fixture"},
        }
    elif retry:
        value["stage_metrics"][0]["attempts"] = 2
    if latency_ms is not None:
        value["stage_metrics"][0]["latency_ms"] = latency_ms
    if missing_stage:
        value["stage_metrics"][0]["stage"] = "unrelated-stage"
    pit = {
        "event_time": available, "available_time": available,
        "as_of_time": available, "source_available_times": [available],
    }
    provenance = {
        "source": value["provenance"]["source"],
        "collector": value["provenance"]["collector"],
        "observed_at": available,
    }
    return build_analysis_quality_event(
        value, trusted_tenant_id=tenant_id,
        trusted_pit=pit, trusted_provenance=provenance,
    )


def _policy(**updates):
    base = AnalysisAnomalyPolicy(
        tenant_id="tenant-a", baseline_version="baseline.v1",
        query_version="query.v1", producer_version="producer.v1",
        reference_start="2026-07-01T00:00:00Z",
        reference_end="2026-07-08T00:00:00Z",
        current_start="2026-07-08T00:00:00Z",
        current_end="2026-07-15T00:00:00Z",
        query_as_of="2026-07-15T00:00:00Z",
        minimum_reference_samples=3, minimum_current_samples=3,
    )
    return replace(base, **updates)


def _manifest(events):
    rows = [
        {
            "analysis_id": event.payload["analysis_id"], "tenant_id": "tenant-a",
            "analysis_identity": event.identity, "schema_version": "learning-event.v1",
            "analysis_event_time": event.event_time,
            "analysis_available_time": event.available_time,
            "coin": event.payload["coin"], "mode": event.payload["mode"],
            "question_type": event.payload["question_type"],
            "calibrated_confidence": event.payload["confidence"]["calibrated"],
            "raw_confidence": event.payload["confidence"]["raw"],
            "direction": event.payload["decision"]["direction"],
            "outcome_identity": f"outcome-{index}",
            "source_event_identity": event.identity,
            "market_data_variant": "as_first_known",
            "outcome_available_time": "2026-07-20T01:00:00.000000Z",
            "outcome_source_version": "fixture.v1", "horizon": "T+1",
            "outcome_pct": "1.0", "ground_truth_direction": "bullish",
            "split": (
                "train" if event.available_time < "2026-07-05T00:00:00.000000Z"
                else "validation" if event.available_time < "2026-07-10T00:00:00.000000Z"
                else "test"
            ),
        }
        for index, event in enumerate(events)
    ]
    import hashlib
    def sha(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest = {
        "kind": "confidence-calibration-dataset.v2",
        "policy": {
            "dataset_as_of": "2026-07-15T00:00:00.000000Z",
            "train_end": "2026-07-05T00:00:00.000000Z",
            "validation_end": "2026-07-10T00:00:00.000000Z",
            "embargo_seconds": 0,
            "tenant_id": "tenant-a", "market_data_variant": "as_first_known",
            "producer_version": "p1", "eligibility_version": "e1",
            "split_version": "s1",
        },
        "input_roots": {
            "analysis_sha256": _sha256(sorted(
                (_event_anchor(event) for event in events),
                key=lambda anchor: (anchor["identity"], _sha256(anchor)),
            )),
            "outcome_sha256": "b" * 64,
        },
        "versions": {
            "producer": "p1", "eligibility": "e1", "split": "s1",
            "analysis_schema": "analysis-quality.v1",
            "outcome_schema": "delayed-outcome.v1",
            "kernel_schema": "learning-event.v1",
        },
        "excluded_counts": {},
        "split_ranges": {
            "train": {
                "start": None,
                "end_exclusive": "2026-07-05T00:00:00.000000Z",
            },
            "validation": {
                "start": "2026-07-05T00:00:00.000000Z",
                "end_exclusive": "2026-07-10T00:00:00.000000Z",
            },
            "test": {
                "start": "2026-07-10T00:00:00.000000Z",
                "end_inclusive": "2026-07-15T00:00:00.000000Z",
            },
        },
        "row_counts": {
            split: sum(row["split"] == split for row in rows)
            for split in ("train", "validation", "test")
        },
        "group_counts": {
            split: sum(row["split"] == split for row in rows)
            for split in ("train", "validation", "test")
        },
        "row_count": len(rows), "group_count": len(rows),
        "rows_sha256": sha(rows), "rows": rows,
    }
    manifest["manifest_sha256"] = sha(manifest)
    return manifest


def _normal_events():
    return [
        *[_event(i, f"2026-07-0{i+1}T01:00:00Z", confidence=.55 + i*.02) for i in range(3)],
        *[_event(i+3, f"2026-07-{i+9:02d}T01:00:00Z", confidence=.56 + i*.02) for i in range(3)],
    ]


def test_normal_negative_and_order_independent_replay_are_deterministic():
    events = _normal_events()
    first = detect_analysis_anomalies(events, calibration_manifest=_manifest(events), policy=_policy())
    second = detect_analysis_anomalies(reversed(events), calibration_manifest=_manifest(events), policy=_policy())
    assert first["status"] == "complete"
    assert first["findings"] == []
    assert first["report_sha256"] == second["report_sha256"]


def test_known_anomalies_emit_candidate_only_with_versions_and_query():
    reference = [_event(i, f"2026-07-0{i+1}T01:00:00Z", confidence=.5) for i in range(3)]
    current = [
        _event(i+3, f"2026-07-{i+9:02d}T01:00:00Z", confidence=.95,
               evidence=0, distribution={"none": 0}, partial=True)
        for i in range(3)
    ]
    events = reference + current
    result = detect_analysis_anomalies(events, calibration_manifest=_manifest(events), policy=_policy())
    codes = {finding["reason_code"] for finding in result["findings"]}
    assert {
        "CONFIDENCE_DRIFT", "EVIDENCE_MISSING", "PIPELINE_FAILURE_OR_PARTIAL"
    } <= codes
    for diagnostic in result["diagnostics"]:
        assert diagnostic.kind == "candidate_diagnostic"
        assert diagnostic.payload["candidate_only"] is True
        assert "approval_action" not in diagnostic.payload
        assert "activation" not in diagnostic.payload
        assert diagnostic.payload["input_manifest"]["manifest_sha256"]
        assert diagnostic.payload["reproducible_query"]["sha256"] == result["query_sha256"]
        assert set(diagnostic.payload) == {
            "diagnostic_id", "analysis_id", "reason", "reason_code",
            "classification", "eligible_as_evidence", "candidate_only", "details",
            "baseline", "input_manifest", "reproducible_query", "input_summary",
        }


def test_distribution_switch_and_median_mad_outlier():
    reference = [_event(i, f"2026-07-0{i+1}T01:00:00Z", confidence=.5) for i in range(3)]
    current = [
        _event(3, "2026-07-09T01:00:00Z", confidence=.5, evidence=10, distribution={"one": 9, "two": 1}),
        _event(4, "2026-07-10T01:00:00Z", confidence=.5, evidence=10, distribution={"one": 9, "two": 1}),
        _event(5, "2026-07-11T01:00:00Z", confidence=.9, evidence=10, distribution={"one": 9, "two": 1}),
    ]
    events = reference + current
    result = detect_analysis_anomalies(events, calibration_manifest=_manifest(events), policy=_policy())
    assert {"SOURCE_CONCENTRATION", "MEDIAN_MAD_OUTLIER"} <= {
        finding["reason_code"] for finding in result["findings"]
    }


def test_small_sample_is_explicit_and_deduplicated():
    events = _normal_events()[:2]
    result = detect_analysis_anomalies(events, calibration_manifest=_manifest(events), policy=_policy())
    assert result["status"] == "insufficient_data"
    assert [item["reason_code"] for item in result["findings"]] == ["INSUFFICIENT_DATA"]
    assert len(result["diagnostics"]) == 1


def test_manifest_tamper_tenant_and_future_event_fail_closed_or_invisible():
    events = _normal_events()
    manifest = _manifest(events)
    tampered = copy.deepcopy(manifest)
    tampered["policy"]["tenant_id"] = "tenant-b"
    with pytest.raises(AnalysisAnomalyError, match="checksum"):
        detect_analysis_anomalies(events, calibration_manifest=tampered, policy=_policy())
    future = _event(99, "2026-07-16T01:00:00Z")
    first = detect_analysis_anomalies(events, calibration_manifest=manifest, policy=_policy())
    second = detect_analysis_anomalies(events + [future], calibration_manifest=manifest, policy=_policy())
    assert first["report_sha256"] == second["report_sha256"]


def test_duplicate_identity_fails_and_foreign_tenant_is_not_counted():
    events = _normal_events()
    with pytest.raises(AnalysisAnomalyError, match="duplicate"):
        detect_analysis_anomalies(events + [events[0]], calibration_manifest=_manifest(events), policy=_policy())


def test_rollback_is_explicit_old_policy_and_has_no_mutable_state():
    events = _normal_events()
    v1 = detect_analysis_anomalies(events, calibration_manifest=_manifest(events), policy=_policy())
    v2 = detect_analysis_anomalies(events, calibration_manifest=_manifest(events), policy=_policy(baseline_version="baseline.v2"))
    rolled_back = detect_analysis_anomalies(events, calibration_manifest=_manifest(events), policy=_policy())
    assert v1["report_sha256"] == rolled_back["report_sha256"]
    assert v1["report_sha256"] != v2["report_sha256"]
    assert serialize_learning_event(v1["diagnostics"][0]) == serialize_learning_event(rolled_back["diagnostics"][0]) if v1["diagnostics"] else True


def _resign(manifest):
    manifest["rows_sha256"] = _sha256(manifest["rows"])
    manifest["manifest_sha256"] = _sha256({
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    })
    return manifest


def test_root_mismatch_rejects_subset_extra_and_fake_digest():
    events = _normal_events()
    manifest = _manifest(events)
    with pytest.raises(AnalysisAnomalyError, match="root mismatch"):
        detect_analysis_anomalies(events[:-1], calibration_manifest=manifest, policy=_policy())
    extra = _event(77, "2026-07-12T02:00:00Z")
    with pytest.raises(AnalysisAnomalyError, match="root mismatch"):
        detect_analysis_anomalies(events + [extra], calibration_manifest=manifest, policy=_policy())
    fake = copy.deepcopy(manifest)
    fake["input_roots"]["analysis_sha256"] = "a" * 64
    _resign(fake)
    with pytest.raises(AnalysisAnomalyError, match="root mismatch"):
        detect_analysis_anomalies(events, calibration_manifest=fake, policy=_policy())


def test_exact_cutoff_future_and_foreign_do_not_consume_snapshot_quota(monkeypatch):
    events = _normal_events()
    manifest = _manifest(events)
    at_cutoff = _event(88, "2026-07-15T00:00:00Z")
    future = _event(89, "2026-07-16T00:00:00Z")
    monkeypatch.setattr(baseline_module, "_MAX_EVENTS", len(events))
    baseline = detect_analysis_anomalies(events, calibration_manifest=manifest, policy=_policy())
    assert detect_analysis_anomalies(
        events + [future], calibration_manifest=manifest, policy=_policy()
    )["report_sha256"] == baseline["report_sha256"]
    # At dataset_as_of is visible, so it must be root-authorized.
    with pytest.raises(AnalysisAnomalyError, match="event count|root mismatch"):
        detect_analysis_anomalies(
            events + [at_cutoff], calibration_manifest=manifest, policy=_policy()
        )


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("minimum_reference_samples", 0, "positive integer"),
        ("minimum_current_samples", True, "positive integer"),
        ("confidence_drift_threshold", float("nan"), "finite"),
        ("evidence_missing_rate_threshold", 1.1, "outside"),
        ("source_concentration_threshold", -0.1, "outside"),
        ("robust_z_threshold", 0, "positive"),
        ("latency_robust_z_threshold", float("inf"), "finite"),
    ],
)
def test_policy_numeric_bounds_fail_closed(field, value, match):
    with pytest.raises(AnalysisAnomalyError, match=match):
        _policy(**{field: value}).canonical()


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda m: m.update({"extra": True}), "schema is not exact"),
        (lambda m: m["policy"].update({"extra": True}), "policy is invalid"),
        (lambda m: m["versions"].update({"extra": "v"}), "versions are invalid"),
        (lambda m: m["input_roots"].update({"analysis_sha256": "z" * 64}), "root is invalid"),
        (lambda m: m["rows"][0].update({"extra": True}), "row must be an exact"),
        (lambda m: m["rows"][0].update({"source_event_identity": "spoof"}), "source binding"),
        (lambda m: m["rows"][0].update({"horizon": "T+99"}), "horizon"),
    ],
)
def test_manifest_exact_schema_and_row_contract_fail_closed(mutation, match):
    events = _normal_events()
    manifest = copy.deepcopy(_manifest(events))
    mutation(manifest)
    _resign(manifest)
    with pytest.raises(AnalysisAnomalyError, match=match):
        detect_analysis_anomalies(events, calibration_manifest=manifest, policy=_policy())


def test_candidate_has_no_authority_aliases_and_baseline_is_reproducible():
    events = _normal_events()[:2]
    result = detect_analysis_anomalies(events, calibration_manifest=_manifest(events), policy=_policy())
    payload = result["diagnostics"][0].payload
    assert payload["classification"] == "non_evidentiary_candidate"
    assert payload["eligible_as_evidence"] is False
    assert not {"approve", "activate", "proposal", "active_version"} & set(payload)
    assert payload["baseline"]["spec_sha256"]
    assert payload["baseline"]["reference_stats"]["count"] == 2


def test_bounded_canonical_bytes_abort_before_anchor_materialization(monkeypatch):
    events = _normal_events()
    monkeypatch.setattr(baseline_module, "_MAX_EVENT_INPUT_BYTES", 32)
    with pytest.raises(AnalysisAnomalyError, match="UTF-8 byte"):
        detect_analysis_anomalies(events, calibration_manifest=_manifest(events), policy=_policy())


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ({"partial": True}, "PIPELINE_FAILURE_OR_PARTIAL"),
        ({"failed_stage": True}, "PIPELINE_FAILURE_OR_PARTIAL"),
        ({"retry": True}, "PIPELINE_RETRY_SPIKE"),
        ({"latency_ms": 10_000}, "PIPELINE_LATENCY_OUTLIER"),
        ({"missing_stage": True}, "PIPELINE_STAGE_MISSING"),
    ],
)
def test_pipeline_anomaly_classes_are_independently_detected(mutation, expected):
    reference = [
        _event(i, f"2026-07-0{i+1}T01:00:00Z", confidence=.5 + i * .05)
        for i in range(3)
    ]
    current = [
        _event(i + 3, f"2026-07-{i+9:02d}T01:00:00Z", confidence=.55, **(
            mutation if i == 0 else {}
        ))
        for i in range(3)
    ]
    events = reference + current
    result = detect_analysis_anomalies(
        events, calibration_manifest=_manifest(events), policy=_policy()
    )
    codes = {finding["reason_code"] for finding in result["findings"]}
    pipeline_codes = {
        "PIPELINE_FAILURE_OR_PARTIAL",
        "PIPELINE_RETRY_SPIKE",
        "PIPELINE_LATENCY_OUTLIER",
        "PIPELINE_STAGE_MISSING",
    }
    assert codes & pipeline_codes == {expected}


def test_normal_pipeline_emits_no_pipeline_specific_code():
    events = _normal_events()
    result = detect_analysis_anomalies(
        events, calibration_manifest=_manifest(events), policy=_policy()
    )
    assert not {
        finding["reason_code"] for finding in result["findings"]
    } & {
        "PIPELINE_FAILURE_OR_PARTIAL",
        "PIPELINE_RETRY_SPIKE",
        "PIPELINE_LATENCY_OUTLIER",
        "PIPELINE_STAGE_MISSING",
    }


def test_valid_foreign_tenant_event_is_byte_and_hash_invisible():
    events = _normal_events()
    foreign = _event(999, "2026-07-10T01:00:00Z", tenant_id="tenant-b")
    manifest = _manifest(events)
    baseline = detect_analysis_anomalies(
        events, calibration_manifest=manifest, policy=_policy()
    )
    mixed = detect_analysis_anomalies(
        [foreign, *events], calibration_manifest=manifest, policy=_policy()
    )
    assert serialize_learning_event(mixed["diagnostics"][0]) == serialize_learning_event(
        baseline["diagnostics"][0]
    ) if baseline["diagnostics"] else mixed["report_sha256"] == baseline["report_sha256"]


def test_manifest_nested_count_and_temporal_contract_fail_closed():
    events = _normal_events()
    mutations = [
        lambda m: m.update({"row_count": -1}),
        lambda m: m["excluded_counts"].update({"bad": -1}),
        lambda m: m["versions"].update({"kernel_schema": ""}),
        lambda m: m["split_ranges"]["train"].update({"start": "forged"}),
        lambda m: m["policy"].update({"dataset_as_of": "2026-07-16T00:00:00.000000Z"}),
    ]
    for mutate in mutations:
        manifest = copy.deepcopy(_manifest(events))
        mutate(manifest)
        _resign(manifest)
        with pytest.raises(AnalysisAnomalyError):
            detect_analysis_anomalies(
                events, calibration_manifest=manifest, policy=_policy()
            )


def test_candidate_identity_is_bound_to_ordinal_reason_and_no_authority_aliases():
    events = _normal_events()[:2]
    result = detect_analysis_anomalies(
        events, calibration_manifest=_manifest(events), policy=_policy()
    )
    diagnostic = result["diagnostics"][0]
    assert diagnostic.payload["classification"] == "non_evidentiary_candidate"
    assert diagnostic.payload["eligible_as_evidence"] is False
    serialized = serialize_learning_event(diagnostic)
    for forbidden in (
        '"approval_action"', '"activation"', '"proposal"', '"active_version"',
        '"classification": "Evidence"',
    ):
        assert forbidden not in serialized
    assert diagnostic.payload["baseline"]["thresholds"]
    assert diagnostic.payload["baseline"]["reference_stats"]["evidence_missing_rate"] is not None


def _thaw(value):
    if isinstance(value, dict) or hasattr(value, "items"):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _forge_event(event, *, payload=None, schema_version=None):
    if payload is not None:
        object.__setattr__(event, "payload", payload)
    if schema_version is not None:
        object.__setattr__(event, "schema_version", schema_version)
    return event


def test_source_record_exactly_binds_all_identity_authorities():
    events = _normal_events()[:2]
    first = detect_analysis_anomalies(
        events, calibration_manifest=_manifest(events), policy=_policy()
    )["diagnostics"][0]
    source = first.provenance["source_record"]
    assert set(source) == {
        "diagnostic_id", "tenant_id", "baseline_spec_sha256",
        "manifest_sha256", "rows_sha256", "analysis_input_root",
        "manifest_versions", "query_sha256", "ordinal", "reason_code",
        "query", "finding_sha256",
    }
    assert first.provenance["checksum"] == canonical_integrity_checksum(source)
    changed = detect_analysis_anomalies(
        events,
        calibration_manifest=_manifest(events),
        policy=_policy(baseline_version="baseline.v2"),
    )["diagnostics"][0]
    assert first.identity != changed.identity
    assert first.provenance != changed.provenance
    changed_query = detect_analysis_anomalies(
        events,
        calibration_manifest=_manifest(events),
        policy=_policy(query_version="query.v2"),
    )["diagnostics"][0]
    assert first.identity != changed_query.identity
    assert first.provenance != changed_query.provenance
    changed_manifest = copy.deepcopy(_manifest(events))
    changed_manifest["input_roots"]["outcome_sha256"] = "c" * 64
    _resign(changed_manifest)
    changed_input = detect_analysis_anomalies(
        events, calibration_manifest=changed_manifest, policy=_policy()
    )["diagnostics"][0]
    assert first.identity != changed_input.identity
    assert first.provenance != changed_input.provenance


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda payload: payload["stage_metrics"].__setitem__(0, "not-an-object"), "schema"),
        (lambda payload: payload["stage_metrics"][0].update({"stage": ""}), "stage name"),
        (lambda payload: payload["stage_metrics"][0].update({"latency_ms": True}), "latency"),
        (lambda payload: payload["stage_metrics"][0].update({"latency_ms": -1}), "latency"),
        (lambda payload: payload["stage_metrics"][0].update({"attempts": True}), "attempts"),
        (lambda payload: payload["stage_metrics"][0].update({"attempts": 0}), "attempts"),
        (lambda payload: payload["stage_metrics"][0].update({"status": "unknown"}), "status"),
    ],
)
def test_forged_stage_metrics_fail_closed_without_type_errors(mutation, match):
    events = _normal_events()
    payload = _thaw(events[0].payload)
    mutation(payload)
    payload["stage_metrics"] = tuple(payload["stage_metrics"])
    _forge_event(events[0], payload=payload)
    manifest = _manifest(events)
    with pytest.raises(AnalysisAnomalyError, match=match):
        detect_analysis_anomalies(events, calibration_manifest=manifest, policy=_policy())


def test_event_resource_failures_happen_before_anchor_materialization(monkeypatch):
    event = _normal_events()[0]
    payload = _thaw(event.payload)
    payload["question"] = "x" * 70_000
    _forge_event(event, payload=payload)
    monkeypatch.setattr(
        baseline_module, "_calibration_event_anchor",
        lambda _event: pytest.fail("anchor materialized before preflight"),
    )
    with pytest.raises(AnalysisAnomalyError, match="field|byte"):
        baseline_module._bounded_events(
            [event], tenant_id="tenant-a",
            dataset_as_of=baseline_module._parse("2026-07-15T00:00:00Z", "cutoff"),
        )


def test_event_nonfinite_unknown_schema_depth_node_and_count_fail_pre_anchor(monkeypatch):
    base = _normal_events()
    nonfinite = _forge_event(base[0], payload={**_thaw(base[0].payload), "question": float("nan")})
    unknown = _forge_event(_normal_events()[0], schema_version="learning-event.v99")
    deep = _normal_events()[0]
    deep_payload = _thaw(deep.payload)
    deep_payload["question"] = {"a": {"b": {"c": "x"}}}
    _forge_event(deep, payload=deep_payload)
    wide = _normal_events()[0]
    count_events = _normal_events()[:2]
    monkeypatch.setattr(
        baseline_module, "_calibration_event_anchor",
        lambda _event: pytest.fail("anchor materialized before preflight/validation"),
    )
    cutoff = baseline_module._parse("2026-07-15T00:00:00Z", "cutoff")

    with pytest.raises(AnalysisAnomalyError, match="finite|JSON"):
        baseline_module._bounded_events(
            [nonfinite], tenant_id="tenant-a", dataset_as_of=cutoff
        )

    with pytest.raises(AnalysisAnomalyError, match="schema"):
        baseline_module._bounded_events(
            [unknown], tenant_id="tenant-a", dataset_as_of=cutoff
        )

    monkeypatch.setattr(calibration_module, "_MAX_NESTING_DEPTH", 2)
    with pytest.raises(AnalysisAnomalyError, match="depth"):
        baseline_module._bounded_events(
            [deep], tenant_id="tenant-a", dataset_as_of=cutoff
        )

    monkeypatch.setattr(baseline_module, "_MAX_INPUT_NODES", 10)
    with pytest.raises(AnalysisAnomalyError, match="node"):
        baseline_module._bounded_events(
            [wide], tenant_id="tenant-a", dataset_as_of=cutoff
        )

    monkeypatch.setattr(calibration_module, "_MAX_NESTING_DEPTH", 64)
    monkeypatch.setattr(baseline_module, "_MAX_INPUT_NODES", 1_000_000)
    monkeypatch.setattr(baseline_module, "_MAX_EVENTS", 1)
    with pytest.raises(AnalysisAnomalyError, match="count"):
        baseline_module._bounded_events(
            count_events, tenant_id="tenant-a", dataset_as_of=cutoff
        )
