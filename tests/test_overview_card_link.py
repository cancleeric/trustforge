"""P-2026 生產 UX bug（第二處）回歸測試：首頁多幣總覽 5 張卡
（`tf-overview-card`）原本是純 `<div>`，真點下去零反應。

修法：`scripts/fetch_scheduler.py::_render_overview_html()` 這端（寫入者，
組 blob 當下）把每張卡包成 `<a href="/analyze?coin=...">`（真資料，不帶
`sample=1`），讓卡片點下去直接導向該幣完整分析；首頁讀路徑
（`web.py::_render_home_overview_cached()`）不變，原樣嵌入這個 blob。

`coin` 必須通過 `COIN_POOL` 白名單才組連結，非白名單值保持純 `<div>`
不可點（防呆，見 `_overview_card_href` docstring）。
"""
from __future__ import annotations

import html
from urllib.parse import parse_qs, urlparse

import pytest

from scripts import fetch_scheduler
from trustforge.schema import COIN_POOL, QuestionType


def _fake_snapshot(coin: str) -> dict:
    return {
        "coin": coin,
        "trust_score": 0.5,
        "direction": "neutral",
        "calibrated_confidence": 0.5,
        "decision_state": "hold",
        "generated_at": "2026-07-01T00:00:00Z",
    }


@pytest.mark.parametrize("coin", list(COIN_POOL))
def test_overview_card_href_is_real_analyze_link_for_whitelisted_coin(coin):
    href = fetch_scheduler._overview_card_href(coin)
    assert href is not None
    assert href.startswith("/analyze?")

    qs = parse_qs(urlparse(html.unescape(href)).query)
    assert qs["coin"][0] == coin
    assert qs["type"][0] == QuestionType.MULTI_SOURCE.value
    assert qs["q"][0].strip()
    assert "sample" not in qs  # 真資料，不落在離線示範沙盒


def test_overview_card_href_rejects_non_whitelisted_coin():
    """非 `COIN_POOL` 白名單的幣種不組連結（回 `None`），避免卡片指向
    未知/非法幣種、多半 404 的連結。"""
    assert fetch_scheduler._overview_card_href("NOT_A_REAL_COIN") is None


def test_render_overview_html_wraps_each_card_in_real_analyze_link():
    snapshots = [_fake_snapshot(coin) for coin in COIN_POOL]
    htmlout = fetch_scheduler._render_overview_html(snapshots)

    assert htmlout  # 非空
    # 每張卡都是 <a href="/analyze?..."> 開頭的可導航連結，不是死 <div>
    assert htmlout.count('<a class="tf-overview-card" href="/analyze?') == len(COIN_POOL)
    # 死 <div> 版本不該再出現
    assert '<div class="tf-overview-card"' not in htmlout

    for coin in COIN_POOL:
        expected_href = fetch_scheduler._overview_card_href(coin)
        assert f'href="{expected_href}"' in htmlout


def test_render_overview_html_keeps_dead_div_for_non_whitelisted_coin():
    """防呆情境：若快照 dict 出現非白名單幣種（理論上不該發生），卡片保持
    純 `<div>` 不可點，不冒然組出未驗證的連結。"""
    htmlout = fetch_scheduler._render_overview_html([_fake_snapshot("NOT_A_REAL_COIN")])
    assert '<div class="tf-overview-card"' in htmlout
    assert "<a " not in htmlout


def test_render_overview_html_empty_snapshots_returns_empty_string():
    assert fetch_scheduler._render_overview_html([]) == ""
