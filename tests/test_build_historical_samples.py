from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "build_historical_samples.py"
SPEC = importlib.util.spec_from_file_location("build_historical_samples", SCRIPT)
assert SPEC and SPEC.loader
samples = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(samples)


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    fng = tmp_path / "fng.jsonl"
    rows = [
        {"coin": coin, "published_at": "2026-01-01T00:00:00Z", "value": 20,
         "classification": "Extreme Fear"}
        for coin in ("ETH", "BTC", "SOL")
    ]
    fng.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    ohlcv = tmp_path / "ohlcv"
    ohlcv.mkdir()
    (ohlcv / "BTC_daily_ohlcv.csv").write_text(
        "date,close\n2026-01-01,100\n2026-01-02,110\n", encoding="utf-8"
    )
    replay = tmp_path / "replay"
    replay.mkdir()
    (replay / "btc-2026-01-01.json").write_text(json.dumps({
        "snapshot_at": "2026-01-01T12:00:00Z",
        "coin": "BTC",
        "report": {"direction": "bullish", "calibrated_confidence": 0.7},
        "evidence": [
            {
                "source": "blockchain-com-charts",
                "provider": "Blockchain.com",
                "kind": "onchain",
                "published_at": "2026-01-01T08:00:00Z",
            },
            {
                "source": "ohlcv-csv",
                "provider": "HOYA BIT",
                "kind": "price",
                "visible_at": "2026-01-01T09:00:00Z",
            },
        ],
    }), encoding="utf-8")
    return fng, ohlcv, replay


def test_same_day_families_preserved_without_fng_inflation(tmp_path: Path) -> None:
    fng, ohlcv, replay = _write_inputs(tmp_path)
    result, counters = samples.build_samples(
        fng_path=fng, replay_dir=replay, ohlcv_path=ohlcv / "BTC_daily_ohlcv.csv",
        coin="BTC", horizon=1, cutoff=date(2026, 1, 1),
    )
    assert [row["source_family"] for row in result] == ["sentiment", "onchain", "price"]
    assert sum(row["source"] == "alternative-me-fng" for row in result) == 1
    assert counters["fng_duplicate_expansion"] == 2
    assert all(row["training_cutoff"] == "2026-01-01" for row in result)


@pytest.mark.parametrize("timestamp", [None, "not-a-time", "2026-01-02T00:00:00Z"])
def test_pit_rejects_missing_invalid_and_future_evidence(timestamp: str | None) -> None:
    counter: Counter[str] = Counter()
    evidence = {"source": "blockchain-com-charts", "kind": "onchain"}
    if timestamp is not None:
        evidence["published_at"] = timestamp
    result = samples.extract_replay_evidence({
        "snapshot_at": "2026-01-01T00:00:00Z",
        "coin": "BTC",
        "report": {"direction": "bullish", "calibrated_confidence": 0.6},
        "evidence": [evidence],
    }, counter, "BTC")
    assert result == []
    key = "future_evidence" if timestamp and timestamp.startswith("2026-01-02") else "missing_or_invalid_timestamp"
    assert counter[key] == 1


def test_hostile_python_string_is_never_evaluated(tmp_path: Path) -> None:
    marker = tmp_path / "owned"
    payload = f"__import__('pathlib').Path({str(marker)!r}).write_text('owned')"
    counter: Counter[str] = Counter()
    result = samples.extract_replay_evidence({
        "snapshot_at": "2026-01-01T00:00:00Z",
        "report": payload,
        "evidence": payload,
    }, counter, "BTC")
    assert result == []
    assert not marker.exists()
    assert counter["malformed_input"] == 1


@pytest.mark.parametrize("bad_kind", ["future", "missing", "malformed"])
def test_snapshot_rejects_all_evidence_when_any_item_is_invalid(bad_kind: str) -> None:
    bad: object
    if bad_kind == "malformed":
        bad = "not-an-object"
    else:
        bad = {
            "source": "ohlcv-csv",
            "kind": "price",
            **({"visible_at": "2026-01-02T00:00:00Z"} if bad_kind == "future" else {}),
        }
    counter: Counter[str] = Counter()
    result = samples.extract_replay_evidence({
        "snapshot_at": "2026-01-01T12:00:00Z",
        "coin": "BTC",
        "report": {"direction": "bullish", "calibrated_confidence": 0.9},
        "evidence": [
            {
                "source": "blockchain-com-charts",
                "kind": "onchain",
                "visible_at": "2026-01-01T08:00:00Z",
            },
            bad,
        ],
    }, counter, "BTC")
    assert result == []
    assert counter["rejected_snapshots"] == 1


@pytest.mark.parametrize("snapshot_coin", [None, "DOGE", "ETH"])
def test_snapshot_coin_must_be_supported_and_match_request(snapshot_coin: str | None) -> None:
    counter: Counter[str] = Counter()
    result = samples.extract_replay_evidence({
        "snapshot_at": "2026-01-01T12:00:00Z",
        "coin": snapshot_coin,
        "report": {"direction": "bullish", "calibrated_confidence": 0.9},
        "evidence": [{
            "source": "ohlcv-csv",
            "kind": "price",
            "visible_at": "2026-01-01T08:00:00Z",
        }],
    }, counter, "BTC")
    assert result == []
    assert counter["snapshot_coin_mismatch"] == 1


def test_replay_loader_fails_closed_on_oversize_unicode_and_deep_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay = tmp_path / "replay"
    replay.mkdir()
    (replay / "oversize.json").write_text("{}", encoding="utf-8")
    (replay / "unicode.json").write_bytes(b"\xff")
    (replay / "deep.json").write_text("[" * 2_000 + "]" * 2_000, encoding="utf-8")
    monkeypatch.setattr(samples, "_MAX_INPUT_BYTES", 1)
    counter: Counter[str] = Counter()
    with pytest.raises(samples.ReplayInputError):
        list(samples._load_replay_snapshots(replay, counter))
    assert counter["input_too_large"] == 1


@pytest.mark.parametrize(
    ("field", "spoof"),
    [("kind", "sentiment"), ("provider", "Evil Inc"), ("scope", "market-wide")],
)
def test_known_source_identity_spoof_rejects_entire_snapshot(
    field: str, spoof: str
) -> None:
    item = {
        "source": "blockchain-com-charts",
        "kind": "onchain",
        "provider": "Blockchain.com",
        "scope": "per-coin",
        "visible_at": "2026-01-01T08:00:00Z",
    }
    item[field] = spoof
    counter: Counter[str] = Counter()
    assert samples.extract_replay_evidence({
        "snapshot_at": "2026-01-01T12:00:00Z",
        "coin": "BTC",
        "report": {"direction": "bullish", "calibrated_confidence": 0.9},
        "evidence": [item],
    }, counter, "BTC") == []
    assert counter["source_identity_conflict"] == 1
    assert counter["rejected_snapshots"] == 1


def test_unknown_source_is_not_self_registered() -> None:
    counter: Counter[str] = Counter()
    assert samples.extract_replay_evidence({
        "snapshot_at": "2026-01-01T12:00:00Z",
        "coin": "BTC",
        "report": {"direction": "bullish", "calibrated_confidence": 0.9},
        "evidence": [{
            "source": "attacker",
            "kind": "onchain",
            "provider": "Blockchain.com",
            "scope": "per-coin",
            "visible_at": "2026-01-01T08:00:00Z",
        }],
    }, counter, "BTC") == []
    assert counter["unknown_source"] == 1


@pytest.mark.parametrize("limit_kind", ["count", "single", "aggregate"])
def test_replay_batch_limits_abort_without_partial_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit_kind: str
) -> None:
    replay = tmp_path / "replay"
    replay.mkdir()
    (replay / "a.json").write_text("{}", encoding="utf-8")
    (replay / "b.json").write_text("{}", encoding="utf-8")
    if limit_kind == "count":
        monkeypatch.setattr(samples, "_MAX_REPLAY_FILES", 1)
    elif limit_kind == "single":
        monkeypatch.setattr(samples, "_MAX_INPUT_BYTES", 1)
    else:
        monkeypatch.setattr(samples, "_MAX_REPLAY_TOTAL_BYTES", 3)
    with pytest.raises(samples.ReplayInputError):
        list(samples._load_replay_snapshots(replay, Counter()))


def test_replay_lineage_excludes_index_and_is_shared_by_candidates(tmp_path: Path) -> None:
    fng, ohlcv, replay = _write_inputs(tmp_path)
    (replay / "index.json").write_text('{"mutable": true}', encoding="utf-8")
    result, _ = samples.build_samples(
        fng_path=fng, replay_dir=replay, ohlcv_path=ohlcv / "BTC_daily_ohlcv.csv",
        coin="BTC", horizon=1, cutoff=date(2026, 1, 1),
    )
    assert len({row["lineage_hash"] for row in result}) == 1
    before = result[0]["lineage_hash"]
    (replay / "index.json").write_text('{"mutable": false}', encoding="utf-8")
    result, _ = samples.build_samples(
        fng_path=fng, replay_dir=replay, ohlcv_path=ohlcv / "BTC_daily_ohlcv.csv",
        coin="BTC", horizon=1, cutoff=date(2026, 1, 1),
    )
    assert result[0]["lineage_hash"] == before


def test_fng_loader_fails_closed_when_file_exceeds_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fng = tmp_path / "fng.jsonl"
    fng.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(samples, "_MAX_INPUT_BYTES", 1)
    counter: Counter[str] = Counter()
    assert samples.load_fng_records(fng, counter) == []
    assert counter["input_too_large"] == 1


def test_output_is_deterministic_and_cutoff_is_inclusive(tmp_path: Path) -> None:
    fng, ohlcv, replay = _write_inputs(tmp_path)
    kwargs = dict(
        fng_path=fng, replay_dir=replay, ohlcv_path=ohlcv / "BTC_daily_ohlcv.csv",
        coin="BTC", horizon=1, cutoff=date(2026, 1, 1),
    )
    first, _ = samples.build_samples(**kwargs)
    second, _ = samples.build_samples(**kwargs)
    assert first == second
    assert [row["sample_id"] for row in first] == [row["sample_id"] for row in second]


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_atomic_output_failure_preserves_old_file_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    fng, ohlcv, replay = _write_inputs(tmp_path)
    output = tmp_path / "samples.jsonl"
    output.write_bytes(b"trusted-old-output\n")

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected atomic write failure")

    monkeypatch.setattr(samples.os, failure, fail)
    with pytest.raises(OSError, match="injected atomic write failure"):
        samples.main([
            "--fng-jsonl", str(fng),
            "--replay-dir", str(replay),
            "--ohlcv-dir", str(ohlcv),
            "--horizon", "1",
            "--cutoff", "2026-01-01",
            "--out", str(output),
        ])
    assert output.read_bytes() == b"trusted-old-output\n"
    assert list(tmp_path.glob(f".{output.name}.*.tmp")) == []


def test_market_wide_and_blockchain_sources_are_btc_only(tmp_path: Path) -> None:
    fng, ohlcv, replay = _write_inputs(tmp_path)
    (ohlcv / "ETH_daily_ohlcv.csv").write_text(
        "date,close\n2026-01-01,100\n2026-01-02,110\n", encoding="utf-8"
    )
    result, counters = samples.build_samples(
        fng_path=fng, replay_dir=replay, ohlcv_path=ohlcv / "ETH_daily_ohlcv.csv",
        coin="ETH", horizon=1, cutoff=date(2026, 1, 1),
    )
    assert all(row["source"] not in {"alternative-me-fng", "blockchain-com-charts"} for row in result)
    assert counters["fng_non_btc"] == 1
    assert counters["snapshot_coin_mismatch"] == 1


@pytest.mark.subprocess
def test_cli_writes_jsonl_and_reports_exclusions(tmp_path: Path) -> None:
    fng, ohlcv, replay = _write_inputs(tmp_path)
    (replay / "btc-2026-01-02.json").write_text(json.dumps({
        "snapshot_at": "2026-01-01T00:00:00Z",
        "coin": "BTC",
        "report": {"direction": "bullish", "calibrated_confidence": 0.8},
        "evidence": [{"source": "x", "kind": "sentiment",
                      "fetched_at": "2026-01-02T00:00:00Z"}],
    }), encoding="utf-8")
    output = tmp_path / "samples.jsonl"
    completed = subprocess.run([
        sys.executable, str(SCRIPT), "--fng-jsonl", str(fng),
        "--replay-dir", str(replay), "--ohlcv-dir", str(ohlcv),
        "--horizon", "1", "--cutoff", "2026-01-01", "--out", str(output),
    ], check=True, capture_output=True, text=True)
    summary = json.loads(completed.stdout)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert summary["future_evidence"] == 1
    assert summary["included"] == 3
    assert len(rows) == 3
    first_bytes = output.read_bytes()
    subprocess.run([
        sys.executable, str(SCRIPT), "--fng-jsonl", str(fng),
        "--replay-dir", str(replay), "--ohlcv-dir", str(ohlcv),
        "--horizon", "1", "--cutoff", "2026-01-01", "--out", str(output),
    ], check=True, capture_output=True, text=True)
    assert output.read_bytes() == first_bytes


@pytest.mark.subprocess
def test_cli_rejects_non_iso_cutoff(tmp_path: Path) -> None:
    fng, ohlcv, _ = _write_inputs(tmp_path)
    completed = subprocess.run([
        sys.executable, str(SCRIPT), "--fng-jsonl", str(fng),
        "--ohlcv-dir", str(ohlcv), "--cutoff", "01/01/2026",
    ], capture_output=True, text=True)
    assert completed.returncode == 2
    assert "cutoff must be UTC YYYY-MM-DD" in completed.stderr


@pytest.mark.subprocess
def test_cli_replay_limit_is_nonzero_and_does_not_replace_output(tmp_path: Path) -> None:
    fng, ohlcv, replay = _write_inputs(tmp_path)
    oversized = replay / "oversized.json"
    with oversized.open("wb") as stream:
        stream.seek(samples._MAX_INPUT_BYTES)
        stream.write(b"\0")
    output = tmp_path / "samples.jsonl"
    output.write_bytes(b"trusted-old-output\n")
    completed = subprocess.run([
        sys.executable, str(SCRIPT), "--fng-jsonl", str(fng),
        "--replay-dir", str(replay), "--ohlcv-dir", str(ohlcv),
        "--horizon", "1", "--cutoff", "2026-01-01", "--out", str(output),
    ], capture_output=True, text=True)
    assert completed.returncode != 0
    assert "replay file exceeds safety limit" in completed.stderr
    assert output.read_bytes() == b"trusted-old-output\n"
