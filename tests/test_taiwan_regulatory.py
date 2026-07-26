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


def test_builder_returns_eight_distinct_sources() -> None:
    sources = build_taiwan_regulatory_sources()
    names = [s.name for s in sources]
    assert len(names) == len(set(names)) == 8
    assert set(names) == {
        "fsc-news", "fsc-penalty", "fsc-notice",
        "mops-twse", "mops-tpex", "twse-punish", "tpex-punish",
        "fsc-vasp-registry",
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


# ── FSC VASP 登記業者名單（issue #721）──────────────────────────────────
#
# 這個來源不用規則切版面，改由模型整理、規則做回源驗證（見
# `_VASP_ITEM_SCHEMA` 上方說明）。測試據此分兩類：
#   1. 結構化結果正確（用真實跑出來的 LLM 輸出當 fixture，不打 Bedrock）
#   2. **回源驗證擋得住杜撰**——這是 #385「不用 LLM 猜測」的底線

VASP_AREA = FIXTURES / "vasp_area.html"
VASP_PDF = FIXTURES / "vasp_list.pdf"
VASP_LLM_RECORDS = json.loads(
    (FIXTURES / "vasp_llm_records.json").read_text(encoding="utf-8")
)


def _stub_vasp(monkeypatch, records=None) -> None:
    """兩段擷取用 fixture，LLM 整理用實跑存下的輸出（測試不打 Bedrock）。"""

    def fake_fetch(url: str, **kwargs):
        if url.endswith(".pdf"):
            return VASP_PDF.read_bytes()
        return VASP_AREA.read_bytes()

    monkeypatch.setattr(tw.safe_fetch, "fetch_url", fake_fetch)

    import trustforge.bedrock as bedrock

    payload = VASP_LLM_RECORDS if records is None else records
    monkeypatch.setattr(
        bedrock.BedrockClient, "extract_records", lambda self, **kw: payload
    )


def test_vasp_registry_yields_one_document_per_company(monkeypatch) -> None:
    """這是唯一產出結構化實體清單的來源，其餘七源都只是文字公告。"""
    _stub_vasp(monkeypatch)
    docs = tw.FSCVASPRegistrySource().fetch("vasp")

    assert len(docs) == 8, "fixture 名單為 8 家登記業者"
    assert len({d.id for d in docs}) == 8
    for doc in docs:
        assert doc.id.startswith("tw-reg:fsc-vasp:")
        assert doc.meta["registry"] == "fsc-vasp-aml-registration"


def test_vasp_document_carries_structured_entity_fields(monkeypatch) -> None:
    _stub_vasp(monkeypatch)
    docs = tw.FSCVASPRegistrySource().fetch("vasp")
    hoya = next(d for d in docs if d.meta["tax_id"] == "90615871")

    assert hoya.meta["company_name"] == "禾亞數位科技股份有限公司"
    assert hoya.meta["brand"] == "HOYA BIT"
    assert hoya.meta["website"] == "https://hoyabit.com/"
    assert len(hoya.meta["licensed_business"]) == 4
    assert hoya.meta["registered_on"] == "2025-09-22"
    assert hoya.id == "tw-reg:fsc-vasp:90615871"


def test_vasp_doc_id_uses_the_government_issued_tax_id(monkeypatch) -> None:
    """統一編號是政府核發的實體唯一鍵，比任何內容 hash 都穩定。"""
    _stub_vasp(monkeypatch)
    for doc in tw.FSCVASPRegistrySource().fetch("vasp"):
        assert doc.id.split(":")[-1] == doc.meta["tax_id"]
        assert len(doc.meta["tax_id"]) == 8


# ── 回源驗證：模型只能整理，不能發明 ─────────────────────────────────────

def test_fabricated_company_is_dropped(monkeypatch, caplog) -> None:
    """原文沒有的業者一律丟棄——這是「不用 LLM 猜測」的守門。"""
    import logging

    fake = dict(VASP_LLM_RECORDS[0])
    fake["company_name"] = "虛構幣安台灣股份有限公司"
    fake["tax_id"] = "12345678"
    _stub_vasp(monkeypatch, VASP_LLM_RECORDS + [fake])

    with caplog.at_level(logging.WARNING, logger="trustforge.ingestion.taiwan_regulatory"):
        docs = tw.FSCVASPRegistrySource().fetch("vasp")

    assert len(docs) == 8, "杜撰的那筆必須被丟棄"
    assert "12345678" not in {d.meta["tax_id"] for d in docs}
    assert "找不到" in caplog.text


def test_malformed_tax_id_is_dropped(monkeypatch) -> None:
    bad = dict(VASP_LLM_RECORDS[0])
    bad["company_name"] = "拓荒數碼科技股份有限公司"
    bad["tax_id"] = "02-77566286"  # 電話被誤填成統編
    _stub_vasp(monkeypatch, [bad])

    with pytest.raises(TaiwanRegulatoryUnavailable):
        tw.FSCVASPRegistrySource().fetch("vasp")


def test_fabricated_optional_field_is_cleared_not_fatal(monkeypatch) -> None:
    """選填欄位比不到就清空，不丟棄整筆——業者本身仍是真的。"""
    tweaked = [dict(r) for r in VASP_LLM_RECORDS]
    tweaked[0]["website"] = "https://not-in-the-pdf.example.com/"
    tweaked[0]["brand"] = "完全沒出現過的品牌"
    _stub_vasp(monkeypatch, tweaked)

    docs = tw.FSCVASPRegistrySource().fetch("vasp")
    hoya = next(d for d in docs if d.meta["tax_id"] == "90615871")
    assert hoya.meta["website"] == ""
    assert hoya.meta["brand"] == ""
    assert hoya.meta["company_name"] == "禾亞數位科技股份有限公司"


def test_verification_tolerates_pdf_line_breaks(monkeypatch) -> None:
    """PDF 會把中文值硬斷行（`禾亞數位科技股份\\n有限公司`），模型接回後
    與原文逐字不同——回源比對必須先去空白，否則真實資料會被誤判為杜撰。"""
    source_text = "業者名稱 禾亞數位科技股份\n有限公司 90615871"
    record = {
        "company_name": "禾亞數位科技股份有限公司",
        "tax_id": "90615871",
        "business": [],
        "registered_on": "114年9月22日",
    }
    verified = tw.FSCVASPRegistrySource._verify_against_source(record, source_text)
    assert verified is not None
    assert verified["company_name"] == "禾亞數位科技股份有限公司"


def test_verification_rejects_text_absent_from_source() -> None:
    record = {
        "company_name": "不存在公司",
        "tax_id": "99999999",
        "business": [],
        "registered_on": "114年9月22日",
    }
    assert tw.FSCVASPRegistrySource._verify_against_source(record, "無關內容") is None


# ── 兩段擷取與 fail-closed ───────────────────────────────────────────────

def test_vasp_picks_the_list_pdf_not_the_other_attachments() -> None:
    """專區頁另掛「疑似洗錢態樣例示.pdf」等無關附件。"""
    from urllib.parse import unquote

    picked = tw.FSCVASPRegistrySource._find_list_pdf(
        VASP_AREA.read_text(encoding="utf-8")
    )
    assert picked is not None
    assert "VASP" in unquote(picked)
    assert "疑似洗錢態樣" not in unquote(picked)


def test_vasp_pdf_url_is_resolved_dynamically_not_hardcoded() -> None:
    """檔名嵌民國日期會隨每次更新改變，寫死就會在下次更新後失效。"""
    import inspect

    body = inspect.getsource(tw.FSCVASPRegistrySource._find_list_pdf)
    assert "1150722" not in body, "不得寫死當期 PDF 檔名"
    assert tw._VASP_PDF_NAME_TERMS == ("登記", "VASP")


def test_vasp_list_update_date_comes_from_the_filename() -> None:
    """`1150722` ＝ 2026-07-22，既是更新日期也是變更偵測訊號。"""
    url = "https://www.fsc.gov.tw/userfiles/file/1150722%E6%9B%B4%E6%96%B0.pdf"
    assert tw.FSCVASPRegistrySource._roc_date_from_filename(url) == date(2026, 7, 22)


def test_vasp_missing_pdf_link_is_fail_closed(monkeypatch) -> None:
    """版面改了找不到連結 → 來源不可用，**不得**回報成「名單為空」。"""
    monkeypatch.setattr(
        tw.safe_fetch, "fetch_url", lambda url, **kw: b"<html><body>no links</body></html>"
    )
    source = tw.FSCVASPRegistrySource()
    with pytest.raises(TaiwanRegulatoryUnavailable):
        source.fetch("vasp")
    assert source.last_degraded is True


def test_vasp_truncated_pdf_is_rejected(monkeypatch) -> None:
    """`safe_fetch` 超過 max_bytes 是靜默截斷；PDF 用 %%EOF 當 sentinel。"""
    whole = VASP_PDF.read_bytes()

    def fake(url: str, **kwargs):
        return whole[: len(whole) // 2] if url.endswith(".pdf") else VASP_AREA.read_bytes()

    monkeypatch.setattr(tw.safe_fetch, "fetch_url", fake)
    with pytest.raises(TaiwanRegulatoryUnavailable):
        tw.FSCVASPRegistrySource().fetch("vasp")


def test_vasp_llm_unavailable_is_fail_closed(monkeypatch) -> None:
    """模型不可用（離線／無憑證／逾時）→ 來源不可用，不得靜默回空名單。"""
    _stub_vasp(monkeypatch)
    import trustforge.bedrock as bedrock

    def boom(self, **kwargs):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(bedrock.BedrockClient, "extract_records", boom)
    with pytest.raises(TaiwanRegulatoryUnavailable):
        tw.FSCVASPRegistrySource().fetch("vasp")


def test_vasp_empty_result_never_reports_an_empty_registry(monkeypatch) -> None:
    """名單本來就不可能是空的（公告明載有數家業者）。整理不出來一律當失敗
    ——名單缺漏在信任評分上是危險的假陰性。"""
    _stub_vasp(monkeypatch, [])
    with pytest.raises(TaiwanRegulatoryUnavailable):
        tw.FSCVASPRegistrySource().fetch("vasp")


def test_vasp_pdf_host_must_be_allowlisted() -> None:
    assert "www.sfb.gov.tw" in ALLOWED_TW_HOSTS
    assert tw.FSCVASPRegistrySource()._validate_host(tw._VASP_AREA_URL) is True


def test_vasp_content_hash_is_the_pdf_not_the_landing_page(monkeypatch) -> None:
    """兩段擷取時 content hash 必須指向真正的資料本體。"""
    import hashlib

    _stub_vasp(monkeypatch)
    docs = tw.FSCVASPRegistrySource().fetch("vasp")
    expected = hashlib.sha256(VASP_PDF.read_bytes()).hexdigest()
    assert all(d.meta["content_hash"] == expected for d in docs)


def test_vasp_is_registered_in_the_builder() -> None:
    assert "fsc-vasp-registry" in {s.name for s in build_taiwan_regulatory_sources()}
