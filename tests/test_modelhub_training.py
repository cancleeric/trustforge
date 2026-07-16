import json

from scripts import prepare_calibrator_training
from trustforge.modelhub_training import build_calibrator_training_package


def _labels(count: int) -> list[dict]:
    return [{"labels": [{
        "date": f"2021-01-{day:03d}",
        "coin": "BTC",
        "direction": "偏多",
        "calibrated_confidence": 0.6,
        "ohlcv_lineage": {"sha256": "ohlcv-pin"},
        "outcomes": {"T+1": {"status": "labeled", "hit": bool(day % 2), "directional_return_pct": 1.2, "start_close": 1, "end_close": 2}},
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
    assert package["split"] == {"strategy": "chronological_80_20", "train_count": 80, "holdout_count": 20, "train_end": "2021-01-080", "holdout_start": "2021-01-081"}
    assert package["modelhub_submission_draft"]["candidate_architectures"] == ["sklearn-logreg", "isotonic"]


def test_prepare_script_writes_non_networked_package(tmp_path):
    labels = tmp_path / "btc-labels.json"
    output = tmp_path / "package.json"
    labels.write_text(json.dumps(_labels(1)), encoding="utf-8")
    assert prepare_calibrator_training.main(["--labels", str(labels), "--out", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "blocked"
