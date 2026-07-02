"""成本會計階段2：`cost_model.py` 常數與純函式測試。"""
from __future__ import annotations

import pytest

from trustforge.cost_model import (
    CONNECTOR_COST_MODEL,
    estimate_connector_cost,
    is_paid_tier_enabled,
    quota_percent,
)

ALL_KNOWN_SOURCES = [
    "coingecko-price", "coingecko-sentiment", "coingecko-dev",
    "coindesk", "decrypt", "cryptopanic",
    "alternative-me-fng", "blockchain-info", "sec-gov",
    "reddit-cryptocurrency", "reddit-bitcoin",
]


def test_connector_cost_model_registers_all_known_ingestion_sources():
    """`src/trustforge/ingestion/*.py` 目前的全部 source 名稱都要在成本模型裡
    登記到，否則 `/status`「連接器用量」表會漏掉真實用量。"""
    for source in ALL_KNOWN_SOURCES:
        assert source in CONNECTOR_COST_MODEL, f"{source} 未登記於 CONNECTOR_COST_MODEL"


def test_coingecko_sources_share_official_10000_per_month_quota():
    for source in ("coingecko-price", "coingecko-sentiment", "coingecko-dev"):
        model = CONNECTOR_COST_MODEL[source]
        assert model.free_tier_quota == 10_000
        assert model.free_tier_period == "month"
        assert "coingecko.com/en/api/pricing" in model.free_tier_reference


@pytest.mark.parametrize(
    "source",
    [s for s in ALL_KNOWN_SOURCES if not s.startswith("coingecko-")],
)
def test_non_coingecko_sources_honestly_labeled_no_official_quota(source):
    """非 CoinGecko 的公開端點誠實標「無官方公開量化硬配額」，不臆測數字。"""
    model = CONNECTOR_COST_MODEL[source]
    assert model.free_tier_quota is None
    assert model.free_tier_period == "n/a"


def test_is_paid_tier_enabled_defaults_false(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_PAID_TIER_SOURCES", raising=False)
    for source in ALL_KNOWN_SOURCES:
        assert is_paid_tier_enabled(source) is False


def test_is_paid_tier_enabled_respects_env_allowlist(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_PAID_TIER_SOURCES", "coingecko-price, sec-gov")
    assert is_paid_tier_enabled("coingecko-price") is True
    assert is_paid_tier_enabled("sec-gov") is True
    assert is_paid_tier_enabled("coingecko-sentiment") is False


def test_estimate_connector_cost_free_tier_always_zero(monkeypatch):
    """⛔ 誠實原則：free tier（預設，未啟用付費層）不管呼叫幾次都必須是 $0，
    即使該 source 附了假設性付費單價（如 coingecko-price）。"""
    monkeypatch.delenv("TRUSTFORGE_PAID_TIER_SOURCES", raising=False)
    for source in ALL_KNOWN_SOURCES:
        assert estimate_connector_cost(source, 0) == 0.0
        assert estimate_connector_cost(source, 999_999) == 0.0


def test_estimate_connector_cost_zero_or_negative_call_count_is_zero(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_PAID_TIER_SOURCES", "coingecko-price")
    assert estimate_connector_cost("coingecko-price", 0) == 0.0
    assert estimate_connector_cost("coingecko-price", -5) == 0.0


def test_estimate_connector_cost_paid_tier_uses_assumed_unit_price(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_PAID_TIER_SOURCES", "coingecko-price")
    cost = estimate_connector_cost("coingecko-price", 500_000)
    # 129.0 / 500_000 * 500_000 ≈ 129.0（假設單價換算，見 cost_model.py 註解）
    assert cost == pytest.approx(129.0, rel=1e-3)


def test_estimate_connector_cost_paid_tier_source_with_no_assumed_price_is_zero(monkeypatch):
    """啟用付費層但該 source 沒有登記假設單價（`paid_unit_cost_usd=None`）時
    仍必須回 0，不得誤算。"""
    monkeypatch.setenv("TRUSTFORGE_PAID_TIER_SOURCES", "coindesk")
    assert estimate_connector_cost("coindesk", 1000) == 0.0


def test_estimate_connector_cost_unknown_source_is_zero(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_PAID_TIER_SOURCES", "not-a-real-source")
    assert estimate_connector_cost("not-a-real-source", 1000) == 0.0


def test_quota_percent_none_when_no_official_quota():
    for source in ALL_KNOWN_SOURCES:
        if source.startswith("coingecko-"):
            continue
        assert quota_percent(source, 100) is None


def test_quota_percent_none_for_unknown_source():
    assert quota_percent("not-a-real-source", 100) is None


def test_quota_percent_computed_for_coingecko():
    assert quota_percent("coingecko-price", 100) == pytest.approx(1.0)
    assert quota_percent("coingecko-price", 10_000) == pytest.approx(100.0)
    assert quota_percent("coingecko-price", 20_000) == pytest.approx(200.0)  # 允許顯示超額
