import json
from urllib.parse import parse_qs, urlparse

import pytest

from trustforge.ingestion.whale_trades import (
    ArkhamIntelSource,
    _extract_entity_name,
    _has_arkham_attribution,
    _parse_iso_timestamp,
)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("2026-07-29T01:02:03Z", True),
        ("2026-07-29T01:02:03+00:00", True),
        ("2026-07-29T09:02:03+08:00", True),
        ("2026-07-29T01:02:03", False),
        ("invalid", False),
        (None, False),
    ],
)
def test_parse_iso_timestamp(value, valid):
    assert (_parse_iso_timestamp(value) is not None) is valid


def test_extract_entity_name_priority_and_address_fallback():
    assert _extract_entity_name(
        {
            "arkhamEntity": {"name": "Entity Name"},
            "arkhamLabel": {"name": "Label Name"},
            "address": "0x123456789abcdef",
        }
    ) == "Entity Name"
    assert _extract_entity_name(
        {"arkhamLabel": {"name": "Label Name"}, "address": "0x123456789abcdef"}
    ) == "Label Name"
    assert _extract_entity_name({"address": "0x123456789abcdef"}) == "0x12345678"
    assert _extract_entity_name(None) == "unknown"


def test_has_arkham_attribution_requires_non_empty_entity_or_label():
    assert _has_arkham_attribution({"entity": {"name": "Known"}}) is True
    assert _has_arkham_attribution({"arkhamLabel": {"name": "Known"}}) is True
    assert _has_arkham_attribution({"address": "0x123"}) is False
    assert _has_arkham_attribution({"entity": {}}) is False


def test_arkham_fetch_uses_v1_endpoint_header_and_schema(monkeypatch):
    monkeypatch.setenv("ARKHAM_API_KEY", "test-key")
    captured = {}
    payload = {
        "transfers": [
            {
                "tokenSymbol": "BTC",
                "historicalUSD": 1_500_000,
                "blockTimestamp": "2026-07-29T01:02:03Z",
                "transactionHash": "0xabc",
                "fromAddress": {"address": "bc1from"},
                "toAddress": {
                    "address": "bc1to",
                    "arkhamEntity": {"name": "Known Fund"},
                },
            }
        ]
    }

    def fake_fetch(url, extra_headers=None):
        captured["url"] = url
        captured["headers"] = extra_headers
        return json.dumps(payload).encode()

    monkeypatch.setattr("trustforge.ingestion.whale_trades._fetch_url", fake_fetch)
    docs = ArkhamIntelSource().fetch("", coin="BTC")

    parsed = urlparse(captured["url"])
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.arkm.com"
    assert parsed.path == "/transfers"
    assert params == {
        "chains": ["bitcoin"],
        "limit": ["20"],
        "timeLast": ["1h"],
        "usdGte": ["1000000"],
    }
    assert captured["headers"] == {"API-Key": "test-key"}
    assert "test-key" not in captured["url"]
    assert len(docs) == 1
    assert docs[0].meta["coin"] == "BTC"
    assert docs[0].meta["amount_usd"] == 1_500_000
    assert docs[0].meta["entity"] == "Known Fund"
    assert docs[0].meta["action"] == "buy"
    assert docs[0].meta["verified_onchain"] is True


def test_arkham_fetch_without_key_is_offline(monkeypatch):
    monkeypatch.delenv("ARKHAM_API_KEY", raising=False)
    assert ArkhamIntelSource().fetch("", coin="BTC") == []
