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
    "fsc-vasp-registry",
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


# ── 接線點 5：跨來源鏡像去重（issue #385 驗收條件）─────────────────────────

def test_collect_dedupes_the_same_document_across_sources() -> None:
    """實測 FSC 三個 feed 中 tw-reg:fsc:202602260001 同時出現在 fsc-news 與
    fsc-notice。各來源自己 fetch() 內的去重擋不到跨來源鏡像，會算兩票。"""
    from trustforge.ingestion.base import Document, Source, collect

    class _Mirror(Source):
        kind = "regulatory"

        def __init__(self, name: str) -> None:
            self.name = name

        def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
            return [
                Document(
                    id="tw-reg:fsc:202602260001",
                    kind="regulatory",
                    source=self.name,
                    text="金管會公告完成洗錢防制登記之提供虛擬資產服務之事業或人員名單",
                    url="https://www.fsc.gov.tw/x",
                    ts=1.0,
                )
            ]

    # 這兩個名稱預設 disabled（見接線點 2），collect() 會過濾掉，需明確開啟。
    base.set_source_enabled_override("fsc-news", True)
    base.set_source_enabled_override("fsc-notice", True)

    docs = collect(
        "q", coin=None, sources=[_Mirror("fsc-news"), _Mirror("fsc-notice")]
    )
    assert [d.id for d in docs] == ["tw-reg:fsc:202602260001"], "一份公告只能一票"
    assert docs[0].source == "fsc-news", "保留第一次出現者"


def test_collect_keeps_genuinely_distinct_documents() -> None:
    """去重只按 id，不得誤殺不同文件。"""
    from trustforge.ingestion.base import Document, Source, collect

    class _Two(Source):
        kind = "regulatory"
        name = "fsc-news"

        def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
            return [
                Document(id="a", kind="regulatory", source=self.name, text="虛擬資產甲"),
                Document(id="b", kind="regulatory", source=self.name, text="虛擬資產乙"),
            ]

    base.set_source_enabled_override("fsc-news", True)
    assert [d.id for d in collect("q", coin=None, sources=[_Two()])] == ["a", "b"]


def test_id_collision_with_different_content_is_logged_not_silent(caplog) -> None:
    """id 相同但內容不同＝某來源的 id 生成有誤，必須被看見。"""
    import logging

    from trustforge.ingestion.base import Document, _dedupe_by_id

    with caplog.at_level(logging.WARNING, logger="trustforge.ingestion.base"):
        kept = _dedupe_by_id(
            [
                Document(id="same", kind="regulatory", source="s1", text="內容一"),
                Document(id="same", kind="regulatory", source="s2", text="內容二"),
            ]
        )
    assert len(kept) == 1
    assert "id 重複但內容不同" in caplog.text


def test_identical_mirror_does_not_log_warning(caplog) -> None:
    """真正的鏡像（內容相同）是預期情形，不該吵。"""
    import logging

    from trustforge.ingestion.base import Document, _dedupe_by_id

    with caplog.at_level(logging.WARNING, logger="trustforge.ingestion.base"):
        kept = _dedupe_by_id(
            [
                Document(id="same", kind="regulatory", source="fsc-news", text="同一份"),
                Document(id="same", kind="regulatory", source="fsc-notice", text="同一份"),
            ]
        )
    assert len(kept) == 1
    assert caplog.text == ""


# ── 接線點 6：運維啟用通道（issue #385）──────────────────────────────────

class _FakeAdminConfig:
    """最小的 admin config double，只帶本測試在意的兩個欄位。"""

    def __init__(self, *, enabled=None, disabled=None) -> None:
        self.enabled_sources = enabled
        self.disabled_sources = disabled


def _sync_with(monkeypatch, cfg) -> None:
    import trustforge.admin_config as admin_config

    monkeypatch.delenv("TRUSTFORGE_DISABLE_ADMIN_CONFIG", raising=False)
    monkeypatch.setattr(admin_config, "get_config", lambda store=None: cfg)
    base.sync_source_enabled_from_admin()


def test_admin_can_enable_a_default_disabled_source(monkeypatch) -> None:
    """在 #385 之前 admin 只有『關』的方向，`_DEFAULT_DISABLED_SOURCES` 內的
    源除了改碼重新部署之外無法啟用。"""
    assert base.get_source_enabled("fsc-news") is False
    _sync_with(monkeypatch, _FakeAdminConfig(enabled=["fsc-news"]))
    assert base.get_source_enabled("fsc-news") is True


def test_admin_enable_cannot_bypass_the_hoyabit_endpoint_precondition(
    monkeypatch,
) -> None:
    """`hoyabit-ticker` 不是被「缺啟用通道」擋著，而是被一個刻意的前置條件
    擋著：必須先設定合法 HTTPS 端點（`base.py` 的 `is_valid_hoyabit_endpoint`
    檢查在 override 之前）。admin 開關**不得**繞過它——沒有正式契約前，
    舊 placeholder 不該取得第一方信任（#167）。
    """
    monkeypatch.delenv("TRUSTFORGE_HOYABIT_TICKER_URL", raising=False)
    _sync_with(monkeypatch, _FakeAdminConfig(enabled=["hoyabit-ticker"]))
    assert base.get_source_enabled("hoyabit-ticker") is False, (
        "端點未設定時，admin 開關不得放行"
    )

    # 端點合法後才輪到 override 生效。
    monkeypatch.setenv("TRUSTFORGE_HOYABIT_TICKER_URL", "https://api.example.com/ticker")
    assert base.get_source_enabled("hoyabit-ticker") is True


def test_admin_disable_still_works(monkeypatch) -> None:
    _sync_with(monkeypatch, _FakeAdminConfig(disabled=["sec-gov"]))
    assert base.get_source_enabled("sec-gov") is False


def test_disable_wins_over_enable_fail_closed(monkeypatch) -> None:
    """同一個源同時列在兩邊 → 關勝過開（fail-closed）。"""
    _sync_with(
        monkeypatch,
        _FakeAdminConfig(enabled=["fsc-news"], disabled=["fsc-news"]),
    )
    assert base.get_source_enabled("fsc-news") is False


def test_neither_field_set_keeps_each_source_default(monkeypatch) -> None:
    _sync_with(monkeypatch, _FakeAdminConfig())
    assert base.get_source_enabled("fsc-news") is False   # 預設關
    assert base.get_source_enabled("sec-gov") is True     # 預設開


def test_enabled_source_then_flows_into_collect(monkeypatch) -> None:
    """啟用後必須真的被 collect() 走訪到——這才是通道有效的證據。"""
    _sync_with(monkeypatch, _FakeAdminConfig(enabled=sorted(TAIWAN_SOURCE_NAMES)))
    assert TAIWAN_SOURCE_NAMES <= _collected_source_names(monkeypatch)


# ── admin_config 儲存層對 enabled_sources 的支援 ─────────────────────────

def test_admin_config_accepts_enabled_sources_field() -> None:
    import trustforge.admin_config as admin_config

    assert "enabled_sources" in admin_config._ALLOWED_CHANGE_FIELDS


def test_admin_config_validates_enabled_sources() -> None:
    import pytest as _pytest

    import trustforge.admin_config as admin_config

    admin_config._validate_changes({"enabled_sources": ["fsc-news"]})
    admin_config._validate_changes({"enabled_sources": None})
    with _pytest.raises(ValueError, match="enabled_sources"):
        admin_config._validate_changes({"enabled_sources": "fsc-news"})
    with _pytest.raises(ValueError, match="enabled_sources"):
        admin_config._validate_changes({"enabled_sources": [""]})


def test_admin_config_public_dict_exposes_enabled_sources() -> None:
    import trustforge.admin_config as admin_config

    cfg = admin_config.AdminConfig(enabled_sources={"fsc-news", "mops-twse"})
    public = cfg.to_public_dict()
    assert public["enabled_sources"] == ["fsc-news", "mops-twse"]
    assert public["disabled_sources"] == []
    assert "live_token_hash" not in public, "機敏欄位不得外洩"


def test_web_startup_syncs_source_enablement() -> None:
    """產品端也必須 sync，否則 admin 開了之後排程寫 cache、web 卻不讀。"""
    import inspect

    from trustforge import web

    assert "sync_source_enabled_from_admin" in inspect.getsource(web.main)
