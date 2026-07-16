from datetime import datetime, timezone

from trustforge.historical_sources import historical_source_capabilities, parse_alternative_me_history


def test_historical_capability_matrix_never_calls_recent_rss_an_archive():
    matrix = {item["source"]: item for item in historical_source_capabilities()}
    assert matrix["sec-gov"]["strategy"] == "dated_api_or_official_bulk"
    assert matrix["news-rss-group"]["status"] == "archive_required"
    assert matrix["hoyabit-ticker"]["status"] == "blocked"


def test_alternative_me_history_is_filtered_and_expanded_as_market_wide():
    day = datetime(2022, 1, 2, tzinfo=timezone.utc).timestamp()
    payload = {"data": [
        {"timestamp": str(day), "value": "20", "value_classification": "Fear"},
        {"timestamp": "bad", "value": "50"},
    ]}
    rows = parse_alternative_me_history(payload, retrieved_at=day + 100, start_epoch=day, end_epoch=day + 1)
    assert len(rows) == 5
    assert {row["coin"] for row in rows} == {"BTC", "ETH", "SOL", "BNB", "XRP"}
    assert all(row["scope"] == "market-wide" for row in rows)
    assert all(row["published_at"] == "2022-01-02T00:00:00Z" for row in rows)
