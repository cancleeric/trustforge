import copy

import pytest

from trustforge.analysis_quality_event import (
    build_analysis_quality_event as _build_analysis_quality_event,
)
from trustforge.learning_event_contract import (
    LearningEventError,
    canonical_integrity_checksum,
    serialize_learning_event,
)


def snapshot():
    value = {
        "analysis_id": "an-1",
        "run_id": "run-1",
        "question_id": "question-1",
        "answer_id": "answer-1",
        "question": "Will BTC rise?",
        "tenant_id": "tenant-a",
        "coin": "BTC",
        "mode": "formal",
        "question_type": "direction",
        "event_time": "2026-07-01T00:00:00Z",
        "available_time": "2026-07-01T00:00:01Z",
        "as_of_time": "2026-07-01T00:00:01Z",
        "source_available_times": ["2026-06-30T23:59:59Z"],
        "provenance": {
            "source": "analysis-flow",
            "collector": "unit-test",
            "observed_at": "2026-07-01T00:00:01Z",
        },
        "confidence": {"raw": 0.7, "calibrated": 0.62},
        "decision": {"direction": "bullish", "state": "buy"},
        "evidence_stats": {
            "supporting_count": 3,
            "contrarian_count": 1,
            "evidence_count": 4,
            "average_trust": 0.81,
            "independent_source_count": 3,
            "source_distribution": {"exchange": 2, "news": 2},
        },
        "quality": {
            "freshness": "ok",
            "conflict": "low",
            "missingness": 0.0,
            "completeness": "complete",
        },
        "versions": {
            "contract": "analysis-quality.v1",
            "schema": "analysis-quality.v1",
            "kernel": "learning-event.v1",
            "scoring": "score-v1",
            "evidence": "evidence-v1",
            "prompt": "prompt-v1",
            "model": "model-v1",
            "policy": "policy-v1",
            "rule": "rule-v1",
        },
        "stage_metrics": [
            {
                "stage": "kernel",
                "latency_ms": 12,
                "status": "complete",
                "attempts": 1,
                "failure": None,
            }
        ],
        "failure": {
            "status": "complete",
            "failed_stage": None,
            "code": None,
            "message": None,
            "retryable": False,
        },
    }
    value["evidence_snapshot"] = [
        {
            "source": f"source-{index}",
            "fetched_at": "2026-06-30T23:59:59.000000Z",
            "content_reference": f"sha256:content-{index}",
            "related_claim": f"claim-{index}",
            "schema_version": "evidence.v1",
            "trust": 0.8,
        }
        for index in range(4)
    ]
    value["evidence_snapshot_id"] = canonical_integrity_checksum(
        value["evidence_snapshot"]
    )
    return value


def trusted_pit(value):
    return {
        field: copy.deepcopy(value[field])
        for field in (
            "event_time",
            "available_time",
            "as_of_time",
            "source_available_times",
        )
    }


def trusted_provenance(value):
    return copy.deepcopy(value["provenance"])


def build_analysis_quality_event(
    value,
    *,
    trusted_tenant_id,
    trusted_pit_override=None,
    trusted_provenance_override=None,
):
    return _build_analysis_quality_event(
        value,
        trusted_tenant_id=trusted_tenant_id,
        trusted_pit=trusted_pit_override or trusted_pit(value),
        trusted_provenance=(
            trusted_provenance_override or trusted_provenance(value)
        ),
    )


def test_builds_complete_immutable_canonical_event():
    event = build_analysis_quality_event(snapshot(), trusted_tenant_id="tenant-a")

    assert event.identity == "le1/tenant-a/historical_non_evidentiary/analysis-quality%3Aan-1/v1"
    assert event.payload["event_type"] == "analysis-quality.v1"
    assert event.payload["question"] == "Will BTC rise?"
    assert event.payload["answer_id"] == "answer-1"
    assert event.provenance["source_record"]["versions"]["model"] == "model-v1"
    assert event.provenance["source_record"]["pit"]["as_of_time"].endswith("Z")
    with pytest.raises(TypeError):
        event.payload["quality"]["freshness"] = "stale"


@pytest.mark.parametrize(
    ("path", "match"),
    [
        (("versions", "model"), "versions"),
        (("provenance", "source"), "provenance"),
        (("stage_metrics",), "stage_metrics"),
        (("failure", "status"), "failure"),
        (("available_time",), "available_time"),
    ],
)
def test_missing_required_schema_fails_closed(path, match):
    value = snapshot()
    pit_authority = trusted_pit(value)
    provenance_authority = trusted_provenance(value)
    if len(path) == 1:
        value.pop(path[0])
    else:
        value[path[0]].pop(path[1])

    with pytest.raises(LearningEventError, match=match):
        build_analysis_quality_event(
            value,
            trusted_tenant_id="tenant-a",
            trusted_pit_override=pit_authority,
            trusted_provenance_override=provenance_authority,
        )


def test_rejects_future_source_and_provenance_times():
    future_source = snapshot()
    future_source["source_available_times"] = ["2026-07-02T00:00:00Z"]
    future_provenance = snapshot()
    future_provenance["provenance"]["observed_at"] = "2026-07-02T00:00:00Z"

    with pytest.raises(LearningEventError, match="future source"):
        build_analysis_quality_event(future_source, trusted_tenant_id="tenant-a")
    with pytest.raises(LearningEventError, match="observed_at"):
        build_analysis_quality_event(future_provenance, trusted_tenant_id="tenant-a")


def test_trusted_tenant_is_authority_and_spoofing_fails_closed():
    spoofed = snapshot()
    spoofed["tenant_id"] = "tenant-b"

    with pytest.raises(LearningEventError, match="trusted_tenant_id"):
        build_analysis_quality_event(spoofed, trusted_tenant_id="tenant-a")
    tenant_b = snapshot()
    tenant_b.pop("tenant_id")
    event = build_analysis_quality_event(tenant_b, trusted_tenant_id="tenant-b")
    assert event.tenant_id == "tenant-b"
    assert event.provenance["tenant_id"] == "tenant-b"


def test_partial_failure_is_explicit_and_canonical():
    value = snapshot()
    value["stage_metrics"][0] = {
        "stage": "retrieval",
        "latency_ms": 500,
        "status": "failed",
        "attempts": 2,
        "failure": {"code": "timeout", "message": "provider timed out"},
    }
    value["failure"] = {
        "status": "partial",
        "failed_stage": "retrieval",
        "code": "timeout",
        "message": "provider timed out",
        "retryable": True,
    }

    first = build_analysis_quality_event(value, trusted_tenant_id="tenant-a")
    second = build_analysis_quality_event(copy.deepcopy(value), trusted_tenant_id="tenant-a")

    assert serialize_learning_event(first) == serialize_learning_event(second)
    assert first.payload["failure"]["status"] == "partial"


def test_transport_retry_metadata_cannot_change_canonical_event():
    value = snapshot()
    value["retry"] = {"attempt": 2}

    with pytest.raises(LearningEventError, match="transport retry"):
        build_analysis_quality_event(value, trusted_tenant_id="tenant-a")


@pytest.mark.parametrize(
    "missing_id",
    ["run_id", "question_id", "answer_id", "evidence_snapshot_id", "question"],
)
def test_required_analysis_references_are_nonempty(missing_id):
    value = snapshot()
    value[missing_id] = ""

    with pytest.raises(LearningEventError, match=missing_id):
        build_analysis_quality_event(value, trusted_tenant_id="tenant-a")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (("confidence", "raw"), float("nan")),
        (("confidence", "calibrated"), 1.01),
        (("quality", "missingness"), -0.01),
        (("evidence_stats", "average_trust"), float("inf")),
        (("evidence_stats", "evidence_count"), -1),
        (("evidence_stats", "supporting_count"), 1.5),
    ],
)
def test_quality_and_count_types_and_ranges_fail_closed(field, value):
    item = snapshot()
    item[field[0]][field[1]] = value

    with pytest.raises(LearningEventError):
        build_analysis_quality_event(item, trusted_tenant_id="tenant-a")


def test_source_distribution_must_match_evidence_count():
    value = snapshot()
    value["evidence_stats"]["source_distribution"]["news"] = 1

    with pytest.raises(LearningEventError, match="sum to evidence_count"):
        build_analysis_quality_event(value, trusted_tenant_id="tenant-a")


def test_equivalent_source_time_order_and_duplicates_have_identical_bytes():
    first = snapshot()
    first["source_available_times"] = [
        "2026-06-30T23:59:58Z",
        "2026-06-30T23:59:59Z",
    ]
    second = snapshot()
    second["source_available_times"] = [
        "2026-06-30T23:59:59Z",
        "2026-06-30T23:59:58Z",
        "2026-06-30T23:59:59Z",
    ]

    assert serialize_learning_event(
        build_analysis_quality_event(first, trusted_tenant_id="tenant-a")
    ) == serialize_learning_event(
        build_analysis_quality_event(second, trusted_tenant_id="tenant-a")
    )


def test_outcome_and_gold_label_surfaces_are_forbidden():
    value = snapshot()
    value["outcome_id"] = "out-1"

    with pytest.raises(LearningEventError, match="outcome or gold label"):
        build_analysis_quality_event(value, trusted_tenant_id="tenant-a")


def test_evidence_snapshot_is_content_addressed_and_deeply_immutable():
    value = snapshot()
    original_nested = value["evidence_snapshot"][0]
    event = build_analysis_quality_event(value, trusted_tenant_id="tenant-a")
    serialized = serialize_learning_event(event)

    original_nested["related_claim"] = "mutated after build"

    assert serialize_learning_event(event) == serialized
    assert event.payload["evidence_snapshot"][0]["related_claim"] == "claim-0"
    with pytest.raises(TypeError):
        event.payload["evidence_snapshot"][0]["trust"] = 0.1


def test_evidence_snapshot_id_mismatch_fails_closed():
    value = snapshot()
    value["evidence_snapshot_id"] = "sha256:" + "0" * 64

    with pytest.raises(LearningEventError, match="evidence_snapshot_id"):
        build_analysis_quality_event(value, trusted_tenant_id="tenant-a")


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("source", "", "source"),
        ("fetched_at", "2026-07-01T00:00:02Z", "available_time"),
        ("content_reference", "", "content_reference"),
        ("related_claim", "", "related_claim"),
        ("schema_version", "", "schema_version"),
        ("trust", float("nan"), "trust"),
    ],
)
def test_evidence_snapshot_minimum_schema_fails_closed(field, value, match):
    item = snapshot()
    item["evidence_snapshot"][0][field] = value

    with pytest.raises(LearningEventError, match=match):
        build_analysis_quality_event(item, trusted_tenant_id="tenant-a")


def test_snapshot_cannot_advance_as_of_or_spoof_trusted_pit():
    value = snapshot()
    authority = trusted_pit(value)
    value["as_of_time"] = "2026-07-02T00:00:00Z"

    with pytest.raises(LearningEventError, match="trusted_pit"):
        build_analysis_quality_event(
            value,
            trusted_tenant_id="tenant-a",
            trusted_pit_override=authority,
        )


def test_snapshot_cannot_spoof_trusted_provenance():
    value = snapshot()
    authority = trusted_provenance(value)
    value["provenance"]["collector"] = "attacker"

    with pytest.raises(LearningEventError, match="trusted_provenance"):
        build_analysis_quality_event(
            value,
            trusted_tenant_id="tenant-a",
            trusted_provenance_override=authority,
        )


def test_all_availability_and_observation_times_are_bounded_by_available_time():
    source_future = snapshot()
    source_future["source_available_times"] = ["2026-07-01T00:00:01.500000Z"]
    provenance_future = snapshot()
    provenance_future["provenance"]["observed_at"] = "2026-07-01T00:00:01.500000Z"

    with pytest.raises(LearningEventError, match="future source"):
        build_analysis_quality_event(source_future, trusted_tenant_id="tenant-a")
    with pytest.raises(LearningEventError, match="available_time"):
        build_analysis_quality_event(
            provenance_future,
            trusted_tenant_id="tenant-a",
        )


def test_failed_stage_requires_nonempty_failure_code_and_message():
    value = snapshot()
    value["stage_metrics"][0] = {
        "stage": "retrieval",
        "latency_ms": 1,
        "status": "failed",
        "attempts": 1,
        "failure": {"code": "", "message": "timed out"},
    }
    value["failure"] = {
        "status": "partial",
        "failed_stage": "retrieval",
        "code": "timeout",
        "message": "timed out",
        "retryable": True,
    }

    with pytest.raises(LearningEventError, match="failure.code"):
        build_analysis_quality_event(value, trusted_tenant_id="tenant-a")
