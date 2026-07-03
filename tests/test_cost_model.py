"""成本會計階段2：`cost_model.py` 常數與純函式測試。"""
from __future__ import annotations

import pytest

from trustforge.cost_model import (
    CONNECTOR_COST_MODEL,
    SHARED_POOL_LABEL,
    estimate_connector_cost,
    is_paid_tier_enabled,
)

ALL_KNOWN_SOURCES = [
    "coingecko-price", "coingecko-sentiment", "coingecko-dev",
    "coindesk", "decrypt", "cryptopanic",
    "cointelegraph", "bitcoinmagazine", "cryptoslate",
    "bitcoinist", "newsbtc", "dailyhodl",
    "alternative-me-fng", "blockchain-info", "sec-gov",
    "reddit-cryptocurrency", "reddit-bitcoin",
]


def test_connector_cost_model_registers_all_known_ingestion_sources():
    """`src/trustforge/ingestion/*.py` 目前的全部 source 名稱都要在成本模型裡
    登記到，否則 `/status`「連接器用量」表會漏掉真實用量。"""
    for source in ALL_KNOWN_SOURCES:
        assert source in CONNECTOR_COST_MODEL, f"{source} 未登記於 CONNECTOR_COST_MODEL"


def test_connector_cost_model_covers_every_build_news_source(monkeypatch):
    """防呆：`build_news_sources()` 實際會註冊的每個連接器 name，都必須在
    `CONNECTOR_COST_MODEL` 登記到——直接從 `build_news_sources()` **推導**
    預期集合（而非重複手寫一份可能漏改的清單），未來任何人加新聞源忘了
    登記成本模型，這裡就會紅（不能被靜默略過，見 codex 對抗審 PR #54）。
    連 `CRYPTOPANIC_TOKEN` 開起來的條件式 `cryptopanic` 也要涵蓋到。"""
    monkeypatch.setenv("CRYPTOPANIC_TOKEN", "test-token-for-cost-model-coverage")
    from trustforge.ingestion.news import build_news_sources

    expected_names = {s.name for s in build_news_sources()}
    assert expected_names, "build_news_sources() 不應回傳空集合"
    for name in expected_names:
        assert name in CONNECTOR_COST_MODEL, f"{name} 由 build_news_sources() 產生，但未登記於 CONNECTOR_COST_MODEL"


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


# ---------------------------------------------------------------------------
# codex HIGH（#24、PR #41）：不再提供 quota_percent()——rolling window 呼叫數
# 除以官方月配額算百分比語意錯誤，且逐 source 分別算會低估共用 key 的真實
# 使用率。改用 `shared_pool` 讓呼叫端把共用同一組配額 key 的 source 合併
# 顯示（見 `web.py::_render_connector_usage_table`）。
# ---------------------------------------------------------------------------

def test_quota_percent_no_longer_exported():
    """回歸鎖：確保沒有人不小心把這個已知會誤導的函式加回去。"""
    import trustforge.cost_model as cm

    assert not hasattr(cm, "quota_percent")


def test_coingecko_sources_share_the_same_pool_key():
    """3 個 coingecko-* source 必須標記同一個 `shared_pool`，`web.py` 才能
    正確把它們合併成一行加總，而不是逐 source 誤顯示成各自獨立配額。"""
    pool_keys = {
        CONNECTOR_COST_MODEL[s].shared_pool
        for s in ("coingecko-price", "coingecko-sentiment", "coingecko-dev")
    }
    assert len(pool_keys) == 1
    pool_key = pool_keys.pop()
    assert pool_key is not None
    assert pool_key in SHARED_POOL_LABEL


def test_non_coingecko_sources_have_no_shared_pool():
    for source in ALL_KNOWN_SOURCES:
        if source.startswith("coingecko-"):
            continue
        assert CONNECTOR_COST_MODEL[source].shared_pool is None
