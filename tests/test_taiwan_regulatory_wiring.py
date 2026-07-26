"""台灣監管來源的管線接線測試（issue #385 階段 4）。

接線出錯的失敗模式是「靜默」的——adapter 寫得再對，沒接上就永遠沒資料，
且不會有任何錯誤。這組測試就是在鎖那四個接線點。
"""

from __future__ import annotations

import pytest

from trustforge.ingestion import base
from trustforge.ingestion.cache import COIN_AGNOSTIC_SOURCES
from trustforge.ingestion.taiwan_regulatory import build_taiwan_regulatory_sources

TAIWAN_SOURCE_NAMES = {
    "fsc-news",
    "fsc-penalty",
    "fsc-notice",
    "mops-twse",
    "mops-tpex",
    "twse-punish",
    "tpex-punish",
}


@pytest.fixture(autouse=True)
def _clean_overrides():
    """每個測試前後清掉 per-source override，避免互相污染。"""
    base.reset_source_enabled_overrides()
    yield
    base.reset_source_enabled_overrides()


def test_builder_covers_exactly_the_declared_source_names() -> None:
    assert {s.name for s in build_taiwan_regulatory_sources()} == TAIWAN_SOURCE_NAMES


# ── 接線點 1：base.collect() 的線上組裝 ───────────────────────────────────

def _collected_source_names(monkeypatch) -> set[str]:
    """跑一次線上 collect，回傳「實際被走訪到的來源名稱」。

    線上模式每個來源都包在 `CachedSource` 裡，cache-miss 會拋
    `CacheMissError` 被 `collect()` 攔下並記成 failed——重點不是成功與否，
    而是**有沒有被走訪到**，那才是接線是否生效的證據。不打任何網路。
    """
    seen: set[str] = set()

    def _spy(source, kind, coin, started, count, outcome, **kwargs):
        seen.add(source)

    monkeypatch.setattr(base, "_record_source_event", _spy)
    base.collect("crypto regulation", coin=None, offline=False)
    return seen


def test_online_assembly_visits_taiwan_sources_when_enabled(monkeypatch) -> None:
    """必須被建構進線上組裝，否則 override 開了也沒有東西可開。"""
    for name in TAIWAN_SOURCE_NAMES:
        base.set_source_enabled_override(name, True)
    assert TAIWAN_SOURCE_NAMES <= _collected_source_names(monkeypatch)


def test_online_assembly_skips_taiwan_sources_by_default(monkeypatch) -> None:
    assert not (TAIWAN_SOURCE_NAMES & _collected_source_names(monkeypatch))


def test_product_path_wraps_taiwan_sources_in_cache() -> None:
    """產品路徑不得直接打政府站——真呼叫只屬於排程器。"""
    import inspect

    from trustforge.ingestion.cache import CachedSource

    source_text = inspect.getsource(base.collect)
    assert "build_taiwan_regulatory_sources()" in source_text, (
        "台灣來源必須進 collect() 的線上組裝"
    )
    # 組裝後統一包 CachedSource，與其餘線上來源同一條路徑
    assert "CachedSource(s) for s in raw_sources" in source_text
    assert CachedSource is not None


# ── 接線點 2：預設 disabled ───────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(TAIWAN_SOURCE_NAMES))
def test_taiwan_sources_are_disabled_by_default(name: str) -> None:
    """實測 7 源中僅 fsc-news 有實質內容，且台灣監管文件多半不提幣別會被
    當成全市場通用塞進每個幣的池子。先觀察雜訊率再翻預設。"""
    assert base.get_source_enabled(name) is False


@pytest.mark.parametrize("name", sorted(TAIWAN_SOURCE_NAMES))
def test_override_can_enable_each_taiwan_source(name: str) -> None:
    base.set_source_enabled_override(name, True)
    assert base.get_source_enabled(name) is True


def test_disabling_taiwan_sources_does_not_disable_the_real_regulatory_source() -> None:
    """SEC EDGAR 不得被本單的預設關閉波及。"""
    assert base.get_source_enabled("sec-gov") is True


# ── 接線點 3：cache.COIN_AGNOSTIC_SOURCES ─────────────────────────────────

@pytest.mark.parametrize("name", sorted(TAIWAN_SOURCE_NAMES))
def test_taiwan_sources_are_coin_agnostic(name: str) -> None:
    """政府公告不分幣別。漏登記會變成每幣各打一次真 API 打政府站。"""
    assert name in COIN_AGNOSTIC_SOURCES


def test_existing_coin_agnostic_entries_preserved() -> None:
    assert {"alternative-me-fng", "sec-gov"} <= COIN_AGNOSTIC_SOURCES


# ── 接線點 4：fetch_scheduler 註冊表 ──────────────────────────────────────

def test_scheduler_registry_contains_every_taiwan_source() -> None:
    """真呼叫只發生在排程器；沒進註冊表就永遠不會被抓。"""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / "scripts" / "fetch_scheduler.py"
    spec = importlib.util.spec_from_file_location("_fetch_scheduler_probe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    registry = module.build_registry()
    assert TAIWAN_SOURCE_NAMES <= set(registry)
    # 註冊表放的是真 Source（排程器要打真 API），不是 CachedSource
    from trustforge.ingestion.cache import CachedSource

    for name in TAIWAN_SOURCE_NAMES:
        assert not isinstance(registry[name], CachedSource)
