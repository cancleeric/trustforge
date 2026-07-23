import copy
from collections.abc import Mapping
import json

import pytest

import trustforge.analysis_quality_event as analysis_quality
from trustforge.analysis_quality_event import (
    MAX_CANONICAL_EVENT_BYTES,
    MAX_EVIDENCE_ITEMS,
    MAX_IDENTIFIER_CHARS,
    MAX_PREFLIGHT_DEPTH,
    MAX_RAW_AUTHORITY_INPUT_BYTES,
    MAX_SOURCE_AVAILABLE_TIMES,
    MAX_SOURCE_DISTRIBUTION_BUCKETS,
    MAX_STAGE_METRICS,
    MAX_TEXT_CHARS,
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


@pytest.mark.parametrize(
    "forbidden",
    [
        "outcome_id",
        "label_id",
        "diagnostic_id",
        "approval_action",
        "activation",
        "unreviewed_extension",
    ],
)
def test_evidence_snapshot_rejects_every_unknown_field_even_with_recomputed_hash(
    forbidden,
):
    value = snapshot()
    value["evidence_snapshot"][0][forbidden] = "attacker-controlled"
    value["evidence_snapshot_id"] = canonical_integrity_checksum(
        value["evidence_snapshot"]
    )

    with pytest.raises(LearningEventError, match=rf"unknown fields: {forbidden}"):
        build_analysis_quality_event(value, trusted_tenant_id="tenant-a")


@pytest.mark.parametrize(
    ("field", "maximum"),
    [
        ("evidence_snapshot", MAX_EVIDENCE_ITEMS),
        ("source_available_times", MAX_SOURCE_AVAILABLE_TIMES),
        ("stage_metrics", MAX_STAGE_METRICS),
    ],
)
def test_collection_preflight_accepts_exact_limit_and_rejects_limit_plus_one(
    monkeypatch, field, maximum
):
    value = snapshot()
    # Isolate the cheap boundary itself; semantic validation of repeated
    # entries is deliberately later.
    monkeypatch.setattr(
        analysis_quality,
        {
            "evidence_snapshot": "MAX_EVIDENCE_ITEMS",
            "source_available_times": "MAX_SOURCE_AVAILABLE_TIMES",
            "stage_metrics": "MAX_STAGE_METRICS",
        }[field],
        maximum,
    )
    value[field] = [value[field][0]] * maximum
    analysis_quality._preflight_bounds(
        value, trusted_pit(value), trusted_provenance(value)
    )
    value[field].append(value[field][0])
    with pytest.raises(LearningEventError, match=rf"{field} exceeds {maximum} items"):
        analysis_quality._preflight_bounds(
            value, trusted_pit(value), trusted_provenance(value)
        )


def test_source_distribution_preflight_boundary():
    value = snapshot()
    distribution = {
        f"source-{index}": 0
        for index in range(MAX_SOURCE_DISTRIBUTION_BUCKETS)
    }
    value["evidence_stats"]["source_distribution"] = distribution
    analysis_quality._preflight_bounds(
        value, trusted_pit(value), trusted_provenance(value)
    )
    distribution["one-too-many"] = 0
    with pytest.raises(LearningEventError, match="source_distribution exceeds"):
        analysis_quality._preflight_bounds(
            value, trusted_pit(value), trusted_provenance(value)
        )


def test_identifier_string_boundary_is_diagnostic():
    value = snapshot()
    value["analysis_id"] = "a" * MAX_IDENTIFIER_CHARS
    event = build_analysis_quality_event(value, trusted_tenant_id="tenant-a")
    assert event.payload["analysis_id"] == value["analysis_id"]

    value["analysis_id"] += "a"
    with pytest.raises(LearningEventError, match=rf"analysis_id exceeds {MAX_IDENTIFIER_CHARS}"):
        build_analysis_quality_event(value, trusted_tenant_id="tenant-a")


def test_question_text_boundary_is_diagnostic():
    value = snapshot()
    value["question"] = "q" * MAX_TEXT_CHARS
    build_analysis_quality_event(value, trusted_tenant_id="tenant-a")

    value["question"] += "q"
    with pytest.raises(
        LearningEventError,
        match=rf"snapshot.question exceeds {MAX_TEXT_CHARS} characters",
    ):
        build_analysis_quality_event(value, trusted_tenant_id="tenant-a")


def test_canonical_event_byte_boundary(monkeypatch):
    value = snapshot()
    event = build_analysis_quality_event(value, trusted_tenant_id="tenant-a")
    exact_size = len(serialize_learning_event(event).encode("utf-8"))
    assert exact_size < MAX_CANONICAL_EVENT_BYTES

    monkeypatch.setattr(
        analysis_quality, "MAX_CANONICAL_EVENT_BYTES", exact_size
    )
    build_analysis_quality_event(value, trusted_tenant_id="tenant-a")
    monkeypatch.setattr(
        analysis_quality, "MAX_CANONICAL_EVENT_BYTES", exact_size - 1
    )
    with pytest.raises(LearningEventError, match=rf"exceeds {exact_size - 1} bytes"):
        build_analysis_quality_event(value, trusted_tenant_id="tenant-a")


def test_wrong_type_nested_sequence_is_rejected_before_iteration():
    class BombList(list):
        def __iter__(self):
            raise AssertionError("oversized list must not be iterated")

    value = snapshot()
    value["evidence_snapshot"][0]["attacker_extension"] = BombList([0] * 1000)
    with pytest.raises(LearningEventError, match="exact built-in JSON types"):
        analysis_quality._preflight_bounds(
            value, trusted_pit(value), trusted_provenance(value)
        )


def test_wrong_type_nested_mapping_is_rejected_before_iteration():
    class BombMapping(Mapping):
        def __len__(self):
            return 1000

        def __iter__(self):
            raise AssertionError("oversized mapping must not be iterated")

        def __getitem__(self, key):
            raise AssertionError("oversized mapping must not be read")

        def items(self):
            raise AssertionError("oversized mapping must not be iterated")

    value = snapshot()
    value["evidence_snapshot"][0]["attacker_extension"] = BombMapping()
    with pytest.raises(LearningEventError, match="exact built-in JSON types"):
        analysis_quality._preflight_bounds(
            value, trusted_pit(value), trusted_provenance(value)
        )


def test_hostile_mapping_root_is_rejected_before_any_method_call():
    class HostileRoot(Mapping):
        def __len__(self):
            raise AssertionError("root len must not be called")

        def __iter__(self):
            raise AssertionError("root iteration must not occur")

        def __getitem__(self, key):
            raise AssertionError("root lookup must not occur")

        def get(self, key, default=None):
            raise AssertionError("root get must not occur")

    with pytest.raises(LearningEventError, match="analysis snapshot must be an object"):
        _build_analysis_quality_event(
            HostileRoot(),
            trusted_tenant_id="tenant-a",
            trusted_pit={},
            trusted_provenance={},
        )


@pytest.mark.parametrize("container_type", [dict, list])
def test_hostile_builtin_container_subclass_is_rejected_before_methods(container_type):
    if container_type is dict:
        class Hostile(dict):
            def __len__(self):
                raise AssertionError("subclass len must not be called")

            def items(self):
                raise AssertionError("subclass items must not be called")

        hostile = Hostile()
    else:
        class Hostile(list):
            def __len__(self):
                raise AssertionError("subclass len must not be called")

            def __iter__(self):
                raise AssertionError("subclass iteration must not occur")

        hostile = Hostile()
    value = snapshot()
    value["confidence"]["raw"] = hostile

    with pytest.raises(LearningEventError, match="exact built-in JSON types"):
        analysis_quality._preflight_bounds(
            value, trusted_pit(value), trusted_provenance(value)
        )


@pytest.mark.parametrize("text", ["ascii", "台灣🙂"])
def test_streaming_raw_utf8_budget_exact_and_plus_one(monkeypatch, text):
    values = ({"value": text}, {"authority": text}, {"provenance": text})
    exact = sum(
        len(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        for value in values
    )
    assert exact < MAX_RAW_AUTHORITY_INPUT_BYTES
    monkeypatch.setattr(
        analysis_quality, "MAX_RAW_AUTHORITY_INPUT_BYTES", exact
    )
    analysis_quality._preflight_bounds(*values)
    monkeypatch.setattr(
        analysis_quality, "MAX_RAW_AUTHORITY_INPUT_BYTES", exact - 1
    )
    with pytest.raises(LearningEventError, match=rf"raw JSON budget {exact - 1} bytes"):
        analysis_quality._preflight_bounds(*values)


def test_preflight_depth_accepts_exact_limit_and_rejects_limit_plus_one():
    def nested_list(levels):
        value = "leaf"
        for _ in range(levels):
            value = [value]
        return value

    # Root snapshot field consumes depth 1; 63 containers place the scalar leaf
    # exactly at depth 64.
    exactly = {"attacker_extension": nested_list(MAX_PREFLIGHT_DEPTH - 1)}
    analysis_quality._preflight_bounds(exactly, {}, {})

    too_deep = {"attacker_extension": nested_list(MAX_PREFLIGHT_DEPTH)}
    with pytest.raises(
        LearningEventError,
        match=rf"maximum preflight depth {MAX_PREFLIGHT_DEPTH}",
    ):
        analysis_quality._preflight_bounds(too_deep, {}, {})


def test_self_referential_sequence_fails_at_depth_limit():
    cycle = []
    cycle.append(cycle)
    value = {"attacker_extension": cycle}

    with pytest.raises(
        LearningEventError,
        match=rf"maximum preflight depth {MAX_PREFLIGHT_DEPTH}",
    ):
        analysis_quality._preflight_bounds(value, {}, {})
