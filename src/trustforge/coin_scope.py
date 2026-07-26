"""Pure application coin-scope rules shared by ingestion and adapters."""

from __future__ import annotations

import re


_COIN_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("BTC", ("btc", "bitcoin", "比特幣", "比特")),
    ("ETH", ("eth", "ethereum", "以太坊", "以太")),
    ("SOL", ("sol", "solana")),
    ("BNB", ("bnb", "binance")),
    ("XRP", ("xrp", "ripple", "瑞波")),
    ("ARB", ("arb", "arbitrum")),
)


def _alias_in(alias: str, text: str) -> bool:
    if alias.isascii():
        return (
            re.search(
                r"\b" + re.escape(alias) + r"\b",
                text,
                re.IGNORECASE | re.ASCII,
            )
            is not None
        )
    return alias in text


def coins_mentioned(text: str) -> set[str]:
    """Return canonical symbols explicitly mentioned in text."""
    return {
        code
        for code, aliases in _COIN_ALIASES
        if any(_alias_in(alias, text) for alias in aliases)
    }


def matches_coin_fields(
    *, document_id: str, text: str, explicit_coin: object, target_coin: str
) -> bool:
    """Apply the canonical app coin-scope policy to primitive document fields."""
    targets = {
        target.strip().upper()
        for target in re.split(r"[,\s]+", target_coin)
        if target.strip()
    }
    if not targets:
        return True
    if explicit_coin:
        return str(explicit_coin).upper() in targets
    mentioned = coins_mentioned(f"{document_id} {text}")
    return not mentioned or bool(mentioned & targets) and not (mentioned - targets)
