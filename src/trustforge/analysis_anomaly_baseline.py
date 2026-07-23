"""Deterministic, explainable anomaly baseline for analysis-quality events.

This module is deliberately a pure candidate generator.  It has no registry,
mutable "current" pointer, approval path, activation path, or persistence.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .calibration_dataset import (
    _canonical_value_tokens as _calibration_canonical_value_tokens,
    _event_anchor as _calibration_event_anchor,
    _preflight_event as _calibration_preflight_event,
    _sha256 as _calibration_sha256,
)
from .learning_event_contract import (
    LearningEvent,
    canonical_integrity_checksum,
    make_learning_event,
)

_MANIFEST_KIND = "confidence-calibration-dataset.v2"
_ANALYSIS_KIND = "historical_non_evidentiary"
_ANALYSIS_TYPE = "analysis-quality.v1"
_MAX_EVENTS = 100_000
_MAX_EVENT_INPUT_BYTES = 16 * 1024 * 1024
_MAX_MANIFEST_INPUT_BYTES = 16 * 1024 * 1024
_MAX_FIELD_BYTES = 64 * 1024
_MAX_INPUT_NODES = 1_000_000
_MANIFEST_FIELDS = {
    "kind", "policy", "input_roots", "versions", "excluded_counts",
    "split_ranges", "row_counts", "group_counts", "row_count", "group_count",
    "rows_sha256", "rows", "manifest_sha256",
}
_MANIFEST_POLICY_FIELDS = {
    "dataset_as_of", "train_end", "validation_end", "embargo_seconds",
    "eligibility_version", "split_version", "producer_version", "tenant_id",
    "market_data_variant",
}
_MANIFEST_VERSION_FIELDS = {
    "producer", "eligibility", "split", "analysis_schema", "outcome_schema",
    "kernel_schema",
}
_ROW_FIELDS = {
    "analysis_id", "tenant_id", "analysis_identity", "schema_version",
    "analysis_event_time", "analysis_available_time", "coin", "mode",
    "question_type", "calibrated_confidence", "raw_confidence", "direction",
    "outcome_identity", "source_event_identity", "market_data_variant",
    "outcome_available_time", "outcome_source_version", "horizon",
    "outcome_pct", "ground_truth_direction", "split",
}
_CANDIDATE_FIELDS = {
    "diagnostic_id", "analysis_id", "reason", "reason_code", "classification",
    "eligible_as_evidence", "candidate_only", "details", "baseline",
    "input_manifest", "reproducible_query", "input_summary",
}


class AnalysisAnomalyError(ValueError):
    """The frozen baseline inputs are invalid or cannot be reproduced."""


@dataclass(frozen=True)
class AnalysisAnomalyPolicy:
    """Exact immutable policy for one reproducible anomaly query."""

    tenant_id: str
    baseline_version: str
    query_version: str
    producer_version: str
    reference_start: str
    reference_end: str
    current_start: str
    current_end: str
    query_as_of: str
    minimum_reference_samples: int = 20
    minimum_current_samples: int = 10
    confidence_drift_threshold: float = 0.15
    evidence_missing_rate_threshold: float = 0.25
    source_concentration_threshold: float = 0.80
    robust_z_threshold: float = 3.5
    pipeline_anomaly_rate_threshold: float = 0.10
    latency_robust_z_threshold: float = 3.5
    required_stages: tuple[str, ...] = ("kernel",)

    def canonical(self) -> dict[str, Any]:
        values = asdict(self)
        for field in ("tenant_id", "baseline_version", "query_version", "producer_version"):
            _text(values[field], field)
        times = {
            name: _parse(values[name], name)
            for name in (
                "reference_start",
                "reference_end",
                "current_start",
                "current_end",
                "query_as_of",
            )
        }
        if not (
            times["reference_start"]
            < times["reference_end"]
            <= times["current_start"]
            < times["current_end"]
            <= times["query_as_of"]
        ):
            raise AnalysisAnomalyError(
                "windows must satisfy reference_start < reference_end <= "
                "current_start < current_end <= query_as_of"
            )
        for field in ("minimum_reference_samples", "minimum_current_samples"):
            if type(values[field]) is not int or values[field] < 1:
                raise AnalysisAnomalyError(f"{field} must be a positive integer")
        for field in (
            "confidence_drift_threshold",
            "evidence_missing_rate_threshold",
            "source_concentration_threshold",
            "pipeline_anomaly_rate_threshold",
        ):
            _finite_range(values[field], field, 0.0, 1.0)
        _finite_range(values["robust_z_threshold"], "robust_z_threshold", 0.0, math.inf)
        _finite_range(
            values["latency_robust_z_threshold"],
            "latency_robust_z_threshold",
            0.0,
            math.inf,
        )
        if values["robust_z_threshold"] == 0:
            raise AnalysisAnomalyError("robust_z_threshold must be positive")
        if values["latency_robust_z_threshold"] == 0:
            raise AnalysisAnomalyError("latency_robust_z_threshold must be positive")
        if type(self.required_stages) is not tuple or not self.required_stages:
            raise AnalysisAnomalyError("required_stages must be a non-empty tuple")
        if len(set(self.required_stages)) != len(self.required_stages):
            raise AnalysisAnomalyError("required_stages must be unique")
        for stage in self.required_stages:
            _text(stage, "required_stages item")
        values["required_stages"] = sorted(self.required_stages)
        for name, parsed in times.items():
            values[name] = _utc(parsed)
        return values


def detect_analysis_anomalies(
    analysis_events: Iterable[LearningEvent],
    *,
    calibration_manifest: Mapping[str, Any],
    policy: AnalysisAnomalyPolicy,
) -> dict[str, Any]:
    """Return a deterministic report and non-evidentiary candidate events."""

    frozen = policy.canonical()
    manifest = _verify_manifest(calibration_manifest, frozen)
    events = _bounded_events(
        analysis_events,
        tenant_id=policy.tenant_id,
        dataset_as_of=_parse(manifest["policy"]["dataset_as_of"], "dataset_as_of"),
    )
    anchors = sorted(
        (_calibration_event_anchor(event) for event in events),
        key=lambda anchor: (anchor["identity"], _calibration_sha256(anchor)),
    )
    actual_root = _calibration_sha256(anchors)
    if actual_root != manifest["input_roots"]["analysis_sha256"]:
        raise AnalysisAnomalyError("analysis input root mismatch")
    cohort = _manifest_cohort(manifest)
    distribution_selected: dict[str, dict[str, Any]] = {}
    pipeline_selected: dict[str, dict[str, Any]] = {}
    rejected = Counter({
        "not_manifest_row_cohort": 0, "outside_windows": 0,
        "after_query_as_of": 0,
    })
    for event in events:
        if _parse(event.available_time, "analysis.available_time") > _parse(
            policy.query_as_of, "query_as_of"
        ):
            rejected["after_query_as_of"] += 1
            continue
        # Root authority is primary. Rows define labeled cohort membership for
        # distribution rules; pipeline/degraded diagnostics still inspect every
        # root-bound canonical input so partial analyses cannot be hidden.
        if event.identity in pipeline_selected:
            raise AnalysisAnomalyError("duplicate analysis event identity")
        row = _analysis_metrics(event)
        available = _parse(event.available_time, "analysis.available_time")
        window = _window(available, frozen)
        if window is None:
            rejected["outside_windows"] += 1
            continue
        row["window"] = window
        pipeline_selected[event.identity] = row
        if event.identity in cohort:
            distribution_selected[event.identity] = row
        else:
            rejected["not_manifest_row_cohort"] += 1

    reference = [
        row for row in distribution_selected.values() if row["window"] == "reference"
    ]
    current = [
        row for row in distribution_selected.values() if row["window"] == "current"
    ]
    pipeline_reference = [
        row for row in pipeline_selected.values() if row["window"] == "reference"
    ]
    pipeline_current = [
        row for row in pipeline_selected.values() if row["window"] == "current"
    ]
    reference.sort(key=lambda row: row["identity"])
    current.sort(key=lambda row: row["identity"])
    pipeline_reference.sort(key=lambda row: row["identity"])
    pipeline_current.sort(key=lambda row: row["identity"])
    query = {
        "query_version": policy.query_version,
        "tenant_id": policy.tenant_id,
        "manifest_sha256": manifest["manifest_sha256"],
        "baseline_version": policy.baseline_version,
        "reference": {
            "start_inclusive": frozen["reference_start"],
            "end_exclusive": frozen["reference_end"],
        },
        "current": {
            "start_inclusive": frozen["current_start"],
            "end_exclusive": frozen["current_end"],
        },
        "query_as_of": frozen["query_as_of"],
        "cohort_rule": "manifest.rows.analysis_identity",
    }
    query_hash = _sha256(query)
    findings: list[dict[str, Any]] = []
    status = "complete"
    if (
        len(reference) < policy.minimum_reference_samples
        or len(current) < policy.minimum_current_samples
    ):
        status = "insufficient_data"
        findings.append(
            _finding(
                "INSUFFICIENT_DATA",
                "sample counts do not meet frozen minimums",
                {
                    "reference_count": len(reference),
                    "current_count": len(current),
                    "minimum_reference_samples": policy.minimum_reference_samples,
                    "minimum_current_samples": policy.minimum_current_samples,
                },
            )
        )
    else:
        findings.extend(_distribution_findings(reference, current, policy))
        findings.extend(_outlier_findings(reference, current, policy))
        findings.extend(_pipeline_findings(pipeline_reference, pipeline_current, policy))
        if any(
            finding["reason_code"].startswith("DEGRADED_") for finding in findings
        ):
            status = "degraded"
        if any(row["degraded_reasons"] for row in reference + current):
            status = "degraded"
            reasons = Counter(
                reason
                for row in reference + current
                for reason in row["degraded_reasons"]
            )
            findings.append(
                _finding(
                    "DEGRADED_INPUT",
                    "eligible canonical events contain unavailable quality metrics",
                    {"reason_counts": dict(sorted(reasons.items()))},
                )
            )

    findings.sort(key=lambda item: (item["reason_code"], _sha256(item)))
    reference_stats = _reference_stats(reference)
    pipeline_reference_stats = _pipeline_reference_stats(pipeline_reference)
    spec = {
        key: frozen[key]
        for key in frozen
        if key not in {"tenant_id", "reference_start", "reference_end",
                       "current_start", "current_end", "query_as_of"}
    }
    baseline_spec_sha256 = _sha256(spec)
    input_summary = {
        "identity_root": actual_root,
        "identity_count": len(events),
        "excluded_counts": dict(sorted(rejected.items())),
        "degraded_count": sum(
            bool(row["degraded_reasons"]) for row in reference + current
        ),
    }
    diagnostics = [
        _candidate(
            finding,
            ordinal=ordinal,
            policy=frozen,
            manifest=manifest,
            query=query,
            query_hash=query_hash,
            baseline_spec_sha256=baseline_spec_sha256,
            reference_stats=reference_stats,
            pipeline_reference_stats=pipeline_reference_stats,
            input_summary=input_summary,
        )
        for ordinal, finding in enumerate(findings)
    ]
    report: dict[str, Any] = {
        "kind": "analysis-anomaly-baseline-report.v1",
        "status": status,
        "policy": frozen,
        "baseline": {
            "version": policy.baseline_version,
            "spec_sha256": baseline_spec_sha256,
            "thresholds": _frozen_thresholds(frozen),
            "reference_stats": reference_stats,
            "pipeline_reference_stats": pipeline_reference_stats,
            "manifest_sha256": manifest["manifest_sha256"],
            "manifest_rows_sha256": manifest["rows_sha256"],
            "analysis_input_root": manifest["input_roots"]["analysis_sha256"],
        },
        "query": query,
        "query_sha256": query_hash,
        "sample_counts": {
            "cohort": "manifest_rows_distribution",
            "reference": len(reference),
            "current": len(current),
        },
        "pipeline_sample_counts": {
            "cohort": "root_bound_all_analysis_events",
            "reference": len(pipeline_reference),
            "current": len(pipeline_current),
        },
        "rejected_counts": dict(sorted(rejected.items())),
        "input_summary": input_summary,
        "findings": findings,
        "diagnostics": diagnostics,
    }
    report["report_sha256"] = _sha256(
        {key: value for key, value in report.items() if key != "diagnostics"}
    )
    return report


def _verify_manifest(value: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is not dict:
        raise AnalysisAnomalyError("calibration manifest must be an exact object")
    _bounded_json(value, "calibration manifest")
    manifest = json.loads(json.dumps(value))
    if set(manifest) != _MANIFEST_FIELDS:
        raise AnalysisAnomalyError("calibration manifest schema is not exact")
    if manifest["kind"] != _MANIFEST_KIND:
        raise AnalysisAnomalyError("unsupported calibration manifest kind")
    manifest_checksum = manifest["manifest_sha256"]
    unsigned = {key: item for key, item in manifest.items() if key != "manifest_sha256"}
    if manifest_checksum != _sha256(unsigned):
        raise AnalysisAnomalyError("calibration manifest checksum mismatch")
    if manifest["rows_sha256"] != _sha256(manifest["rows"]):
        raise AnalysisAnomalyError("calibration rows checksum mismatch")
    manifest_policy = manifest["policy"]
    if type(manifest_policy) is not dict or set(manifest_policy) != _MANIFEST_POLICY_FIELDS:
        raise AnalysisAnomalyError("calibration manifest policy is invalid")
    if manifest_policy.get("tenant_id") != policy["tenant_id"]:
        raise AnalysisAnomalyError("calibration manifest tenant mismatch")
    if _parse(manifest_policy.get("dataset_as_of"), "manifest.dataset_as_of") < _parse(
        policy["current_end"], "current_end"
    ):
        raise AnalysisAnomalyError("calibration manifest is older than current window")
    if _parse(manifest_policy["dataset_as_of"], "manifest.dataset_as_of") > _parse(
        policy["query_as_of"], "query_as_of"
    ):
        raise AnalysisAnomalyError("calibration manifest is newer than query cutoff")
    if manifest_policy.get("market_data_variant") not in {
        "as_first_known", "latest_official"
    }:
        raise AnalysisAnomalyError("calibration manifest variant is invalid")
    for field in ("producer_version", "eligibility_version", "split_version"):
        _text(manifest_policy.get(field), f"manifest.policy.{field}")
    if (
        type(manifest_policy["embargo_seconds"]) is not int
        or manifest_policy["embargo_seconds"] < 0
    ):
        raise AnalysisAnomalyError("calibration embargo_seconds is invalid")
    train = _parse(manifest_policy["train_end"], "manifest.train_end")
    validation = _parse(manifest_policy["validation_end"], "manifest.validation_end")
    dataset = _parse(manifest_policy["dataset_as_of"], "manifest.dataset_as_of")
    if not train < validation <= dataset:
        raise AnalysisAnomalyError("calibration manifest temporal policy is invalid")
    if (
        type(manifest["versions"]) is not dict
        or set(manifest["versions"]) != _MANIFEST_VERSION_FIELDS
    ):
        raise AnalysisAnomalyError("calibration manifest versions are invalid")
    for name, version in manifest["versions"].items():
        _text(version, f"manifest.versions.{name}")
    if manifest["versions"]["analysis_schema"] != _ANALYSIS_TYPE:
        raise AnalysisAnomalyError("calibration analysis schema is unsupported")
    for manifest_field, policy_field in (
        ("producer", "producer_version"),
        ("eligibility", "eligibility_version"),
        ("split", "split_version"),
    ):
        if manifest["versions"][manifest_field] != manifest_policy[policy_field]:
            raise AnalysisAnomalyError(
                "calibration manifest policy/version binding is invalid"
            )
    if type(manifest["input_roots"]) is not dict or set(manifest["input_roots"]) != {
        "analysis_sha256", "outcome_sha256"
    }:
        raise AnalysisAnomalyError("calibration input roots are invalid")
    for root in manifest["input_roots"].values():
        if (
            type(root) is not str
            or len(root) != 64
            or any(character not in "0123456789abcdef" for character in root)
        ):
            raise AnalysisAnomalyError("calibration input root is invalid")
    for name in ("excluded_counts", "row_counts", "group_counts", "split_ranges"):
        if type(manifest[name]) is not dict:
            raise AnalysisAnomalyError(f"calibration {name} is invalid")
    for reason, count in manifest["excluded_counts"].items():
        _text(reason, "calibration excluded_counts reason")
        if type(count) is not int or count < 0:
            raise AnalysisAnomalyError("calibration excluded_counts is invalid")
    expected_ranges = {
        "train": {"start": None, "end_exclusive": manifest_policy["train_end"]},
        "validation": {
            "start": manifest_policy["train_end"],
            "end_exclusive": manifest_policy["validation_end"],
        },
        "test": {
            "start": manifest_policy["validation_end"],
            "end_inclusive": manifest_policy["dataset_as_of"],
        },
    }
    if manifest["split_ranges"] != expected_ranges:
        raise AnalysisAnomalyError("calibration split_ranges are invalid")
    if set(manifest["row_counts"]) != {"train", "validation", "test"}:
        raise AnalysisAnomalyError("calibration row_counts schema is invalid")
    if set(manifest["group_counts"]) != {"train", "validation", "test"}:
        raise AnalysisAnomalyError("calibration group_counts schema is invalid")
    for counts in (manifest["row_counts"], manifest["group_counts"]):
        if any(type(count) is not int or count < 0 for count in counts.values()):
            raise AnalysisAnomalyError("calibration split counts are invalid")
    if type(manifest["row_count"]) is not int or type(manifest["group_count"]) is not int:
        raise AnalysisAnomalyError("calibration aggregate counts are invalid")
    if manifest["row_count"] < 0 or manifest["group_count"] < 0:
        raise AnalysisAnomalyError("calibration aggregate counts are invalid")
    if type(manifest["rows"]) is not list or manifest["row_count"] != len(manifest["rows"]):
        raise AnalysisAnomalyError("calibration row count mismatch")
    expected_counts = Counter(row.get("split") for row in manifest["rows"] if type(row) is dict)
    if manifest["row_counts"] != {
        split: expected_counts[split] for split in ("train", "validation", "test")
    }:
        raise AnalysisAnomalyError("calibration split row counts mismatch")
    return manifest


def _manifest_cohort(manifest: Mapping[str, Any]) -> set[str]:
    cohort: set[str] = set()
    groups: set[tuple[str, str, str]] = set()
    row_keys: set[tuple[str, Any]] = set()
    for row in manifest["rows"]:
        if type(row) is not dict or set(row) != _ROW_FIELDS:
            raise AnalysisAnomalyError("calibration row must be an exact object")
        if row.get("tenant_id") != manifest["policy"]["tenant_id"]:
            raise AnalysisAnomalyError("calibration row tenant mismatch")
        identity = row.get("analysis_identity")
        _text(identity, "calibration row analysis_identity")
        split = row.get("split")
        if split not in {"train", "validation", "test"}:
            raise AnalysisAnomalyError("calibration row split is invalid")
        row_key = (identity, row.get("horizon"))
        if row_key in row_keys:
            raise AnalysisAnomalyError("duplicate calibration row")
        row_keys.add(row_key)
        cohort.add(identity)
        groups.add((row["tenant_id"], identity, split))
        for field in (
            "analysis_id", "schema_version", "coin", "mode", "question_type",
            "outcome_identity", "source_event_identity", "outcome_source_version",
            "outcome_pct",
        ):
            _text(row.get(field), f"calibration row {field}")
        if row["schema_version"] != "learning-event.v1":
            raise AnalysisAnomalyError("calibration row schema is invalid")
        if row["source_event_identity"] != identity:
            raise AnalysisAnomalyError("calibration row source binding is invalid")
        if row["market_data_variant"] != manifest["policy"]["market_data_variant"]:
            raise AnalysisAnomalyError("calibration row variant is invalid")
        if row["horizon"] not in {"T+1", "T+7", "T+14"}:
            raise AnalysisAnomalyError("calibration row horizon is invalid")
        if row["direction"] not in {"bullish", "bearish"}:
            raise AnalysisAnomalyError("calibration row direction is invalid")
        if row["ground_truth_direction"] not in {"bullish", "bearish", "neutral"}:
            raise AnalysisAnomalyError("calibration row ground truth is invalid")
        for field in ("calibrated_confidence", "raw_confidence"):
            _finite_range(row[field], f"calibration row {field}", 0.0, 1.0)
        analysis_time = _parse(
            row["analysis_available_time"], "row.analysis_available_time"
        )
        _parse(row["analysis_event_time"], "row.analysis_event_time")
        outcome_time = _parse(
            row["outcome_available_time"], "row.outcome_available_time"
        )
        if outcome_time <= analysis_time:
            raise AnalysisAnomalyError("calibration row outcome PIT is invalid")
        if analysis_time > _parse(
            manifest["policy"]["dataset_as_of"], "manifest.dataset_as_of"
        ):
            raise AnalysisAnomalyError("calibration row exceeds dataset cutoff")
        expected_split = (
            "train" if analysis_time < _parse(manifest["policy"]["train_end"], "manifest.train_end")
            else "validation" if analysis_time < _parse(
                manifest["policy"]["validation_end"], "manifest.validation_end"
            )
            else "test"
        )
        if split != expected_split:
            raise AnalysisAnomalyError("calibration row split binding is invalid")
    expected_groups = Counter(group[2] for group in groups)
    if manifest["group_count"] != len(groups) or manifest["group_counts"] != {
        split: expected_groups[split] for split in ("train", "validation", "test")
    }:
        raise AnalysisAnomalyError("calibration group counts mismatch")
    return cohort


def _bounded_events(
    events: Iterable[LearningEvent], *, tenant_id: str, dataset_as_of: datetime
) -> list[LearningEvent]:
    result: list[LearningEvent] = []
    size = 0
    nodes = 0
    identities: set[str] = set()
    for event in events:
        if not isinstance(event, LearningEvent):
            raise AnalysisAnomalyError("analysis input must contain LearningEvent")
        if event.tenant_id != tenant_id:
            continue
        # Safe envelope metadata is checked before tenant snapshot quotas.
        if _parse(event.available_time, "analysis.available_time") > dataset_as_of:
            continue
        if len(result) >= _MAX_EVENTS:
            raise AnalysisAnomalyError("analysis input exceeds event count limit")
        if event.identity in identities:
            raise AnalysisAnomalyError("duplicate analysis event identity")
        try:
            event_bytes, event_nodes = _calibration_preflight_event(
                event,
                source="analysis",
            byte_budget=_MAX_EVENT_INPUT_BYTES - size,
                node_budget=_MAX_INPUT_NODES - nodes,
            )
        except ValueError as exc:
            raise AnalysisAnomalyError(str(exc)) from exc
        _validate_event_scalars(event)
        size += event_bytes
        nodes += event_nodes
        if event.schema_version != "learning-event.v1":
            raise AnalysisAnomalyError("analysis schema is unsupported")
        identities.add(event.identity)
        result.append(event)
    return result


def _validate_event_scalars(event: LearningEvent) -> None:
    """Reject non-finite or non-JSON scalars without copying the event tree."""

    stack: list[Any] = [event.provenance, event.payload]
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            for key, item in value.items():
                if type(key) is not str:
                    raise AnalysisAnomalyError("analysis mapping keys must be strings")
                stack.append(item)
        elif isinstance(value, (tuple, list)):
            stack.extend(value)
        elif type(value) is float and not math.isfinite(value):
            raise AnalysisAnomalyError("analysis input contains a non-finite JSON number")
        elif type(value) not in (str, int, float, bool, type(None)):
            raise AnalysisAnomalyError("analysis input must use exact JSON types")


def _analysis_metrics(event: LearningEvent) -> dict[str, Any]:
    if (
        event.kind != _ANALYSIS_KIND
        or event.payload.get("event_type") != _ANALYSIS_TYPE
    ):
        raise AnalysisAnomalyError("cohort source must be canonical analysis-quality.v1")
    if event.schema_version != "learning-event.v1":
        raise AnalysisAnomalyError("analysis schema is unsupported")
    if event.provenance.get("tenant_id") != event.tenant_id:
        raise AnalysisAnomalyError("analysis provenance tenant binding is invalid")
    analysis_id = event.payload.get("analysis_id")
    _text(analysis_id, "analysis.analysis_id")
    if not event.entity_id.endswith(f":{analysis_id}"):
        raise AnalysisAnomalyError("analysis canonical identity binding is invalid")
    payload = event.payload
    confidence = payload.get("confidence")
    evidence = payload.get("evidence_stats")
    failure = payload.get("failure")
    stages = payload.get("stage_metrics")
    if not all(isinstance(item, Mapping) for item in (confidence, evidence, failure)):
        raise AnalysisAnomalyError("analysis-quality metrics are invalid")
    if not isinstance(stages, tuple) or not stages:
        raise AnalysisAnomalyError("analysis-quality stage metrics are invalid")
    stage_names: set[str] = set()
    failure_or_partial = failure.get("status") != "complete"
    retry_spike = False
    total_latency_ms = 0
    for stage in stages:
        if not isinstance(stage, Mapping) or set(stage) != {
            "stage", "latency_ms", "status", "attempts", "failure"
        }:
            raise AnalysisAnomalyError("analysis-quality stage metric schema is invalid")
        name = stage.get("stage")
        _text(name, "analysis stage name")
        if name in stage_names:
            raise AnalysisAnomalyError("analysis stage names must be unique")
        stage_names.add(name)
        latency = stage.get("latency_ms")
        attempts = stage.get("attempts")
        status = stage.get("status")
        if type(latency) is not int or latency < 0:
            raise AnalysisAnomalyError("analysis stage latency is invalid")
        if type(attempts) is not int or attempts < 1:
            raise AnalysisAnomalyError("analysis stage attempts is invalid")
        if status not in {"complete", "failed", "skipped"}:
            raise AnalysisAnomalyError("analysis stage status is invalid")
        if status == "failed":
            stage_failure = stage.get("failure")
            if (
                not isinstance(stage_failure, Mapping)
                or set(stage_failure) != {"code", "message"}
            ):
                raise AnalysisAnomalyError("analysis failed stage detail is invalid")
            _text(stage_failure.get("code"), "analysis stage failure code")
            _text(stage_failure.get("message"), "analysis stage failure message")
        elif stage.get("failure") is not None:
            raise AnalysisAnomalyError("analysis non-failed stage has failure detail")
        failure_or_partial = failure_or_partial or status == "failed"
        retry_spike = retry_spike or attempts > 1
        total_latency_ms += latency
    calibrated = confidence.get("calibrated")
    _finite_range(calibrated, "confidence.calibrated", 0.0, 1.0)
    degraded: list[str] = []
    evidence_count = evidence.get("evidence_count")
    if type(evidence_count) is not int or evidence_count < 0:
        degraded.append("evidence_count_unavailable")
        evidence_count = 0
    distribution = evidence.get("source_distribution")
    source_total = 0
    source_max = 0
    if isinstance(distribution, Mapping) and all(
        type(value) is int and value >= 0 for value in distribution.values()
    ):
        source_total = sum(distribution.values())
        source_max = max(distribution.values(), default=0)
    else:
        degraded.append("source_distribution_unavailable")
    return {
        "identity": event.identity,
        "analysis_id": payload.get("analysis_id"),
        "available_time": event.available_time,
        "confidence": float(calibrated),
        "evidence_missing": evidence_count == 0,
        "source_concentration": (source_max / source_total) if source_total else None,
        "pipeline_failure_or_partial": failure_or_partial,
        "pipeline_retry_spike": retry_spike,
        "pipeline_anomaly": failure_or_partial or retry_spike,
        "stage_names": sorted(stage_names),
        "total_latency_ms": total_latency_ms,
        "degraded_reasons": degraded,
    }


def _distribution_findings(
    reference: list[dict[str, Any]],
    current: list[dict[str, Any]],
    policy: AnalysisAnomalyPolicy,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    ref_conf = statistics.fmean(row["confidence"] for row in reference)
    cur_conf = statistics.fmean(row["confidence"] for row in current)
    drift = abs(cur_conf - ref_conf)
    if drift >= policy.confidence_drift_threshold:
        findings.append(_finding("CONFIDENCE_DRIFT", "mean calibrated confidence shifted", {
            "reference_mean": ref_conf, "current_mean": cur_conf, "absolute_delta": drift,
            "threshold": policy.confidence_drift_threshold,
        }))
    missing = sum(row["evidence_missing"] for row in current) / len(current)
    if missing >= policy.evidence_missing_rate_threshold:
        findings.append(_finding("EVIDENCE_MISSING", "current evidence missing rate exceeded threshold", {
            "current_rate": missing, "threshold": policy.evidence_missing_rate_threshold,
        }))
    concentrations = [
        row["source_concentration"] for row in current
        if row["source_concentration"] is not None
    ]
    if concentrations:
        concentration = statistics.fmean(concentrations)
        if concentration >= policy.source_concentration_threshold:
            findings.append(_finding("SOURCE_CONCENTRATION", "current sources are overly concentrated", {
                "current_mean_max_share": concentration,
                "threshold": policy.source_concentration_threshold,
            }))
    return findings


def _outlier_findings(
    reference: list[dict[str, Any]],
    current: list[dict[str, Any]],
    policy: AnalysisAnomalyPolicy,
) -> list[dict[str, Any]]:
    values = [row["confidence"] for row in reference]
    median = statistics.median(values)
    mad = statistics.median(abs(value - median) for value in values)
    outliers: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    if mad == 0:
        findings.append(_finding(
            "DEGRADED_ZERO_MAD",
            "reference confidence MAD is zero; robust scale is degenerate",
            {"reference_median": median, "reference_mad": mad},
        ))
    for row in current:
        deviation = abs(row["confidence"] - median)
        robust_z = math.inf if mad == 0 and deviation else 0.0 if mad == 0 else 0.6745 * deviation / mad
        if robust_z >= policy.robust_z_threshold:
            outliers.append({"analysis_identity": row["identity"], "robust_z": robust_z})
    if not outliers:
        return findings
    # JSON has no Infinity; the exact marker retains deterministic semantics.
    for item in outliers:
        if not math.isfinite(item["robust_z"]):
            item["robust_z"] = "infinite"
    findings.append(_finding("MEDIAN_MAD_OUTLIER", "current confidence contains robust outliers", {
        "reference_median": median, "reference_mad": mad,
        "threshold": policy.robust_z_threshold, "outliers": outliers,
    }))
    return findings


def _pipeline_findings(
    reference: list[dict[str, Any]],
    current: list[dict[str, Any]],
    policy: AnalysisAnomalyPolicy,
) -> list[dict[str, Any]]:
    reference_failure_rate = sum(
        row["pipeline_failure_or_partial"] for row in reference
    ) / len(reference)
    current_failure_rate = sum(
        row["pipeline_failure_or_partial"] for row in current
    ) / len(current)
    reference_retry_rate = sum(row["pipeline_retry_spike"] for row in reference) / len(reference)
    current_retry_rate = sum(row["pipeline_retry_spike"] for row in current) / len(current)
    findings: list[dict[str, Any]] = []
    if current_failure_rate >= policy.pipeline_anomaly_rate_threshold:
        findings.append(_finding(
            "PIPELINE_FAILURE_OR_PARTIAL",
            "current pipeline failure or partial rate exceeded threshold",
            {
                "reference_rate": reference_failure_rate,
                "current_rate": current_failure_rate,
                "stage_failed_counts_as_failure": True,
                "threshold": policy.pipeline_anomaly_rate_threshold,
            },
        ))
    if current_retry_rate >= policy.pipeline_anomaly_rate_threshold:
        findings.append(_finding(
            "PIPELINE_RETRY_SPIKE",
            "current pipeline retry rate exceeded threshold",
            {
                "reference_rate": reference_retry_rate,
                "current_rate": current_retry_rate,
                "threshold": policy.pipeline_anomaly_rate_threshold,
            },
        ))
    missing = [
        {
            "analysis_identity": row["identity"],
            "missing_stages": sorted(set(policy.required_stages) - set(row["stage_names"])),
        }
        for row in current
        if set(policy.required_stages) - set(row["stage_names"])
    ]
    if missing:
        findings.append(_finding(
            "PIPELINE_STAGE_MISSING",
            "required pipeline stages are absent",
            {"required_stages": sorted(policy.required_stages), "analyses": missing},
        ))
    ref_latency = [row["total_latency_ms"] for row in reference]
    latency_median = statistics.median(ref_latency)
    latency_mad = statistics.median(
        abs(value - latency_median) for value in ref_latency
    )
    latency_outliers = []
    for row in current:
        deviation = abs(row["total_latency_ms"] - latency_median)
        robust_z = (
            math.inf if latency_mad == 0 and deviation
            else 0.0 if latency_mad == 0
            else 0.6745 * deviation / latency_mad
        )
        if robust_z >= policy.latency_robust_z_threshold:
            latency_outliers.append({
                "analysis_identity": row["identity"],
                "total_latency_ms": row["total_latency_ms"],
                "robust_z": "infinite" if not math.isfinite(robust_z) else robust_z,
            })
    if latency_outliers:
        findings.append(_finding(
            "PIPELINE_LATENCY_OUTLIER",
            "current pipeline latency contains robust outliers",
            {
                "reference_median_ms": latency_median,
                "reference_mad_ms": latency_mad,
                "threshold": policy.latency_robust_z_threshold,
                "outliers": latency_outliers,
            },
        ))
    return findings


def _candidate(
    finding: Mapping[str, Any],
    *,
    ordinal: int,
    policy: Mapping[str, Any],
    manifest: Mapping[str, Any],
    query: Mapping[str, Any],
    query_hash: str,
    baseline_spec_sha256: str,
    reference_stats: Mapping[str, Any],
    pipeline_reference_stats: Mapping[str, Any],
    input_summary: Mapping[str, Any],
) -> LearningEvent:
    seed = {
        "tenant_id": policy["tenant_id"],
        "baseline_spec_sha256": baseline_spec_sha256,
        "manifest_sha256": manifest["manifest_sha256"],
        "query_sha256": query_hash,
        "ordinal": ordinal,
        "reason_code": finding["reason_code"],
        "reason": finding["reason"],
    }
    diagnostic_id = f"analysis-anomaly:{_sha256(seed)}"
    source_record = {
        "diagnostic_id": diagnostic_id,
        "tenant_id": policy["tenant_id"],
        "baseline_spec_sha256": baseline_spec_sha256,
        "manifest_sha256": manifest["manifest_sha256"],
        "rows_sha256": manifest["rows_sha256"],
        "analysis_input_root": manifest["input_roots"]["analysis_sha256"],
        "manifest_versions": manifest["versions"],
        "query_sha256": query_hash,
        "ordinal": ordinal,
        "reason_code": finding["reason_code"],
        "query": query,
        "finding_sha256": _sha256(finding),
    }
    payload = {
        "diagnostic_id": diagnostic_id,
        "analysis_id": f"cohort:{query_hash}",
        "reason": finding["reason"],
        "reason_code": finding["reason_code"],
        "classification": "non_evidentiary_candidate",
        "eligible_as_evidence": False,
        "candidate_only": True,
        "details": finding["details"],
        "baseline": {
            "version": policy["baseline_version"],
            "spec_sha256": baseline_spec_sha256,
            "thresholds": _frozen_thresholds(policy),
            "reference_stats": dict(reference_stats),
            "pipeline_reference_stats": dict(pipeline_reference_stats),
        },
        "input_manifest": {
            "manifest_sha256": manifest["manifest_sha256"],
            "rows_sha256": manifest["rows_sha256"],
            "analysis_input_root": manifest["input_roots"]["analysis_sha256"],
            "manifest_versions": manifest["versions"],
        },
        "reproducible_query": {
            "specification": dict(query),
            "sha256": query_hash,
        },
        "input_summary": dict(input_summary),
    }
    if set(payload) != _CANDIDATE_FIELDS:
        raise AnalysisAnomalyError("candidate diagnostic payload schema is not exact")
    if {
        "approve", "approved", "approval", "approval_action",
        "activate", "activated", "activation", "proposal", "active_version",
    } & set(payload):
        raise AnalysisAnomalyError("candidate diagnostic contains authority fields")
    return make_learning_event(
        kind="candidate_diagnostic",
        tenant_id=policy["tenant_id"],
        entity_id=diagnostic_id,
        revision=1,
        event_time=policy["query_as_of"],
        available_time=policy["query_as_of"],
        as_of_time=policy["query_as_of"],
        provenance={
            "source": "analysis-anomaly-baseline",
            "collector": "trustforge",
            "observed_at": policy["query_as_of"],
            "tenant_id": policy["tenant_id"],
            "source_record": source_record,
            "version": policy["producer_version"],
            "checksum": canonical_integrity_checksum(source_record),
        },
        payload=payload,
    )


def _reference_stats(reference: list[dict[str, Any]]) -> dict[str, Any]:
    if not reference:
        return {
            "count": 0, "confidence_mean": None, "confidence_median": None,
            "confidence_mad": None, "evidence_missing_rate": None,
            "source_concentration_rate": None,
            "source_concentration_mean_share": None,
        }
    confidence = [row["confidence"] for row in reference]
    concentrations = [
        row["source_concentration"] for row in reference
        if row["source_concentration"] is not None
    ]
    confidence_median = statistics.median(confidence)
    return {
        "count": len(reference),
        "confidence_mean": statistics.fmean(confidence),
        "confidence_median": confidence_median,
        "confidence_mad": statistics.median(
            abs(value - confidence_median) for value in confidence
        ),
        "evidence_missing_rate": sum(
            row["evidence_missing"] for row in reference
        ) / len(reference),
        "source_concentration_rate": len(concentrations) / len(reference),
        "source_concentration_mean_share": (
            statistics.fmean(concentrations) if concentrations else None
        ),
    }


def _pipeline_reference_stats(reference: list[dict[str, Any]]) -> dict[str, Any]:
    if not reference:
        return {
            "count": 0,
            "pipeline_anomaly_rate": None,
            "pipeline_failure_or_partial_rate": None,
            "pipeline_retry_spike_rate": None,
            "latency_mean_ms": None,
            "latency_median_ms": None,
            "latency_mad_ms": None,
        }
    latency = [row["total_latency_ms"] for row in reference]
    latency_median = statistics.median(latency)
    return {
        "count": len(reference),
        "pipeline_anomaly_rate": sum(
            row["pipeline_anomaly"] for row in reference
        ) / len(reference),
        "pipeline_failure_or_partial_rate": sum(
            row["pipeline_failure_or_partial"] for row in reference
        ) / len(reference),
        "pipeline_retry_spike_rate": sum(
            row["pipeline_retry_spike"] for row in reference
        ) / len(reference),
        "latency_mean_ms": statistics.fmean(latency),
        "latency_median_ms": latency_median,
        "latency_mad_ms": statistics.median(
            abs(value - latency_median) for value in latency
        ),
    }


def _frozen_thresholds(policy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: policy[name]
        for name in (
            "minimum_reference_samples",
            "minimum_current_samples",
            "confidence_drift_threshold",
            "evidence_missing_rate_threshold",
            "source_concentration_threshold",
            "robust_z_threshold",
            "pipeline_anomaly_rate_threshold",
            "latency_robust_z_threshold",
            "required_stages",
        )
    }


def _finding(code: str, reason: str, details: Mapping[str, Any]) -> dict[str, Any]:
    return {"reason_code": code, "reason": reason, "details": dict(details)}


def _window(value: datetime, policy: Mapping[str, Any]) -> str | None:
    if _parse(policy["reference_start"], "reference_start") <= value < _parse(
        policy["reference_end"], "reference_end"
    ):
        return "reference"
    if _parse(policy["current_start"], "current_start") <= value < _parse(
        policy["current_end"], "current_end"
    ):
        return "current"
    return None


def _bounded_json(value: Any, field: str) -> None:
    state = {"nodes": 0, "node_budget": _MAX_INPUT_NODES, "source": field}
    total = 0
    try:
        for token in _calibration_canonical_value_tokens(value, state=state, depth=1):
            total += len(token.encode("utf-8"))
            if total > _MAX_MANIFEST_INPUT_BYTES:
                raise AnalysisAnomalyError(f"{field} exceeds UTF-8 byte limit")
    except ValueError as exc:
        raise AnalysisAnomalyError(str(exc)) from exc


def _canonical_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            _jsonable(value), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise AnalysisAnomalyError("input is not finite canonical JSON") from None
    return encoded


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise AnalysisAnomalyError("mapping keys must be exact strings")
            if len(key.encode("utf-8")) > _MAX_FIELD_BYTES:
                raise AnalysisAnomalyError("mapping key exceeds UTF-8 byte limit")
            result[key] = _jsonable(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, str) and len(value.encode("utf-8")) > _MAX_FIELD_BYTES:
        raise AnalysisAnomalyError("string field exceeds UTF-8 byte limit")
    if type(value) in (str, int, float, bool, type(None)):
        if type(value) is float and not math.isfinite(value):
            raise AnalysisAnomalyError("input contains a non-finite number")
        return value
    raise AnalysisAnomalyError("input must use exact JSON types")


def _text(value: Any, field: str) -> None:
    if type(value) is not str or not value.strip():
        raise AnalysisAnomalyError(f"{field} is required")
    if len(value.encode("utf-8")) > _MAX_FIELD_BYTES:
        raise AnalysisAnomalyError(f"{field} exceeds UTF-8 byte limit")


def _finite_range(value: Any, field: str, minimum: float, maximum: float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise AnalysisAnomalyError(f"{field} must be finite")
    if value < minimum or value > maximum:
        raise AnalysisAnomalyError(f"{field} is outside allowed range")


def _parse(value: Any, field: str) -> datetime:
    if type(value) is not str:
        raise AnalysisAnomalyError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AnalysisAnomalyError(f"{field} is invalid") from None
    if parsed.tzinfo is None:
        raise AnalysisAnomalyError(f"{field} must be timezone aware")
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
