"""Versioned asset context taxonomy and payload contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

ASSET_CONTEXT_SCHEMA_VERSION = "1.0.0"


class AssetSector(StrEnum):
    DEFI = "defi"
    L1 = "l1"
    L2 = "l2"
    STABLECOIN = "stablecoin"
    EXCHANGE = "exchange"
    INFRASTRUCTURE = "infrastructure"
    MEME = "meme"
    RWA = "rwa"
    GAMING = "gaming"
    AI = "ai"
    UNKNOWN = "unknown"


class AssetLayer(StrEnum):
    LAYER_1 = "layer_1"
    LAYER_2 = "layer_2"
    APP = "app"
    PROTOCOL = "protocol"
    TOKEN = "token"
    OFFCHAIN = "offchain"
    UNKNOWN = "unknown"


class TokenRole(StrEnum):
    GAS = "gas"
    GOVERNANCE = "governance"
    UTILITY = "utility"
    STAKING = "staking"
    STABLE = "stable"
    LP = "lp"
    WRAPPED = "wrapped"
    MEME = "meme"
    UNKNOWN = "unknown"


class MarketCapTier(StrEnum):
    LARGE = "large"
    MID = "mid"
    SMALL = "small"
    MICRO = "micro"
    UNKNOWN = "unknown"


ASSET_SECTORS = tuple(item.value for item in AssetSector)
ASSET_LAYERS = tuple(item.value for item in AssetLayer)
TOKEN_ROLES = tuple(item.value for item in TokenRole)
MARKET_CAP_TIERS = tuple(item.value for item in MarketCapTier)

EnumT = TypeVar("EnumT", bound=StrEnum)


@dataclass(frozen=True)
class AssetContext:
    """Controlled context used to compare an asset against the right peer set."""

    asset_id: str
    symbol: str
    name: str
    sector: AssetSector
    layer: AssetLayer
    token_role: TokenRole
    market_cap_tier: MarketCapTier = MarketCapTier.UNKNOWN
    ecosystem: str | None = None
    parent_asset_id: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = ASSET_CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ASSET_CONTEXT_SCHEMA_VERSION:
            raise ValueError(f"unsupported AssetContext schema_version: {self.schema_version}")
        for field_name in ("asset_id", "symbol", "name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"AssetContext.{field_name} must be a non-empty string")
        for field_name, enum_type in (
            ("sector", AssetSector),
            ("layer", AssetLayer),
            ("token_role", TokenRole),
            ("market_cap_tier", MarketCapTier),
        ):
            if not isinstance(getattr(self, field_name), enum_type):
                raise ValueError(f"AssetContext.{field_name} must be {enum_type.__name__}")
        for field_name in ("ecosystem", "parent_asset_id"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"AssetContext.{field_name} must be a string or null")
        if not isinstance(self.tags, tuple) or any(not isinstance(tag, str) for tag in self.tags):
            raise ValueError("AssetContext.tags must be a tuple of strings")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sector"] = self.sector.value
        payload["layer"] = self.layer.value
        payload["token_role"] = self.token_role.value
        payload["market_cap_tier"] = self.market_cap_tier.value
        payload["tags"] = list(self.tags)
        return payload


def _enum_value(enum_type: type[EnumT], raw: Any, field_name: str) -> EnumT:
    if raw is None or raw == "":
        raise ValueError(f"AssetContext.{field_name} is required; use 'unknown' explicitly when unknown")
    if not isinstance(raw, str):
        raise ValueError(f"AssetContext.{field_name} must be a string enum value")
    try:
        return enum_type(raw)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"invalid AssetContext.{field_name}: {raw!r}; allowed: {allowed}") from exc


def _optional_string(payload: dict[str, Any], field_name: str) -> str | None:
    value = payload[field_name]
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"AssetContext.{field_name} must be a string or null")


def asset_context_from_dict(payload: dict[str, Any]) -> AssetContext:
    """Parse an AssetContext payload without guessing missing taxonomy fields."""

    required_fields = (
        "schema_version",
        "asset_id",
        "symbol",
        "name",
        "sector",
        "layer",
        "token_role",
        "market_cap_tier",
        "ecosystem",
        "parent_asset_id",
        "tags",
    )
    missing = [name for name in required_fields if name not in payload]
    if missing:
        raise ValueError(f"missing AssetContext fields: {', '.join(missing)}")
    extra = sorted(set(payload) - set(required_fields))
    if extra:
        raise ValueError(f"unexpected AssetContext fields: {', '.join(extra)}")

    for field_name in ("asset_id", "symbol", "name"):
        value = payload[field_name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"AssetContext.{field_name} must be a non-empty string")

    tags = payload["tags"]
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("AssetContext.tags must be a list of strings")

    return AssetContext(
        schema_version=str(payload["schema_version"]),
        asset_id=payload["asset_id"],
        symbol=payload["symbol"],
        name=payload["name"],
        sector=_enum_value(AssetSector, payload["sector"], "sector"),
        layer=_enum_value(AssetLayer, payload["layer"], "layer"),
        token_role=_enum_value(TokenRole, payload["token_role"], "token_role"),
        market_cap_tier=_enum_value(MarketCapTier, payload["market_cap_tier"], "market_cap_tier"),
        ecosystem=_optional_string(payload, "ecosystem"),
        parent_asset_id=_optional_string(payload, "parent_asset_id"),
        tags=tuple(tags),
    )
