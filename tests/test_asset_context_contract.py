from __future__ import annotations

import pytest

from trustforge.asset_context import (
    ASSET_CONTEXT_SCHEMA_VERSION,
    AssetContext,
    AssetLayer,
    AssetSector,
    MarketCapTier,
    TokenRole,
    asset_context_from_dict,
)
from trustforge.data_contracts import contract_schemas


def test_asset_context_round_trips_with_controlled_taxonomy() -> None:
    context = AssetContext(
        asset_id="asset:arb",
        symbol="ARB",
        name="Arbitrum",
        sector=AssetSector.L2,
        layer=AssetLayer.LAYER_2,
        token_role=TokenRole.GOVERNANCE,
        market_cap_tier=MarketCapTier.LARGE,
        ecosystem="ethereum",
        tags=("rollup", "optimistic"),
        settlement_chain="ethereum",
        gas_token="ETH",
        dependencies=("ethereum",),
    )

    payload = context.to_dict()
    assert payload == {
        "schema_version": ASSET_CONTEXT_SCHEMA_VERSION,
        "asset_id": "asset:arb",
        "symbol": "ARB",
        "name": "Arbitrum",
        "sector": "l2",
        "layer": "layer_2",
        "token_role": "governance",
        "market_cap_tier": "large",
        "ecosystem": "ethereum",
        "parent_asset_id": None,
        "tags": ["rollup", "optimistic"],
        "settlement_chain": "ethereum",
        "gas_token": "ETH",
        "dependencies": ["ethereum"],
    }
    assert asset_context_from_dict(payload).to_dict() == payload


def test_asset_context_from_dict_is_backward_compatible_without_new_fields() -> None:
    legacy_payload = {
        "schema_version": ASSET_CONTEXT_SCHEMA_VERSION,
        "asset_id": "asset:arb",
        "symbol": "ARB",
        "name": "Arbitrum",
        "sector": "l2",
        "layer": "layer_2",
        "token_role": "governance",
        "market_cap_tier": "large",
        "ecosystem": "ethereum",
        "parent_asset_id": None,
        "tags": ["rollup", "optimistic"],
    }

    context = asset_context_from_dict(legacy_payload)

    assert context.settlement_chain == "unknown"
    assert context.gas_token == "unknown"
    assert context.dependencies == ()


def test_asset_context_new_fields_round_trip_and_validate() -> None:
    payload = {
        "schema_version": ASSET_CONTEXT_SCHEMA_VERSION,
        "asset_id": "asset:btc",
        "symbol": "BTC",
        "name": "Bitcoin",
        "sector": "l1",
        "layer": "layer_1",
        "token_role": "gas",
        "market_cap_tier": "large",
        "ecosystem": None,
        "parent_asset_id": None,
        "tags": [],
        "settlement_chain": "bitcoin",
        "gas_token": "BTC",
        "dependencies": [],
    }

    context = asset_context_from_dict(payload)
    assert context.to_dict() == payload

    with pytest.raises(ValueError, match="AssetContext.settlement_chain must be a non-empty string"):
        asset_context_from_dict({**payload, "settlement_chain": ""})

    with pytest.raises(ValueError, match="AssetContext.dependencies must be a list of strings"):
        asset_context_from_dict({**payload, "dependencies": [1]})


def test_asset_context_schema_exposes_required_version_and_enums() -> None:
    schema = contract_schemas()["AssetContext"]

    assert schema["properties"]["schema_version"]["const"] == ASSET_CONTEXT_SCHEMA_VERSION
    assert "schema_version" in schema["required"]
    assert schema["properties"]["asset_id"]["pattern"] == r"\S"
    assert schema["properties"]["symbol"]["pattern"] == r"\S"
    assert schema["properties"]["name"]["pattern"] == r"\S"
    assert schema["properties"]["sector"]["enum"] == [
        "defi",
        "l1",
        "l2",
        "stablecoin",
        "exchange",
        "infrastructure",
        "meme",
        "rwa",
        "gaming",
        "ai",
        "unknown",
    ]
    assert schema["properties"]["layer"]["enum"] == [
        "layer_1",
        "layer_2",
        "app",
        "protocol",
        "token",
        "offchain",
        "unknown",
    ]
    assert "unknown" in schema["properties"]["token_role"]["enum"]
    assert "unknown" in schema["properties"]["market_cap_tier"]["enum"]


def test_asset_context_rejects_invalid_enum_values() -> None:
    payload = {
        "schema_version": ASSET_CONTEXT_SCHEMA_VERSION,
        "asset_id": "asset:arb",
        "symbol": "ARB",
        "name": "Arbitrum",
        "sector": "rollup-ish",
        "layer": "layer_2",
        "token_role": "governance",
        "market_cap_tier": "large",
        "ecosystem": "ethereum",
        "parent_asset_id": None,
        "tags": [],
    }

    with pytest.raises(ValueError, match="invalid AssetContext.sector"):
        asset_context_from_dict(payload)


def test_asset_context_rejects_missing_required_values_without_guessing() -> None:
    payload = {
        "schema_version": ASSET_CONTEXT_SCHEMA_VERSION,
        "asset_id": "asset:arb",
        "symbol": "ARB",
        "name": "Arbitrum",
        "sector": "",
        "layer": "layer_2",
        "token_role": "governance",
        "market_cap_tier": "large",
        "ecosystem": "ethereum",
        "parent_asset_id": None,
        "tags": [],
    }

    with pytest.raises(ValueError, match="use 'unknown' explicitly"):
        asset_context_from_dict(payload)

    with pytest.raises(ValueError, match="missing AssetContext fields: token_role"):
        asset_context_from_dict({key: value for key, value in payload.items() if key != "token_role"})

    with pytest.raises(ValueError, match="AssetContext.tags must be a list"):
        asset_context_from_dict({**payload, "sector": "unknown", "tags": None})

    with pytest.raises(ValueError, match="unexpected AssetContext fields: surprise"):
        asset_context_from_dict({**payload, "sector": "unknown", "surprise": "field"})

    with pytest.raises(ValueError, match="AssetContext.asset_id must be a non-empty string"):
        asset_context_from_dict({**payload, "sector": "unknown", "asset_id": 123})

    with pytest.raises(ValueError, match="AssetContext.name must be a non-empty string"):
        asset_context_from_dict({**payload, "sector": "unknown", "name": None})

    with pytest.raises(ValueError, match="AssetContext.tags must be a list of strings"):
        asset_context_from_dict({**payload, "sector": "unknown", "tags": [1]})


def test_asset_context_constructor_validates_controlled_enum_fields() -> None:
    with pytest.raises(ValueError, match="AssetContext.sector must be AssetSector"):
        AssetContext(
            asset_id="asset:arb",
            symbol="ARB",
            name="Arbitrum",
            sector="unknown",  # type: ignore[arg-type]
            layer=AssetLayer.LAYER_2,
            token_role=TokenRole.GOVERNANCE,
            market_cap_tier=MarketCapTier.LARGE,
        )


def test_asset_context_allows_explicit_unknown_taxonomy() -> None:
    context = asset_context_from_dict(
        {
            "schema_version": ASSET_CONTEXT_SCHEMA_VERSION,
            "asset_id": "asset:new",
            "symbol": "NEW",
            "name": "New Asset",
            "sector": "unknown",
            "layer": "unknown",
            "token_role": "unknown",
            "market_cap_tier": "unknown",
            "ecosystem": None,
            "parent_asset_id": None,
            "tags": [],
        }
    )

    assert context.sector is AssetSector.UNKNOWN
    assert context.layer is AssetLayer.UNKNOWN
    assert context.token_role is TokenRole.UNKNOWN
    assert context.market_cap_tier is MarketCapTier.UNKNOWN
