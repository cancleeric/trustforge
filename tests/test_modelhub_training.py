import json
from datetime import date, timedelta

from scripts import prepare_calibrator_training
import pytest

from trustforge.modelhub_training import (
    TrainingDataError,
    build_calibrator_training_package,
    build_flat_training_package,
    load_flat_training_rows,
)


def _labels(count: int) -> list[dict]:
    return [{"labels": [{
        "date": f"2021-01-{day:03d}",
        "coin": "BTC",
        "direction": "偏多",
        "calibrated_confidence": 0.6,
        "ohlcv_lineage": {"sha256": "ohlcv-pin"},
        "outcomes": {"T+1": {
            "status": "labeled", "hit": bool(day % 2), "directional_return_pct": 1.2,
            "start_close": 1, "end_close": 2,
        }},
    } for day in range(1, count + 1)]}]


def test_training_package_blocks_without_eligible_data():
    package = build_calibrator_training_package(_labels(99))
    assert package["status"] == "blocked"
    assert package["blocked_reason"] == "insufficient_eligible_outcomes"
    assert package["network_action"] == "none"


def test_training_package_pins_dataset_and_time_split():
    package = build_calibrator_training_package(_labels(100))
    assert package["status"] == "ready_for_modelhub_dry_run"
    assert package["dataset"]["row_count"] == 100
    assert len(package["dataset"]["sha256"]) == 64
    assert package["split"] == {
        "strategy": "chronological_80_20", "train_count": 80, "holdout_count": 20,
        "train_end": "2021-01-080", "holdout_start": "2021-01-081",
    }
    assert package["modelhub_submission_draft"]["candidate_architectures"] == ["sklearn-logreg", "isotonic"]


def test_prepare_script_writes_non_networked_package(tmp_path):
    labels = tmp_path / "btc-labels.json"
    output = tmp_path / "package.json"
    labels.write_text(json.dumps(_labels(1)), encoding="utf-8")
    assert prepare_calibrator_training.main(["--labels", str(labels), "--out", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "blocked"


def _flat(count=100, *, coin="BTC"):
    start = date(2020, 1, 1)
    return [{
        "date": (start + timedelta(days=index)).isoformat(), "coin": coin, "direction": "不明",
        "confidence": 0.5, "outcome_pct": 0.0, "ground_truth_direction": "neutral",
        "split": "train" if index < int(count * 0.8) else "val",
    } for index in range(count)]


def test_flat_loader_adapts_real_schema_and_hash_is_deterministic(tmp_path):
    path = tmp_path / "BTC.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in _flat()), encoding="utf-8")
    rows = load_flat_training_rows(path, coin="BTC")
    first = build_flat_training_package(rows)
    second = build_flat_training_package(list(reversed(rows)))
    assert rows[0]["hit"] is True
    assert first["status"] == "ready_for_modelhub_dry_run"
    assert first["dataset"]["sha256"] == second["dataset"]["sha256"]


@pytest.mark.parametrize("mutation", ["partial_label", "bad_coin", "nan", "bad_split"])
def test_flat_loader_rejects_bad_schema(tmp_path, mutation):
    rows = _flat()
    if mutation == "partial_label":
        rows[0].pop("split")
    elif mutation == "bad_coin":
        rows[0]["coin"] = "ETH"
    elif mutation == "nan":
        rows[0]["confidence"] = float("nan")
    else:
        rows[0]["split"] = "test"
    path = tmp_path / "BTC.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    with pytest.raises(TrainingDataError):
        load_flat_training_rows(path, coin="BTC")


def test_loader_skips_unlabelled_and_missing_confidence_rows(tmp_path):
    rows = _flat()
    rows[0].pop("confidence")
    rows.append({"date": "2025-01-01", "coin": "BTC", "direction": "不明"})
    rows.append({
        "date": "2025-01-02", "coin": "BTC", "direction": "不明", "confidence": 0.5,
        "outcome_pct": None, "ground_truth_direction": None, "split": "val",
    })
    path = tmp_path / "BTC.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    loaded = load_flat_training_rows(path, coin="BTC")
    assert len(loaded) == 99
    assert all("sample_id" in row for row in loaded)


def test_duplicate_dates_have_stable_distinct_sample_ids(tmp_path):
    rows = _flat()
    duplicate = dict(rows[0])
    rows.insert(1, duplicate)
    path = tmp_path / "BTC.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    loaded = load_flat_training_rows(path, coin="BTC")
    assert loaded[0]["date"] == loaded[1]["date"]
    assert loaded[0]["sample_id"] != loaded[1]["sample_id"]
