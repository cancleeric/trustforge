#!/usr/bin/env python3
"""Train an honest, versioned source-reliability artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

MIN_SAMPLE_PER_SOURCE = 30
SHRINKAGE_TARGET = 0.50
SHRINKAGE_K = 100.0
SCHEMA = "trustforge.source-reputation"
VERSION = "2.0.0"
SOURCE_FAMILIES = frozenset({"sentiment", "onchain", "price", "regulatory"})


@dataclass(frozen=True)
class SourceStats:
    name: str
    support: int
    correct: int
    accuracy: float
    balanced_accuracy: float | None
    balanced_accuracy_reason: str | None
    brier: float
    wilson_ci_95: tuple[float, float]
    reliability: float
    shrinkage_weight: float


def wilson_interval(k: int, n: int) -> tuple[float, float]:
    """Return a 95% Wilson score interval for a binomial proportion."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    z = 1.959963984540054
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half_width = (
        z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    )
    return max(0.0, centre - half_width), min(1.0, centre + half_width)


def _balanced_accuracy(samples: list[dict[str, Any]]) -> tuple[float | None, str | None]:
    outcome_classes = sorted({str(sample["outcome_direction"]) for sample in samples})
    if len(outcome_classes) < 2:
        return None, "requires at least two observed outcome classes"
    recalls = []
    for outcome in outcome_classes:
        matching = [sample for sample in samples if sample["outcome_direction"] == outcome]
        recalls.append(
            sum(sample["claim_direction"] == outcome for sample in matching) / len(matching)
        )
    return sum(recalls) / len(recalls), None


def compute_source_stats(
    name: str, samples: list[dict]
) -> SourceStats | None:
    """Compute correctly defined metrics for one source."""
    support = len(samples)
    if support < MIN_SAMPLE_PER_SOURCE:
        return None
    labels = [
        1.0 if sample["claim_direction"] == sample["outcome_direction"] else 0.0
        for sample in samples
    ]
    correct = int(sum(labels))
    accuracy = correct / support
    balanced_accuracy, balanced_accuracy_reason = _balanced_accuracy(samples)
    brier = sum(
        (float(sample["evidence_strength"]) - label) ** 2
        for sample, label in zip(samples, labels)
    ) / support
    shrinkage_weight = support / (support + SHRINKAGE_K)
    reliability = (
        accuracy * shrinkage_weight
        + SHRINKAGE_TARGET * (1.0 - shrinkage_weight)
    )
    return SourceStats(
        name=name,
        support=support,
        correct=correct,
        accuracy=accuracy,
        balanced_accuracy=balanced_accuracy,
        balanced_accuracy_reason=balanced_accuracy_reason,
        brier=brier,
        wilson_ci_95=wilson_interval(correct, support),
        reliability=reliability,
        shrinkage_weight=shrinkage_weight,
    )


def parse_cutoff(value: str) -> date:
    """Parse the required inclusive UTC cutoff in exact YYYY-MM-DD form."""
    try:
        cutoff = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--cutoff must be a valid YYYY-MM-DD UTC date") from exc
    if value != cutoff.isoformat():
        raise ValueError("--cutoff must be exactly YYYY-MM-DD")
    return cutoff


def parse_as_of(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("sample as_of must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid sample as_of: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"sample as_of must include a timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def parse_outcome_observed_at(value: Any) -> datetime:
    """Parse a label observation timestamp and require an explicit timezone."""
    if not isinstance(value, str) or not value:
        raise ValueError(
            "sample outcome_observed_at must be a non-empty timezone-aware "
            "ISO-8601 string"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid sample outcome_observed_at: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            f"sample outcome_observed_at must include a timezone: {value!r}"
        )
    return parsed.astimezone(timezone.utc)


def load_samples(path: str) -> list[dict]:
    samples: list[dict] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}") from exc
    return samples


def validate_sample(sample: Any, index: int) -> dict[str, Any]:
    """Validate fields consumed by reliability metrics before training."""
    if not isinstance(sample, dict):
        raise ValueError(f"sample row {index} must be a JSON object")
    required_strings = (
        "sample_id",
        "source",
        "source_family",
        "as_of",
        "outcome_observed_at",
        "claim_direction",
        "outcome_direction",
    )
    for field in required_strings:
        if not isinstance(sample.get(field), str) or not sample[field]:
            raise ValueError(
                f"sample row {index} field {field} must be a non-empty string"
            )
    if sample["source_family"] not in SOURCE_FAMILIES:
        allowed = ", ".join(sorted(SOURCE_FAMILIES))
        raise ValueError(
            f"sample row {index} source_family must be one of: {allowed}"
        )
    strength = sample.get("evidence_strength")
    if isinstance(strength, bool) or not isinstance(strength, (int, float)):
        raise ValueError(
            f"sample row {index} evidence_strength must be a finite number in [0, 1]"
        )
    if not math.isfinite(float(strength)) or not 0.0 <= float(strength) <= 1.0:
        raise ValueError(
            f"sample row {index} evidence_strength must be a finite number in [0, 1]"
        )
    return sample


def build_artifact(samples_path: Path, cutoff_text: str) -> dict[str, Any]:
    cutoff = parse_cutoff(cutoff_text)
    all_samples = load_samples(str(samples_path))
    selected: list[tuple[datetime, dict[str, Any]]] = []
    excluded_after_cutoff = 0
    labels_validated = 0
    sample_ids: set[str] = set()
    for index, raw_sample in enumerate(all_samples, start=1):
        sample = validate_sample(raw_sample, index)
        sample_id = sample["sample_id"]
        if sample_id in sample_ids:
            raise ValueError(f"duplicate sample_id: {sample_id!r}")
        sample_ids.add(sample_id)
        as_of = parse_as_of(sample.get("as_of"))
        if as_of.date() <= cutoff:
            outcome_observed_at = parse_outcome_observed_at(
                sample.get("outcome_observed_at")
            )
            if outcome_observed_at <= as_of:
                raise ValueError(
                    "sample outcome_observed_at must be strictly after as_of: "
                    f"{sample.get('sample_id', '<unknown>')} "
                    f"({outcome_observed_at.isoformat()} <= {as_of.isoformat()})"
                )
            if outcome_observed_at.date() > cutoff:
                raise ValueError(
                    "sample outcome_observed_at is after inclusive UTC cutoff: "
                    f"{sample.get('sample_id', '<unknown>')} "
                    f"({outcome_observed_at.isoformat()} > {cutoff.isoformat()})"
                )
            labels_validated += 1
            selected.append((as_of, sample))
        else:
            excluded_after_cutoff += 1
    selected.sort(
        key=lambda item: (
            item[0],
            str(item[1].get("source", "")),
            str(item[1].get("sample_id", "")),
        )
    )

    by_source: dict[str, list[dict[str, Any]]] = {}
    for _, sample in selected:
        by_source.setdefault(str(sample["source"]), []).append(sample)

    sources: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for source, source_samples in sorted(by_source.items()):
        stats = compute_source_stats(source, source_samples)
        if stats is None:
            warnings.append(
                f"Source {source} has {len(source_samples)} samples; minimum is "
                f"{MIN_SAMPLE_PER_SOURCE}"
            )
            continue
        payload = asdict(stats)
        payload.pop("name")
        payload["wilson_ci_95"] = list(payload["wilson_ci_95"])
        sources[source] = payload

    if len(sources) < 2:
        warnings.append("Fewer than two eligible sources; promotion is not supported")

    canonical = b"".join(
        json.dumps(sample, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
        for _, sample in selected
    )
    timestamps = [as_of for as_of, _ in selected]
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "status": "research-only",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "training_cutoff_utc": cutoff.isoformat(),
        "cutoff_inclusive": True,
        "sample_time_range_utc": {
            "min": min(timestamps).isoformat() if timestamps else None,
            "max": max(timestamps).isoformat() if timestamps else None,
        },
        "provenance": {
            "input_path": str(samples_path),
            "input_sha256": hashlib.sha256(samples_path.read_bytes()).hexdigest(),
            "selected_dataset_sha256": hashlib.sha256(canonical).hexdigest(),
            "input_samples": len(all_samples),
            "selected_samples": len(selected),
            "excluded_after_cutoff": excluded_after_cutoff,
            "labels_validated_at_or_before_cutoff": labels_validated,
            "label_timestamp_missing": 0,
            "label_timestamp_invalid": 0,
            "label_temporal_order_invalid": 0,
            "label_observed_after_cutoff": 0,
            "duplicate_sample_id": 0,
            "invalid_source_family": 0,
        },
        "parameters": {
            "minimum_support": MIN_SAMPLE_PER_SOURCE,
            "shrinkage_target": SHRINKAGE_TARGET,
            "shrinkage_k": SHRINKAGE_K,
        },
        "sources": sources,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/model-artifacts/source_reputation_v2.json"),
    )
    parser.add_argument(
        "--cutoff",
        required=True,
        help="Inclusive UTC training cutoff (YYYY-MM-DD)",
    )
    args = parser.parse_args()
    try:
        artifact = build_artifact(args.samples, args.cutoff)
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "artifact": str(args.out),
                "sources": len(artifact["sources"]),
                "selected_samples": artifact["provenance"]["selected_samples"],
                "warnings": len(artifact["warnings"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
