import json
from urllib.parse import parse_qs, urlparse

import pytest

from trustforge.ingestion.whale_trades import (
    ArkhamIntelSource,
    _arkham_transfer_limit,
    _extract_entity_name,
    _has_arkham_attribution,
    _parse_iso_timestamp,
    _reset_arkham_throttle_for_tests,
)


@pytest.fixture(autouse=True)
def reset_arkham_throttle():
    _reset_arkham_throttle_for_tests()
    yield
    _reset_arkham_throttle_for_tests()


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
                "chain": "bitcoin",
                "historicalUSD": 1_500_000,
                "blockTimestamp": "2026-07-29T01:02:03Z",
                "transactionHash": "0xabc0000000000000",
                "id": "0xabc0000000000000_1",
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
    assert docs[0].meta["chain"] == "bitcoin"
    assert docs[0].meta["asset_symbol"] == "BTC"
    assert docs[0].meta["amount_usd"] == 1_500_000
    assert docs[0].meta["entity"] == "Known Fund"
    assert docs[0].meta["action"] == "transfer"
    assert docs[0].meta["direction"] == "neutral"
    assert docs[0].meta["verified_onchain"] is True


def test_arkham_fetch_without_key_is_offline(monkeypatch):
    monkeypatch.delenv("ARKHAM_API_KEY", raising=False)
    assert ArkhamIntelSource().fetch("", coin="BTC") == []


def test_arkham_parses_live_shaped_bitcoin_utxo_transfer():
    transfer = {
        "chain": "bitcoin",
        "historicalUSD": 1_750_000,
        "blockTimestamp": "2026-08-01T01:02:03Z",
        "transactionHash": "redacted-btc-transaction",
        "seq": 101,
        "fromAddresses": [
            {
                "address": "redacted-btc-source",
                "arkhamEntity": {"name": "Known Treasury"},
            }
        ],
        "toAddress": {"address": "redacted-btc-destination"},
        "fromValue": 12.5,
        "unitValue": 140_000,
    }

    doc = ArkhamIntelSource()._parse_transfer(transfer, "BTC")

    assert doc is not None
    assert doc.meta["coin"] == "BTC"
    assert doc.meta["chain"] == "bitcoin"
    assert doc.meta["asset_symbol"] == "BTC"
    assert doc.meta["action"] == "transfer"
    assert doc.meta["entity"] == "Known Treasury"


def test_arkham_preserves_actual_asset_on_target_chain():
    transfer = {
        "chain": "ethereum",
        "tokenSymbol": "WETH",
        "historicalUSD": 2_000_000,
        "blockTimestamp": "2026-08-01T01:02:03Z",
        "transactionHash": "redacted-eth-transaction",
        "id": "redacted-eth-transfer-1",
        "fromAddress": {"address": "redacted-eth-source"},
        "toAddress": {
            "address": "redacted-eth-destination",
            "arkhamEntity": {"name": "Known Wallet"},
        },
    }

    doc = ArkhamIntelSource()._parse_transfer(transfer, "ETH")

    assert doc is not None
    assert doc.meta["coin"] == "ETH"
    assert doc.meta["asset_symbol"] == "WETH"
    assert "WETH" in doc.text
    assert doc.meta["action"] == "transfer"
    assert doc.meta["direction"] == "neutral"
    assert "不代表買賣" in doc.text


def test_arkham_rejects_unattributed_or_wrong_chain_transfer():
    transfer = {
        "chain": "ethereum",
        "tokenSymbol": "USDC",
        "historicalUSD": 2_000_000,
        "blockTimestamp": "2026-08-01T01:02:03Z",
        "transactionHash": "redacted-transaction",
        "id": "redacted-transfer-1",
        "fromAddress": {"address": "redacted-source"},
        "toAddress": {"address": "redacted-destination"},
    }

    assert ArkhamIntelSource()._parse_transfer(transfer, "ETH") is None
    transfer["toAddress"]["arkhamEntity"] = {"name": "Known Wallet"}
    assert ArkhamIntelSource()._parse_transfer(transfer, "BTC") is None


def test_arkham_label_alone_is_not_entity_attribution():
    transfer = {
        "chain": "ethereum",
        "tokenSymbol": "USDC",
        "historicalUSD": 2_000_000,
        "blockTimestamp": "2026-08-01T01:02:03Z",
        "transactionHash": "0xabc0000000000000",
        "id": "0xabc0000000000000_1",
        "fromAddress": {"address": "redacted-source"},
        "toAddress": {
            "address": "redacted-destination",
            "arkhamLabel": {"name": "Deposit Wallet"},
        },
    }

    assert ArkhamIntelSource()._parse_transfer(transfer, "ETH") is None


def test_arkham_rejects_missing_timestamp_or_transaction_identity():
    transfer = {
        "chain": "bitcoin",
        "historicalUSD": 2_000_000,
        "fromAddresses": [
            {"address": "redacted-source", "arkhamEntity": {"name": "Known"}}
        ],
        "toAddress": {"address": "redacted-destination"},
    }

    assert ArkhamIntelSource()._parse_transfer(transfer, "BTC") is None
    transfer["blockTimestamp"] = "2026-08-01T01:02:03Z"
    assert ArkhamIntelSource()._parse_transfer(transfer, "BTC") is None


@pytest.mark.parametrize(
    ("configured", "expected"),
    [(None, 20), ("1", 1), ("0", 1), ("999", 20), ("invalid", 20)],
)
def test_arkham_transfer_limit_is_bounded(monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv("TRUSTFORGE_ARKHAM_TRANSFER_LIMIT", raising=False)
    else:
        monkeypatch.setenv("TRUSTFORGE_ARKHAM_TRANSFER_LIMIT", configured)
    assert _arkham_transfer_limit() == expected


def test_arkham_fetch_reports_safe_aggregate_parse_counts(monkeypatch, caplog):
    monkeypatch.setenv("ARKHAM_API_KEY", "test-key")
    payload = {
        "transfers": [
            {"chain": "bitcoin", "historicalUSD": 2_000_000},
            "not-an-object",
        ]
    }
    monkeypatch.setattr(
        "trustforge.ingestion.whale_trades._fetch_url",
        lambda *_args, **_kwargs: json.dumps(payload).encode(),
    )

    with caplog.at_level("INFO"):
        assert ArkhamIntelSource().fetch("", coin="BTC") == []

    assert "returned=2 accepted=0 rejected=2" in caplog.text
    assert "test-key" not in caplog.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tokenSymbol", "ETH\nforged"),
        ("transactionHash", "short"),
        ("transactionHash", "x" * 129),
    ],
)
def test_arkham_rejects_unsafe_provider_text(field, value):
    transfer = {
        "chain": "ethereum",
        "tokenSymbol": "ETH",
        "historicalUSD": 2_000_000,
        "blockTimestamp": "2026-08-01T01:02:03Z",
        "transactionHash": "0xabc0000000000000",
        "id": "0xabc0000000000000_1",
        "fromAddress": {
            "address": "redacted-source",
            "arkhamEntity": {"name": "Known Entity"},
        },
        "toAddress": {"address": "redacted-destination"},
    }
    transfer[field] = value

    assert ArkhamIntelSource()._parse_transfer(transfer, "ETH") is None


@pytest.mark.parametrize(
    "entity",
    [
        {"name": "Known\nForged"},
        "Known\nForged",
        "x" * 121,
    ],
)
def test_arkham_rejects_unsafe_entity_name(entity):
    transfer = {
        "chain": "bitcoin",
        "historicalUSD": 2_000_000,
        "blockTimestamp": "2026-08-01T01:02:03Z",
        "transactionHash": "redacted-btc-transaction",
        "seq": 102,
        "fromAddresses": [
            {
                "address": "redacted-source",
                "arkhamEntity": entity,
            }
        ],
        "toAddress": {"address": "redacted-destination"},
    }

    assert ArkhamIntelSource()._parse_transfer(transfer, "BTC") is None


def test_arkham_throttle_enforces_provider_interval(monkeypatch):
    clock = iter([10.0, 10.2, 11.0])
    sleeps = []
    monkeypatch.setattr("trustforge.ingestion.whale_trades.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("trustforge.ingestion.whale_trades.time.sleep", sleeps.append)

    from trustforge.ingestion.whale_trades import _throttle_arkham_request

    _throttle_arkham_request()
    _throttle_arkham_request()

    assert sleeps == [pytest.approx(0.8)]


@pytest.mark.parametrize("chain", ["ethereum", "bsc", "arbitrum", "solana", "xrp"])
def test_arkham_non_bitcoin_missing_symbol_fails_closed(chain):
    target = {
        "ethereum": "ETH",
        "bsc": "BNB",
        "arbitrum": "ARB",
        "solana": "SOL",
        "xrp": "XRP",
    }[chain]
    transfer = {
        "chain": chain,
        "historicalUSD": 2_000_000,
        "blockTimestamp": "2026-08-01T01:02:03Z",
        "transactionHash": "redacted-chain-transaction",
        "id": "redacted-chain-transfer-1",
        "fromAddress": {
            "address": "redacted-source",
            "arkhamEntity": {"name": "Known Entity"},
        },
        "toAddress": {"address": "redacted-destination"},
    }

    assert ArkhamIntelSource()._parse_transfer(transfer, target) is None


def test_arkham_same_transaction_transfers_have_distinct_document_ids():
    transfer = {
        "chain": "ethereum",
        "tokenSymbol": "USDC",
        "historicalUSD": 2_000_000,
        "blockTimestamp": "2026-08-01T01:02:03Z",
        "transactionHash": "redacted-shared-transaction",
        "id": "redacted-transfer-event-1",
        "fromAddress": {
            "address": "redacted-source",
            "arkhamEntity": {"name": "Known Entity"},
        },
        "toAddress": {"address": "redacted-destination"},
    }
    second = {**transfer, "id": "redacted-transfer-event-2"}

    first_doc = ArkhamIntelSource()._parse_transfer(transfer, "ETH")
    second_doc = ArkhamIntelSource()._parse_transfer(second, "ETH")

    assert first_doc is not None
    assert second_doc is not None
    assert first_doc.id != second_doc.id


def test_arkham_missing_provider_transfer_identity_fails_closed():
    transfer = {
        "chain": "bitcoin",
        "historicalUSD": 2_000_000,
        "blockTimestamp": "2026-08-01T01:02:03Z",
        "transactionHash": "redacted-btc-transaction",
        "fromAddresses": [
            {
                "address": "redacted-source",
                "arkhamEntity": {"name": "Known Entity"},
            }
        ],
        "toAddress": {"address": "redacted-destination"},
    }

    assert ArkhamIntelSource()._parse_transfer(transfer, "BTC") is None
