from datetime import datetime, timezone

from trustforge.historical_sources import historical_source_capabilities, parse_alternative_me_history, parse_sec_master_index


def test_historical_capability_matrix_never_calls_recent_rss_an_archive():
    matrix = {item["source"]: item for item in historical_source_capabilities()}
    assert matrix["sec-gov"]["strategy"] == "official_quarterly_master_index"
    assert matrix["sec-gov"]["status"] == "ready_partial"
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


def test_sec_master_index_parser_is_metadata_only_and_time_bounded():
    text = "header\nCIK|Company Name|Form Type|Date Filed|Filename\n----------------\n" \
           "100|Bitcoin Depot Inc|10-K|2021-06-01|edgar/data/100/abc.txt\n" \
           "101|Ordinary Corp|8-K|2021-06-01|edgar/data/101/def.txt\n" \
           "102|Blockchain Holdings|10-Q|2021-06-02|edgar/data/102/ghi.txt\n" \
           "103|Ethereum Fund|N-1A|bad-date|edgar/data/103/jkl.txt\n"
    rows = parse_sec_master_index(text, retrieved_at=1770000000.0, start_epoch=1622505600.0, end_epoch=1622592000.0)
    assert len(rows) == 6
    bitcoin = next(row for row in rows if row["company"] == "Bitcoin Depot Inc")
    assert bitcoin["coin"] == "BTC" and bitcoin["match_scope"] == "metadata_only"
    assert bitcoin["url"] == "https://www.sec.gov/Archives/edgar/data/100/abc.txt"
    assert {row["coin"] for row in rows if row["company"] == "Blockchain Holdings"} == {"BTC", "ETH", "SOL", "BNB", "XRP"}
