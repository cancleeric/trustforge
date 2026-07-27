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
        "outcome_observed_at": f"2026-02-{day:02d}T00:00:00Z",
    }


def test_global_date_split_keeps_same_day_together_across_coin_calendars():
    rows = [_row(day) for day in range(1, 9)]
    rows += [_row(day, "ETH", "onchain") for day in (2, 4, 6, 8)]
    for row in rows:
        row["_date"] = row["as_of"][:10]

    split = conformal.chronological_split(rows)

    calibration_dates = {row["_date"] for row in split.calibration}
    held_dates = {row["_date"] for row in split.held_out}
    assert calibration_dates.isdisjoint(held_dates)
    assert max(calibration_dates) < min(held_dates)
    assert split.calibration_end < split.held_out_start
    assert {row["coin"] for row in split.held_out} == {"BTC", "ETH"}


def test_backtest_global_boundaries_do_not_use_btc_calendar():
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
