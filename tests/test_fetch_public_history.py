import importlib.util
import json
from pathlib import Path

from trustforge.schema import COIN_POOL


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_public_history.py"
SPEC = importlib.util.spec_from_file_location("fetch_public_history", SCRIPT)
assert SPEC and SPEC.loader
fetch_public_history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_public_history)


def test_fetch_alternative_me_writes_provenance_complete_jsonl(monkeypatch, tmp_path):
    payload = {"data": [
        {"timestamp": "1622505600", "value": "27", "value_classification": "Fear"},
        {"timestamp": "1622592000", "value": "30", "value_classification": "Fear"},
    ]}
    monkeypatch.setattr(fetch_public_history, "fetch_url", lambda *args, **kwargs: json.dumps(payload).encode())
    monkeypatch.setattr(fetch_public_history.time, "time", lambda: 1770000000.0)
    output = tmp_path / "history.jsonl"

    assert fetch_public_history.main([
        "--source", "alternative-me-fng", "--from-date", "2021-06-01",
        "--to-date", "2021-06-01", "--out", str(output),
    ]) == 0

    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == len(COIN_POOL)
    assert {row["coin"] for row in rows} == set(COIN_POOL)
    assert all(row["published_at"].startswith("2021-06-01") for row in rows)
    assert all(row["retrieved_at"] != row["published_at"] for row in rows)
    assert all(row["provider"] == "Alternative.me" and row["license"] for row in rows)


def test_fetch_sec_quarterly_indexes_with_explicit_user_agent(monkeypatch, tmp_path):
    master = b"header\nCIK|Company Name|Form Type|Date Filed|Filename\n-----\n100|Bitcoin Depot Inc|10-K|2021-06-01|edgar/data/100/abc.txt\n"
    calls = []

    def fetch(url, **kwargs):
        calls.append((url, kwargs["user_agent"]))
        return master

    monkeypatch.setattr(fetch_public_history, "fetch_url", fetch)
    output = tmp_path / "sec.jsonl"
    assert fetch_public_history.main([
        "--source", "sec-gov", "--from-date", "2021-06-01", "--to-date", "2021-06-30",
        "--user-agent", "TrustForge research ops@example.com", "--out", str(output),
    ]) == 0
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["coin"] == "BTC"
    assert rows[0]["match_scope"] == "metadata_only"
    assert calls == [("https://www.sec.gov/Archives/edgar/full-index/2021/QTR2/master.idx", "TrustForge research ops@example.com")]


def test_fetch_blockchain_charts_uses_fixed_metric_allowlist(monkeypatch, tmp_path):
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return json.dumps({"values": [{"x": 1622505600, "y": 42}]}).encode()

    monkeypatch.setattr(fetch_public_history, "fetch_url", fetch)
    monkeypatch.setattr(fetch_public_history.time, "time", lambda: 1770000000.0)
    output = tmp_path / "blockchain.jsonl"

    assert fetch_public_history.main([
        "--source", "blockchain-com-charts", "--from-date", "2021-06-01",
        "--to-date", "2021-06-01", "--out", str(output),
    ]) == 0

    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 3 and {row["metric"] for row in rows} == {
        "n-transactions", "hash-rate", "difficulty",
    }
    assert all("sampled=false" in url and "timespan=1days" in url for url in calls)
