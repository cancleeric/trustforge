"""Direct and subprocess contract tests for the source-reliability trainer."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "train_source_reliability.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("source_reliability_trainer", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


trainer = _load_module()


def _sample(index: int, *, as_of: str, correct: bool = True) -> dict:
    outcome = "bullish" if index % 2 else "bearish"
    claim = outcome if correct else ("bearish" if outcome == "bullish" else "bullish")
    return {
        "sample_id": f"sample-{index}",
        "source": "provider-a",
        "source_family": "sentiment",
        "as_of": as_of,
        "outcome_observed_at": "2026-07-27T23:59:59Z",
        "claim_direction": claim,
        "outcome_direction": outcome,
        "evidence_strength": 0.8 if correct else 0.2,
    }


def _write_jsonl(path: Path, samples: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(sample) + "\n" for sample in samples),
        encoding="utf-8",
    )


def test_source_stats_report_honest_metrics_without_auc_proxy():
    samples = [
        _sample(i, as_of="2026-07-01T00:00:00Z", correct=i < 24)
        for i in range(30)
    ]
    stats = trainer.compute_source_stats("provider-a", samples)
    assert stats is not None
    assert stats.support == 30
    assert stats.correct == 24
    assert stats.accuracy == pytest.approx(0.8)
    assert stats.balanced_accuracy == pytest.approx(0.8)
    assert stats.brier == pytest.approx(0.04)
    assert stats.wilson_ci_95[0] < stats.accuracy < stats.wilson_ci_95[1]
    assert not hasattr(stats, "auc_proxy")


def test_balanced_accuracy_is_null_for_single_outcome_class():
    samples = [
        {
            **_sample(i, as_of="2026-07-01", correct=True),
            "claim_direction": "bullish",
            "outcome_direction": "bullish",
        }
        for i in range(30)
    ]
    stats = trainer.compute_source_stats("provider-a", samples)
    assert stats is not None
    assert stats.balanced_accuracy is None
    assert "two observed outcome classes" in stats.balanced_accuracy_reason


def test_artifact_cutoff_is_utc_date_inclusive_and_has_provenance(tmp_path: Path):
    path = tmp_path / "samples.jsonl"
    samples = [
        _sample(i, as_of="2026-07-27T23:59:59-04:00", correct=True)
        for i in range(30)
    ]
    samples += [
        _sample(i + 30, as_of="2026-07-27T12:00:00Z", correct=True)
        for i in range(30)
    ]
    _write_jsonl(path, samples)

    artifact = trainer.build_artifact(path, "2026-07-27")

    # The offset timestamps normalise to 2026-07-28 UTC and are excluded.
    assert artifact["training_cutoff_utc"] == "2026-07-27"
    assert artifact["cutoff_inclusive"] is True
    assert artifact["provenance"]["input_samples"] == 60
    assert artifact["provenance"]["selected_samples"] == 30
    assert artifact["provenance"]["excluded_after_cutoff"] == 30
    assert artifact["provenance"]["labels_validated_at_or_before_cutoff"] == 30
    assert artifact["provenance"]["label_timestamp_missing"] == 0
    assert artifact["provenance"]["label_timestamp_invalid"] == 0
    assert artifact["provenance"]["label_temporal_order_invalid"] == 0
    assert artifact["provenance"]["label_observed_after_cutoff"] == 0
    assert artifact["sample_time_range_utc"]["max"] == "2026-07-27T12:00:00+00:00"
    assert len(artifact["provenance"]["input_sha256"]) == 64
    assert len(artifact["provenance"]["selected_dataset_sha256"]) == 64


@pytest.mark.parametrize("cutoff", ["1785150122", "2026-7-1", "2026-02-30"])
def test_cutoff_rejects_non_date_values(cutoff: str):
    with pytest.raises(ValueError, match="cutoff"):
        trainer.parse_cutoff(cutoff)


@pytest.mark.parametrize(
    "observed_at",
    ["2026-07-26T23:59:59Z", "2026-07-27T23:59:59Z"],
)
def test_label_observation_before_or_equal_cutoff_is_accepted(
    tmp_path: Path, observed_at: str
):
    path = tmp_path / "samples.jsonl"
    samples = [
        {
            **_sample(i, as_of="2026-07-20T00:00:00Z"),
            "outcome_observed_at": observed_at,
        }
        for i in range(30)
    ]
    _write_jsonl(path, samples)
    artifact = trainer.build_artifact(path, "2026-07-27")
    assert artifact["provenance"]["labels_validated_at_or_before_cutoff"] == 30


def test_label_observation_after_cutoff_fails_closed(tmp_path: Path):
    path = tmp_path / "samples.jsonl"
    sample = {
        **_sample(1, as_of="2026-07-20T00:00:00Z"),
        "outcome_observed_at": "2026-07-28T00:00:00Z",
    }
    _write_jsonl(path, [sample])
    with pytest.raises(ValueError, match="after inclusive UTC cutoff"):
        trainer.build_artifact(path, "2026-07-27")


@pytest.mark.parametrize(
    "observed_at",
    ["2026-07-19T23:59:59Z", "2026-07-20T00:00:00Z"],
)
def test_label_observation_before_or_equal_as_of_fails_closed(
    tmp_path: Path, observed_at: str
):
    path = tmp_path / "samples.jsonl"
    sample = {
        **_sample(1, as_of="2026-07-20T00:00:00Z"),
        "outcome_observed_at": observed_at,
    }
    _write_jsonl(path, [sample])
    with pytest.raises(ValueError, match="strictly after as_of"):
        trainer.build_artifact(path, "2026-07-27")


def test_missing_label_observation_fails_closed(tmp_path: Path):
    path = tmp_path / "samples.jsonl"
    sample = _sample(1, as_of="2026-07-20T00:00:00Z")
    sample.pop("outcome_observed_at")
    _write_jsonl(path, [sample])
    with pytest.raises(ValueError, match="outcome_observed_at"):
        trainer.build_artifact(path, "2026-07-27")


@pytest.mark.parametrize("observed_at", ["not-a-date", "2026-07-27T12:00:00"])
def test_invalid_or_naive_label_observation_fails_closed(
    tmp_path: Path, observed_at: str
):
    path = tmp_path / "samples.jsonl"
    sample = {
        **_sample(1, as_of="2026-07-20T00:00:00Z"),
        "outcome_observed_at": observed_at,
    }
    _write_jsonl(path, [sample])
    with pytest.raises(ValueError, match="outcome_observed_at"):
        trainer.build_artifact(path, "2026-07-27")


def test_naive_as_of_fails_closed(tmp_path: Path):
    path = tmp_path / "samples.jsonl"
    _write_jsonl(path, [_sample(1, as_of="2026-07-20T00:00:00")])
    with pytest.raises(ValueError, match="as_of must include a timezone"):
        trainer.build_artifact(path, "2026-07-27")


@pytest.mark.parametrize("strength", [float("nan"), float("inf"), -0.01, 1.01, "0.5"])
def test_invalid_evidence_strength_fails_closed(tmp_path: Path, strength):
    path = tmp_path / "samples.jsonl"
    sample = {
        **_sample(1, as_of="2026-07-20T00:00:00Z"),
        "evidence_strength": strength,
    }
    _write_jsonl(path, [sample])
    with pytest.raises(ValueError, match="evidence_strength"):
        trainer.build_artifact(path, "2026-07-27")


def test_non_object_row_fails_closed(tmp_path: Path):
    path = tmp_path / "samples.jsonl"
    path.write_text('["not", "an", "object"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        trainer.build_artifact(path, "2026-07-27")


def test_duplicate_sample_id_fails_closed(tmp_path: Path):
    path = tmp_path / "samples.jsonl"
    first = _sample(1, as_of="2026-07-20T00:00:00Z")
    duplicate = {
        **_sample(2, as_of="2026-07-20T00:00:00Z"),
        "sample_id": first["sample_id"],
    }
    _write_jsonl(path, [first, duplicate])
    with pytest.raises(ValueError, match="duplicate sample_id"):
        trainer.build_artifact(path, "2026-07-27")


@pytest.mark.parametrize("family", [None, "", "social", "SENTIMENT"])
def test_missing_or_invalid_source_family_fails_closed(tmp_path: Path, family):
    path = tmp_path / "samples.jsonl"
    sample = _sample(1, as_of="2026-07-20T00:00:00Z")
    if family is None:
        sample.pop("source_family")
    else:
        sample["source_family"] = family
    _write_jsonl(path, [sample])
    with pytest.raises(ValueError, match="source_family"):
        trainer.build_artifact(path, "2026-07-27")


@pytest.mark.parametrize(
    "field",
    [
        "sample_id",
        "source",
        "source_family",
        "as_of",
        "outcome_observed_at",
        "claim_direction",
        "outcome_direction",
    ],
)
def test_whitespace_only_required_string_fails_closed(tmp_path: Path, field: str):
    path = tmp_path / "samples.jsonl"
    sample = _sample(1, as_of="2026-07-20T00:00:00Z")
    sample[field] = " \t "
    _write_jsonl(path, [sample])
    with pytest.raises(ValueError, match=f"field {field}"):
        trainer.build_artifact(path, "2026-07-27")


def test_cli_writes_v2_contract_and_filters_after_cutoff(tmp_path: Path):
    samples_path = tmp_path / "samples.jsonl"
    out_path = tmp_path / "artifact.json"
    samples = [
        _sample(i, as_of="2026-07-27T12:00:00Z", correct=i < 20)
        for i in range(30)
    ]
    samples.append(_sample(31, as_of="2026-07-28T00:00:00Z"))
    _write_jsonl(samples_path, samples)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--samples",
            str(samples_path),
            "--out",
            str(out_path),
            "--cutoff",
            "2026-07-27",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    artifact = json.loads(out_path.read_text(encoding="utf-8"))
    assert artifact["schema"] == "trustforge.source-reputation"
    assert artifact["version"] == "2.0.0"
    assert artifact["provenance"]["selected_samples"] == 30
    assert artifact["sources"]["provider-a"]["support"] == 30
    assert artifact["provenance"]["duplicate_sample_id"] == 0
    assert artifact["provenance"]["invalid_source_family"] == 0
    assert "auc_proxy" not in json.dumps(artifact)


def test_cli_rejects_epoch_cutoff(tmp_path: Path):
    samples_path = tmp_path / "samples.jsonl"
    _write_jsonl(samples_path, [_sample(1, as_of="2026-07-27")])
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--samples",
            str(samples_path),
            "--cutoff",
            "1785150122",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "YYYY-MM-DD" in result.stderr


def test_cli_rejects_label_observed_after_cutoff(tmp_path: Path):
    samples_path = tmp_path / "samples.jsonl"
    sample = {
        **_sample(1, as_of="2026-07-20T00:00:00Z"),
        "outcome_observed_at": "2026-07-28T00:00:00Z",
    }
    _write_jsonl(samples_path, [sample])
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--samples",
            str(samples_path),
            "--cutoff",
            "2026-07-27",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "after inclusive UTC cutoff" in result.stderr


def test_cli_rejects_label_not_after_as_of(tmp_path: Path):
    samples_path = tmp_path / "samples.jsonl"
    sample = {
        **_sample(1, as_of="2026-07-20T00:00:00Z"),
        "outcome_observed_at": "2026-07-20T00:00:00Z",
    }
    _write_jsonl(samples_path, [sample])
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--samples",
            str(samples_path),
            "--cutoff",
            "2026-07-27",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "strictly after as_of" in result.stderr


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate", "duplicate sample_id"),
        ("invalid-family", "source_family"),
    ],
)
def test_cli_rejects_duplicate_id_or_invalid_family(
    tmp_path: Path, mutation: str, message: str
):
    samples_path = tmp_path / "samples.jsonl"
    first = _sample(1, as_of="2026-07-20T00:00:00Z")
    second = _sample(2, as_of="2026-07-20T00:00:00Z")
    if mutation == "duplicate":
        second["sample_id"] = first["sample_id"]
    else:
        second["source_family"] = "social"
    _write_jsonl(samples_path, [first, second])
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--samples",
            str(samples_path),
            "--cutoff",
            "2026-07-27",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert message in result.stderr


def test_cli_rejects_whitespace_only_required_string(tmp_path: Path):
    samples_path = tmp_path / "samples.jsonl"
    sample = _sample(1, as_of="2026-07-20T00:00:00Z")
    sample["sample_id"] = "   "
    _write_jsonl(samples_path, [sample])
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--samples",
            str(samples_path),
            "--cutoff",
            "2026-07-27",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "field sample_id must be a non-empty string" in result.stderr
