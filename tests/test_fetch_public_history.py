import importlib.util
import json
from pathlib import Path


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
    assert len(rows) == 5
    assert {row["coin"] for row in rows} == {"BTC", "ETH", "SOL", "BNB", "XRP"}
    assert all(row["published_at"].startswith("2021-06-01") for row in rows)
    assert all(row["retrieved_at"] != row["published_at"] for row in rows)
    assert all(row["provider"] == "Alternative.me" and row["license"] for row in rows)
