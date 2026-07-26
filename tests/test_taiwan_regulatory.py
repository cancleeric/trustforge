"""台灣監管來源 adapters 測試（issue #385）。

以本輪實測的真實 response 當 fixture，鎖住三個地雷與 fail-closed 契約：
- `safe_fetch` 靜默截斷 → `</rss>` sentinel 必須攔下
- TWSE 欄位名 `'主旨 '` 帶結尾空白 → key 正規化必須吸收
- 關鍵字閘門 → 數百筆銀行裁罰不得混入每個幣的證據池
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from trustforge.ingestion import taiwan_regulatory as tw
from trustforge.ingestion.taiwan_regulatory import (
    ALLOWED_TW_HOSTS,
    FSCSource,
    MOPSSource,
    TaiwanRegulatorySource,
    TaiwanRegulatoryUnavailable,
    TPEXSource,
    TWSESource,
    build_taiwan_regulatory_sources,
)
from trustforge.ingestion.tw_datetime import TAIPEI, end_of_taipei_day

FIXTURES = Path(__file__).parent / "fixtures" / "taiwan"

ALL_SOURCES = [
    lambda: FSCSource("fsc-news"),
    lambda: FSCSource("fsc-penalty"),
    lambda: FSCSource("fsc-notice"),
    lambda: MOPSSource("mops-twse"),
    lambda: MOPSSource("mops-tpex"),
    TWSESource,
    TPEXSource,
]


def _stub_fetch(monkeypatch, payload: bytes | dict[str, bytes]) -> None:
    """攔截 safe_fetch，改回 fixture 內容（不打真實網路）。"""

    def fake(url: str, **kwargs):
        if isinstance(payload, dict):
            if url not in payload:
                raise AssertionError(f"未預期的 URL：{url}")
            return payload[url]
        return payload

    monkeypatch.setattr(tw.safe_fetch, "fetch_url", fake)


def _fail_fetch(monkeypatch, exc: Exception) -> None:
    def fake(url: str, **kwargs):
        raise exc

    monkeypatch.setattr(tw.safe_fetch, "fetch_url", fake)


# ── 基本契約 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("factory", ALL_SOURCES)
def test_every_source_is_regulatory_kind(factory) -> None:
    assert factory().kind == "regulatory"


@pytest.mark.parametrize("factory", ALL_SOURCES)
def test_every_endpoint_is_on_the_host_allowlist(factory) -> None:
    """端點寫死且必須落在白名單主機上（防 SSRF）。"""
    source = factory()
    assert source._endpoints, f"{source.name} 應有端點"
    for url in source._endpoints:
        assert source._validate_host(url), f"{source.name} 端點不在白名單：{url}"


def test_builder_returns_seven_distinct_sources() -> None:
    sources = build_taiwan_regulatory_sources()
    names = [s.name for s in sources]
    assert len(names) == len(set(names)) == 7
    assert set(names) == {
        "fsc-news", "fsc-penalty", "fsc-notice",
        "mops-twse", "mops-tpex", "twse-punish", "tpex-punish",
    }


def test_openapi_twse_host_is_allowlisted() -> None:
    """stub 版漏了這個實際要打的主機，鎖住不要再漏。"""
    assert "openapi.twse.com.tw" in ALLOWED_TW_HOSTS


def test_base_class_is_abstract() -> None:
    """共用基底不得直接實例化——`_parse`／`_to_document` 是抽象方法。"""
    with pytest.raises(TypeError):
        TaiwanRegulatorySource()


def test_host_validation_rejects_outsiders() -> None:
    source = FSCSource("fsc-news")  # 走具體子類，基底為抽象
    assert source._validate_host("https://www.fsc.gov.tw/RSS/Messages") is True
    assert source._validate_host("https://openapi.twse.com.tw/v1/x") is True
    assert source._validate_host("https://evil.example.com/") is False
    # 白名單主機但非 https 一樣擋掉
    assert source._validate_host("http://www.fsc.gov.tw/RSS/Messages") is False


@pytest.mark.parametrize("bad_feed", ["fsc-unknown", "", "news"])
def test_unknown_fsc_feed_rejected(bad_feed: str) -> None:
    with pytest.raises(ValueError):
        FSCSource(bad_feed)


# ── FSC RSS ──────────────────────────────────────────────────────────────

def test_fsc_parses_real_fixture_into_documents(monkeypatch) -> None:
    _stub_fetch(monkeypatch, (FIXTURES / "fsc_penalty.xml").read_bytes())
    docs = FSCSource("fsc-penalty").fetch("crypto", coin="BTC")

    # fixture 20 筆中僅 3 筆命中加密關鍵字 → 閘門必須擋掉其餘 17 筆。
    assert 1 <= len(docs) <= 3
    for doc in docs:
        assert doc.kind == "regulatory"
        assert doc.source == "fsc-penalty"
        assert doc.id.startswith("tw-reg:fsc:")
        assert doc.url.startswith("https://www.fsc.gov.tw/")
        assert "&amp;" not in doc.url, "CDATA 內的字面 &amp; 應已還原"


def test_fsc_document_has_every_required_contract_field(monkeypatch) -> None:
    """#385 驗收條件：schema_version / source / url / published_at /
    fetched_at / content hash 全部要有。"""
    _stub_fetch(monkeypatch, (FIXTURES / "fsc_penalty.xml").read_bytes())
    doc = FSCSource("fsc-penalty").fetch("crypto")[0]

    assert doc.schema_version
    assert doc.source == "fsc-penalty"
    assert doc.url
    assert doc.meta["published_at"]
    assert doc.meta["fetched_at"]
    assert len(doc.meta["content_hash"]) == 64
    assert doc.meta["source_region"] == "TW"
    assert doc.meta["agency"] == "金融監督管理委員會"
    assert doc.meta["adapter_status"] == "live"
    assert doc.meta["live_source"] is True
    assert doc.meta["url_kind"] == "permalink"


def test_fsc_truncated_response_is_rejected_not_silently_parsed(monkeypatch) -> None:
    """safe_fetch 超過 max_bytes 是靜默截斷；sentinel 是唯一偵測點。"""
    _stub_fetch(monkeypatch, (FIXTURES / "fsc_penalty_truncated.xml").read_bytes())
    source = FSCSource("fsc-penalty")

    with pytest.raises(TaiwanRegulatoryUnavailable):
        source.fetch("crypto")

    assert source.last_truncated is True
    assert source.last_failures == source.last_attempts


def test_fsc_dedups_the_same_official_announcement(monkeypatch) -> None:
    """同一官方公告的鏡像不能算多票（#385 驗收條件）。"""
    raw = (FIXTURES / "fsc_penalty.xml").read_text(encoding="utf-8")
    # 把整份 items 複製一份塞回同一個 feed，模擬鏡像重複。
    head, _, rest = raw.partition("<item>")
    body = "<item>" + rest.replace("</channel>", "").replace("</rss>", "")
    doubled = (head + body + body + "</channel></rss>").encode("utf-8")

    _stub_fetch(monkeypatch, doubled)
    docs = FSCSource("fsc-penalty").fetch("crypto")
    assert len(docs) == len({d.id for d in docs}), "重複公告應只留一份"


def test_fsc_id_is_stable_across_processes(monkeypatch) -> None:
    """stub 版用內建 hash() 會被 PYTHONHASHSEED 隨機化；id 必須可重現。"""
    _stub_fetch(monkeypatch, (FIXTURES / "fsc_penalty.xml").read_bytes())
    first = [d.id for d in FSCSource("fsc-penalty").fetch("crypto")]
    second = [d.id for d in FSCSource("fsc-penalty").fetch("crypto")]
    assert first == second
    # id 直接由來源自身的 dataserno 決定，不含任何隨機成分。
    for doc_id in first:
        assert doc_id.split(":")[-1].isdigit()


def test_fsc_keyword_gate_blocks_unrelated_bank_penalties(monkeypatch) -> None:
    """實測：498 筆裁罰中 38 筆只因『洗錢防制』命中、4 筆只因『加密』命中，
    全數為誤報。閘門不得放行這類文件。"""
    _stub_fetch(monkeypatch, (FIXTURES / "fsc_penalty.xml").read_bytes())
    docs = FSCSource("fsc-penalty").fetch("crypto")
    for doc in docs:
        assert any(term in doc.text for term in tw._CRYPTO_TERMS)


def test_keyword_gate_does_not_use_bare_encryption_term() -> None:
    """『加密』單獨成詞會誤收資安『資料加密』缺失案。"""
    assert "加密" not in tw._CRYPTO_TERMS
    assert "洗錢防制" not in tw._CRYPTO_TERMS
    assert "詐騙" not in tw._CRYPTO_TERMS


# ── PIT ──────────────────────────────────────────────────────────────────

def test_pit_excludes_documents_published_after_analysis_time(monkeypatch) -> None:
    """#385 驗收條件：PIT 測試排除分析時間後發布資料。"""
    _stub_fetch(monkeypatch, (FIXTURES / "fsc_penalty.xml").read_bytes())
    source = FSCSource("fsc-penalty")

    all_docs = source.fetch("crypto")
    assert all_docs, "fixture 應至少產出一筆"

    earliest = min(d.meta["visible_at_epoch"] for d in all_docs)
    as_of = datetime.fromtimestamp(earliest, tz=timezone.utc)

    before = source.fetch(
        "crypto", as_of=as_of.replace(year=as_of.year - 1)
    )
    assert before == [], "分析時間早於所有資料時應全數排除"

    at_boundary = source.fetch("crypto", as_of=as_of)
    assert len(at_boundary) >= 1, "邊界為含入"


def test_pit_visible_at_takes_the_later_of_issue_and_listing(monkeypatch) -> None:
    """實測樣本 pubDate 7/21 但 dataserno 顯示 7/22 上架 → 取 7/22。"""
    _stub_fetch(monkeypatch, (FIXTURES / "fsc_penalty.xml").read_bytes())
    docs = FSCSource("fsc-penalty").fetch("crypto")
    for doc in docs:
        listed_day = date(
            int(doc.meta["dataserno"][:4]),
            int(doc.meta["dataserno"][4:6]),
            int(doc.meta["dataserno"][6:8]),
        )
        visible = datetime.fromtimestamp(
            doc.meta["visible_at_epoch"], tz=timezone.utc
        )
        assert visible >= end_of_taipei_day(listed_day) - _one_second()


def _one_second():
    from datetime import timedelta

    return timedelta(seconds=1)


# ── MOPS / TWSE / TPEx OpenAPI ───────────────────────────────────────────

def test_mops_twse_trailing_space_field_name_does_not_crash(monkeypatch) -> None:
    """TWSE 的欄位名是 '主旨 '（結尾空白）。直接 r["主旨"] 會 KeyError。"""
    raw = (FIXTURES / "mops_twse.json").read_bytes()
    records = json.loads(raw)
    assert any(k == "主旨 " for k in records[0]), "fixture 應保留原始結尾空白"

    _stub_fetch(monkeypatch, raw)
    source = MOPSSource("mops-twse")
    source.fetch("crypto")  # 不得拋 KeyError
    assert source.last_failures == 0


def test_mops_field_map_reads_both_twse_and_tpex_schemas(monkeypatch) -> None:
    """同一份 MOPS 資料，TWSE 與 TPEx 用兩套欄位名。"""
    for market, fixture in (
        ("mops-twse", "mops_twse.json"),
        ("mops-tpex", "mops_tpex.json"),
    ):
        raw = (FIXTURES / fixture).read_bytes()
        _stub_fetch(monkeypatch, raw)
        source = MOPSSource(market)
        source.fetch("crypto")
        assert source.last_failures == 0

        # 直接驗欄位映射能取到值（繞過關鍵字閘門）。
        record = {k.strip(): v for k, v in json.loads(raw)[0].items()}
        assert source._field(record, "code")
        assert source._field(record, "company")
        assert source._field(record, "subject")


def test_mops_marks_history_as_not_backfillable(monkeypatch) -> None:
    """重大訊息只有當日 snapshot，不可假裝有歷史。"""
    assert MOPSSource("mops-twse")._history_backfillable is False
    assert MOPSSource("mops-tpex")._history_backfillable is False


def test_mops_url_is_labelled_as_query_page_not_permalink() -> None:
    """資料集無 per-announcement 連結，不得假裝是永久連結。"""
    assert MOPSSource("mops-twse")._url_kind == "query-page"
    assert TWSESource()._url_kind == "query-page"


def test_punish_sources_have_history() -> None:
    """裁罰專區與重大訊息不同，有年度歷史。"""
    assert TWSESource()._history_backfillable is True
    assert TPEXSource()._history_backfillable is True


def test_punish_sources_parse_real_fixtures(monkeypatch) -> None:
    for factory, fixture in ((TWSESource, "twse_punish.json"),
                             (TPEXSource, "tpex_punish.json")):
        _stub_fetch(monkeypatch, (FIXTURES / fixture).read_bytes())
        source = factory()
        source.fetch("crypto")
        assert source.last_failures == 0
        assert source.last_degraded is False


def test_openapi_records_with_missing_keys_are_skipped_not_fatal(monkeypatch) -> None:
    """單筆髒值只跳過該筆，不拖累整批。"""
    good = json.loads((FIXTURES / "mops_twse.json").read_text(encoding="utf-8"))
    payload = json.dumps(
        [{}, {"公司代號": "1234"}, *good], ensure_ascii=False
    ).encode("utf-8")
    _stub_fetch(monkeypatch, payload)
    source = MOPSSource("mops-twse")
    source.fetch("crypto")
    assert source.last_failures == 0


def test_openapi_non_list_payload_is_schema_drift(monkeypatch) -> None:
    _stub_fetch(monkeypatch, b'{"unexpected": "object"}')
    source = MOPSSource("mops-twse")
    with pytest.raises(TaiwanRegulatoryUnavailable):
        source.fetch("crypto")
    assert source.last_degraded is True


# ── fail-closed ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("factory", ALL_SOURCES)
def test_network_failure_raises_unavailable_not_silent_empty(
    factory, monkeypatch
) -> None:
    """來源掛掉不可偽裝成『本來就沒有相關公告』。"""
    _fail_fetch(monkeypatch, TimeoutError("timeout"))
    source = factory()
    with pytest.raises(TaiwanRegulatoryUnavailable):
        source.fetch("crypto")
    assert source.last_degraded is True
    assert source.last_failures == source.last_attempts


def test_malformed_xml_is_recorded_as_degraded(monkeypatch) -> None:
    _stub_fetch(monkeypatch, b"<rss><channel><item></rss>")
    source = FSCSource("fsc-news")
    with pytest.raises(TaiwanRegulatoryUnavailable):
        source.fetch("crypto")
    assert source.last_degraded is True


def test_empty_but_valid_feed_returns_empty_without_raising(monkeypatch) -> None:
    """真的沒有相關公告 → 回空清單，不是錯誤。"""
    _stub_fetch(
        monkeypatch,
        b"<?xml version='1.0'?><rss><channel><title>x</title></channel></rss>",
    )
    source = FSCSource("fsc-news")
    assert source.fetch("crypto") == []
    assert source.last_degraded is False
    assert source.last_failures == 0


# ── 閘門命中位置（精準度訊號）────────────────────────────────────────────

def test_gate_match_labels_title_hits_separately(monkeypatch) -> None:
    """實測 fsc-news：標題命中的 7 筆全為真正的 VASP／虛擬資產監管事件，
    僅內文命中的 16 筆多為新聞彙編等雜訊。兩者都保留，但要能區分。"""
    _stub_fetch(monkeypatch, (FIXTURES / "fsc_penalty.xml").read_bytes())
    docs = FSCSource("fsc-penalty").fetch("crypto")
    for doc in docs:
        assert doc.meta["gate_match"] in {"title", "body"}
        title = doc.text.splitlines()[0]
        if doc.meta["gate_match"] == "title":
            assert any(t in title for t in tw._CRYPTO_TERMS)
        else:
            assert not any(t in title for t in tw._CRYPTO_TERMS)


def test_gate_match_returns_none_for_unrelated_content() -> None:
    assert tw._gate_match("臺灣銀行內部控制缺失", "違反銀行法第45條") is None


def test_gate_match_prefers_title() -> None:
    assert tw._gate_match("虛擬資產服務法三讀", "無關內文") == "title"
    assert tw._gate_match("金管會每日新聞", "內文提及虛擬資產") == "body"


def test_tokenisation_term_included() -> None:
    """『RWA代幣化小組』是真實的監管事件，初版詞集漏收。"""
    assert "代幣化" in tw._CRYPTO_TERMS
    assert tw._gate_match("「RWA代幣化小組」完成驗證技術的可行性", "") == "title"
