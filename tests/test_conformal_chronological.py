"""Direct and CLI regression tests for #752 chronological conformal research."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


conformal = _load("conformal_on_samples_test", _ROOT / "scripts/conformal_on_samples.py")
backtest = _load("backtest_conformal_chrono_test", _ROOT / "scripts/backtest_conformal.py")


def _row(day: int, coin: str = "BTC", family: str = "sentiment") -> dict:
    return {
        "sample_id": f"{coin}-{day}-{family}",
        "coin": coin,
        "as_of": f"2026-01-{day:02d}T00:00:00Z",
        "source": family,
        "source_family": family,
        "claim_direction": "bullish" if day % 2 else "bearish",
        "evidence_strength": 0.4 + day / 100,
        "outcome_direction": "bearish" if day % 3 else "bullish",
        "outcome_observed_at": f"2026-01-{day + 1:02d}T00:00:00Z",
    }


def _internal(rows: list[dict], tmp_path: Path) -> list[dict]:
    path = tmp_path / "internal.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return conformal.load_samples(str(path))


def test_global_date_split_keeps_same_day_together_across_coin_calendars(tmp_path: Path):
    rows = [_row(day) for day in range(1, 9)]
    rows += [_row(day, "ETH", "onchain") for day in (2, 4, 6, 8)]
    rows = _internal(rows, tmp_path)

    split = conformal.chronological_split(rows)

    calibration_dates = {row["_date"] for row in split.calibration}
    held_dates = {row["_date"] for row in split.held_out}
    assert calibration_dates.isdisjoint(held_dates)
    assert max(calibration_dates) < min(held_dates)
    assert split.calibration_end < split.held_out_start
    assert max(row["_outcome_utc"] for row in split.calibration) < min(
        row["_as_of_utc"] for row in split.held_out
    )
    assert {row["coin"] for row in split.held_out} == {"BTC", "ETH"}


def test_backtest_global_boundaries_do_not_use_btc_calendar(monkeypatch):
    monkeypatch.setattr(backtest, "FORWARD_DAYS", 0)
    samples = {
        "BTC": [
            backtest.Sample("BTC", f"2026-01-{day:02d}", 0.5, False)
            for day in (1, 3, 5, 7, 9, 11, 13)
        ],
        "ETH": [
            backtest.Sample("ETH", f"2026-01-{day:02d}", 0.5, False)
            for day in (2, 4, 6, 8, 10, 12, 14)
        ],
    }
    calibration, held, calib_start, held_start = backtest._chronological_partitions(samples)
    assert calib_start == "2026-01-10"
    assert held_start == "2026-01-12"
    assert max(row.date for row in calibration) < min(row.date for row in held)


def test_signal_builder_does_not_read_future_bar():
    bars = [
        backtest.Bar(date=f"2026-01-{day:02d}", open=100, high=101, low=99,
                     close=100 + day, volume=1000 + day)
        for day in range(1, 32)
    ]
    changed_future = list(bars)
    changed_future[-1] = backtest.Bar(
        date=bars[-1].date, open=1, high=9999, low=1, close=9999, volume=999999
    )
    first = backtest._build_signals("BTC", bars, 29, "up", bars[29].date)
    second = backtest._build_signals("BTC", changed_future, 29, "up", bars[29].date)
    assert [(x.claim.id, x.trust) for group in first for x in group] == [
        (x.claim.id, x.trust) for group in second for x in group
    ]


def test_wrong_strength_upper_quantile_is_not_reversed():
    strengths = [index / 10 for index in range(1, 11)]
    flags = [0] * len(strengths)
    assert conformal.conformal_threshold(strengths, flags, alpha=0.1) == 1.0
    assert conformal.conformal_threshold(strengths[:-2], flags[:-2], alpha=0.1) == float("inf")


def test_backtest_purges_forward_outcomes_at_heldout_boundary():
    samples = {
        "BTC": [
            backtest.Sample("BTC", f"2026-01-{day:02d}", 0.5, False)
            for day in range(1, 31)
        ]
    }
    calibration, held, _, held_start = backtest._chronological_partitions(samples)
    assert max(
        backtest._dt.strptime(row.date, "%Y-%m-%d")
        + backtest._td(days=backtest.FORWARD_DAYS)
        for row in calibration
    ) < backtest._dt.strptime(held_start, "%Y-%m-%d")
    assert min(row.date for row in held) == held_start


def test_missing_or_malformed_heterogeneous_inputs_block_promotion(tmp_path: Path):
    malformed = tmp_path / "fng.jsonl"
    malformed.write_text("{broken")
    assert backtest._load_fng_index(malformed) == {}
    sample = backtest.Sample(
        "BTC", "2026-01-01", 0.8, False,
        frozenset({"price", "sentiment", "onchain"}),
    )
    ready, _, _ = backtest._heterogeneous_ready(
        [sample], [sample], {}, {"2026-01-01": {"hash-rate": 1.0}}
    )
    assert ready is False


def test_offset_timestamps_are_normalized_to_utc_date(tmp_path: Path):
    first = _row(1)
    second = _row(1, "ETH", "onchain")
    first["as_of"] = "2026-01-02T01:00:00+02:00"
    second["as_of"] = "2026-01-01T23:00:00Z"
    first["outcome_observed_at"] = second["outcome_observed_at"] = "2026-01-03T00:00:00Z"
    path = tmp_path / "offset.jsonl"
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
    rows = conformal.load_samples(str(path))
    assert {row["_date"] for row in rows} == {"2026-01-01"}


def test_partition_family_gate_requires_heterogeneity_in_each_partition(tmp_path: Path):
    rows = [_row(day, "BTC", "sentiment") for day in range(1, 9)]
    rows = _internal(rows, tmp_path)
    split = conformal.chronological_split(rows)
    report = conformal.build_report(rows, "0" * 64, split, 0.5)
    assert report["promotion_checks"]["calibration_source_families"] is False
    assert report["promotion_checks"]["held_out_source_families"] is False
    assert report["promotion_checks"]["all_pass"] is False


@pytest.mark.parametrize("family", ["fake-a", "fake-b", "", "SENTIMENT"])
def test_cli_rejects_non_contract_source_families(tmp_path: Path, family: str):
    rows = [_row(day, family=family) for day in range(1, 9)]
    samples = tmp_path / "invalid-family.jsonl"
    output = tmp_path / "report.json"
    samples.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = subprocess.run(
        [
            sys.executable, str(_ROOT / "scripts/conformal_on_samples.py"),
            "--samples", str(samples), "--out", str(output),
        ],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "invalid source_family" in result.stderr
    assert not output.exists()


def test_duplicate_sample_id_fails_closed_direct_and_cli(tmp_path: Path):
    rows = [_row(day) for day in range(1, 9)]
    rows[1]["sample_id"] = rows[0]["sample_id"]
    samples = tmp_path / "duplicate.jsonl"
    output = tmp_path / "report.json"
    samples.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(conformal.DatasetError, match="duplicate sample_id"):
        conformal.load_samples(str(samples))
    result = subprocess.run(
        [
            sys.executable, str(_ROOT / "scripts/conformal_on_samples.py"),
            "--samples", str(samples), "--out", str(output),
        ],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "duplicate sample_id" in result.stderr
    assert not output.exists()


def test_backtest_success_copy_remains_research_only():
    source = (_ROOT / "scripts/backtest_conformal.py").read_text()
    assert "ALL P1-P5 PASS" in source
    assert "research evidence only; NOT promotion approval" in source
    assert "conformal._CONFORMAL_TAU =" not in source


@pytest.mark.parametrize("payload", ["", "{bad json}\n", json.dumps(_row(1)) + "\n"])
def test_cli_fails_closed_for_empty_malformed_or_small_dataset(tmp_path: Path, payload: str):
    samples = tmp_path / "samples.jsonl"
    output = tmp_path / "report.json"
    samples.write_text(payload)
    result = subprocess.run(
        [
            sys.executable, str(_ROOT / "scripts/conformal_on_samples.py"),
            "--samples", str(samples), "--out", str(output),
        ],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "ERROR:" in result.stderr
    assert not output.exists()


def test_cli_writes_honest_research_report_with_boundaries_digest_and_counts(tmp_path: Path):
    samples = tmp_path / "samples.jsonl"
    output = tmp_path / "report.json"
    rows = [_row(day, "BTC", "sentiment") for day in range(1, 9)]
    rows += [_row(day, "ETH", "onchain") for day in range(1, 9)]
    samples.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = subprocess.run(
        [
            sys.executable, str(_ROOT / "scripts/conformal_on_samples.py"),
            "--samples", str(samples), "--out", str(output), "--alpha", "0.5",
        ],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())
    assert report["status"] == "research-only"
    assert report["production_wiring_allowed"] is False
    assert len(report["input_sha256"]) == 64
    assert report["split"]["calibration_strictly_before_held_out"] is True
    assert report["counts"]["per_family"] == {"onchain": 8, "sentiment": 8}
    assert report["counts"]["per_coin"] == {"BTC": 8, "ETH": 8}
    assert "auc_proxy" not in json.dumps(report)
