"""D0.3（#133）：SEC EDGAR 全文檢索（FTS）深化。

- 詞表覆蓋率擴充：_QUERY_TERMS 由 3 詞擴為具名幣 + 通用加密概念詞組合。
- _parse_fts_hit 型別防禦：任何關鍵欄位型別不符一律回 None，不炸。
- 單詞失敗隔離：一個查詢詞抓取/解析失敗不拖累整批，其餘詞照常檢索。
"""
from __future__ import annotations

from urllib.error import URLError

from trustforge.ingestion import regulatory
from trustforge.ingestion.base import Document, Source, collect


def _fts_fixture_for(terms):
    """造一份 EDGAR FTS JSON fixture：每個 term 對應一筆 hit，_id 唯一。"""
    import json
    hits = []
    for i, t in enumerate(terms):
        hits.append({
            "_id": f"0001622876-00-00000{i}:exhibit{i}.txt",
            "_source": {
                "ciks": ["0001622876"],
                "display_names": [f"Filer {i}"],
                "form": "8-K",
                "file_date": "2026-08-01",
                "items": ["Item 1.01"],
            },
        })
    return json.dumps({"hits": {"hits": hits}}).encode()


def _single_hit_fixture(idx: int) -> bytes:
    """只含第 idx 筆 hit 的 fixture（模擬「該查詢詞僅命中這一筆」）。"""
    import json
    return json.dumps({"hits": {"hits": [{
        "_id": f"0001622876-00-00000{idx}:exhibit{idx}.txt",
        "_source": {
            "ciks": ["0001622876"],
            "display_names": [f"Filer {idx}"],
            "form": "8-K",
            "file_date": "2026-08-01",
            "items": ["Item 1.01"],
        },
    }]}}).encode()


def test_query_terms_expanded_beyond_original_three():
    """詞表覆蓋率擴充：至少包含原始 3 詞，且數量大於 3（#133 深化）。"""
    terms = regulatory._QUERY_TERMS
    for original in ("bitcoin", "ethereum", "cryptocurrency"):
        assert original in terms, f"原始關鍵詞 {original} 應保留"
    assert len(terms) > 3, f"詞表應擴充（>3），實得 {len(terms)}"


def test_parse_fts_hit_rejects_malformed_types():
    """型別防禦：關鍵欄位型別不符（ciks 非 list / _id 非 str / display_names
    夾雜非 str / form 非 str）一律回 None，不拋例外。"""
    base = {
        "_id": "0001622876-00-000000:ex.txt",
        "_source": {
            "ciks": ["0001622876"],
            "display_names": ["Filer"],
            "form": "8-K",
            "file_date": "2026-08-01",
            "items": ["Item 1.01"],
        },
    }
    # 正常 → 應回 Document
    ok = regulatory._parse_fts_hit(dict(base), "bitcoin")
    assert isinstance(ok, Document)

    # ciks 非 list
    bad_ciks = {**base, "_source": {**base["_source"], "ciks": "0000000000"}}
    assert regulatory._parse_fts_hit(bad_ciks, "bitcoin") is None

    # ciks 含 None / 非 str 元素
    bad_ciks2 = {**base, "_source": {**base["_source"], "ciks": [None, 123]}}
    assert regulatory._parse_fts_hit(bad_ciks2, "bitcoin") is None

    # _id 非 str
    bad_id = {**base, "_id": 12345}
    assert regulatory._parse_fts_hit(bad_id, "bitcoin") is None

    # display_names 夾雜非 str → 仍回 Document（過濾非 str 後取第一個 str）
    mixed_names = {**base, "_source": {**base["_source"], "display_names": [None, "RealFiler", 7]}}
    ok2 = regulatory._parse_fts_hit(mixed_names, "bitcoin")
    assert isinstance(ok2, Document)
    assert "RealFiler" in ok2.text

    # form 非 str → 回 Document（form 視為空字串）
    bad_form = {**base, "_source": {**base["_source"], "form": 8}}
    ok3 = regulatory._parse_fts_hit(bad_form, "bitcoin")
    assert isinstance(ok3, Document)


def test_single_term_failure_isolated_from_batch(monkeypatch):
    """單詞失敗隔離：其中一個詞抓取失敗（URLError），其餘詞仍正常檢索，
    整批不崩、不歸零——失敗詞對應的命中不被納入，但成功詞的命中照常回傳。"""
    terms = regulatory._QUERY_TERMS

    # 每個詞只回傳自己的單筆命中；第 1 個詞（call #1）抓取時炸。
    calls = {"n": 0}

    def _mock_fetch(url: str) -> bytes:
        calls["n"] += 1
        if calls["n"] == 1:
            raise URLError("simulated network timeout")
        return _single_hit_fixture(calls["n"] - 1)

    monkeypatch.setattr(regulatory, "_fetch_url", _mock_fetch)
    docs = regulatory.SECFullTextSearchSource().fetch("", coin="")

    # 不拋例外、回 list
    assert isinstance(docs, list)
    # 失敗的是第 1 詞（idx 0），其餘 len(terms)-1 詞各回 1 筆命中、去重後
    # = len(terms)-1（idx 1..len(terms)-1）
    expected = len(terms) - 1
    assert len(docs) == expected, (
        f"單詞失敗隔離：失敗詞不應拖累整批，應回 {expected} 筆，實得 {len(docs)}"
    )
    # 成功詞的命中仍帶正確 matched_term
    assert all(d.meta.get("matched_term") for d in docs)


def test_parse_error_term_isolated(monkeypatch):
    """回應非 JSON（解析失敗）的詞被隔離，不拖累整批。"""
    terms = regulatory._QUERY_TERMS

    calls = {"n": 0}

    def _mock_fetch(url: str) -> bytes:
        calls["n"] += 1
        if calls["n"] == 2:
            return b"not valid json <<<<"
        return _single_hit_fixture(calls["n"] - 1)

    monkeypatch.setattr(regulatory, "_fetch_url", _mock_fetch)
    docs = regulatory.SECFullTextSearchSource().fetch("", coin="")
    expected = len(terms) - 1
    assert len(docs) == expected, (
        f"解析失敗詞應被隔離，應回 {expected} 筆，實得 {len(docs)}"
    )


def test_collect_still_works_with_isolated_failure(monkeypatch):
    """端到端：單詞失敗隔離在 collect 路徑也成立（不崩、回 list）。"""
    terms = regulatory._QUERY_TERMS

    calls = {"n": 0}

    def _mock_fetch(url: str) -> bytes:
        calls["n"] += 1
        if calls["n"] == 1:
            raise URLError("boom")
        return _single_hit_fixture(calls["n"] - 1)

    monkeypatch.setattr(regulatory, "_fetch_url", _mock_fetch)
    src = regulatory.SECFullTextSearchSource()
    docs = collect("BTC", coin="BTC", sources=[src], offline=False)
    assert isinstance(docs, list)
    sec_docs = [d for d in docs if d.source == "sec-gov"]
    assert len(sec_docs) == len(terms) - 1


# ─────────────────────────────────────────────────────────────────────────────
# #141 可觀測性 + 降級旗標：FTS 失敗不再靜默吞錯
#   (a) 失敗時 log 明確記錄（含命中詞、來源、例外類型）
#   (b) 失敗計數 / 可觀測狀態
#   (c) 全數失敗時拋 RegulatoryFTSUnavailable，上游（collect._failed）看得到
#   (d) UA typo 修正（hurricanessoft → hurricanesoft，對外送 SEC）
#   關鍵不變式：空結果（查無 filing）是合法低頻，不算降級、不拋。
# ─────────────────────────────────────────────────────────────────────────────


def test_user_agent_domain_typo_fixed():
    """(d) 送給 SEC 的 User-Agent 網域拼字修正：hurricanessoft → hurricanesoft。"""
    assert "hurricanessoft" not in regulatory._UA, "錯誤網域 hurricanessoft 不應再出現"
    assert "hurricanesoft.com.tw" in regulatory._UA, "應為正確網域 hurricanesoft.com.tw"


def test_all_terms_fail_raises_fts_unavailable(monkeypatch):
    """(c) 全部查詢詞都失敗（FTS 整體不可用）→ 拋 RegulatoryFTSUnavailable，
    而非靜默回空清單（把『FTS 掛掉』誤當成『本來就沒有相關 filing』）。"""
    monkeypatch.setattr(
        regulatory, "_fetch_url",
        lambda url: (_ for _ in ()).throw(URLError("total outage")),
    )
    import pytest
    src = regulatory.SECFullTextSearchSource()
    with pytest.raises(regulatory.RegulatoryFTSUnavailable):
        src.fetch("", coin="")
    # (b) 可觀測狀態：全數失敗
    assert src.last_attempts == len(regulatory._QUERY_TERMS)
    assert src.last_failures == len(regulatory._QUERY_TERMS)
    assert src.last_degraded is True
    assert set(src.last_failed_terms) == set(regulatory._QUERY_TERMS)


def test_all_terms_fail_surfaces_to_upstream_via_collect(monkeypatch):
    """(c) 上游可見：全數失敗經 collect 時，來源名稱會進 `_failed`（→ pipeline
    會補進 report.limits，讓 abstain 可解釋），而不是靜默漏掉整個監管來源。"""
    monkeypatch.setattr(
        regulatory, "_fetch_url",
        lambda url: (_ for _ in ()).throw(URLError("total outage")),
    )
    src = regulatory.SECFullTextSearchSource()
    failed: list[str] = []
    docs = collect("BTC", coin="BTC", sources=[src], offline=False, _failed=failed)
    # 不崩、回 list（collect 既有 try/except 接住）
    assert isinstance(docs, list)
    assert not [d for d in docs if d.source == "sec-gov"]
    # 上游降級旗標：sec-gov 被記為本輪未取得資料
    assert "sec-gov" in failed


def test_fetch_failure_is_logged_with_term_and_exception_type(monkeypatch, caplog):
    """(a) 失敗時有明確 log：含命中詞（term）、來源（source）、例外類型。"""
    import logging as _logging
    monkeypatch.setattr(
        regulatory, "_fetch_url",
        lambda url: (_ for _ in ()).throw(URLError("boom-timeout")),
    )
    src = regulatory.SECFullTextSearchSource()
    with caplog.at_level(_logging.WARNING, logger="trustforge.ingestion.regulatory"):
        try:
            src.fetch("", coin="")
        except regulatory.RegulatoryFTSUnavailable:
            pass
    text = caplog.text
    assert "抓取失敗" in text, "應記錄抓取失敗事件"
    assert "URLError" in text, "log 應含例外類型（error_type）"
    assert "sec-gov" in text, "log 應含來源名稱"
    # 至少一個查詢詞名稱出現在 log（含命中詞）
    assert any(t in text for t in regulatory._QUERY_TERMS), "log 應含命中詞 term"
    # 全數失敗的彙總 log 也在
    assert "FTS 不可用" in text


def test_parse_failure_is_logged(monkeypatch, caplog):
    """(a) 回應非合法 JSON 的失敗也要記 log，不再靜默 continue。"""
    import logging as _logging
    monkeypatch.setattr(regulatory, "_fetch_url", lambda url: b"<<< not json >>>")
    src = regulatory.SECFullTextSearchSource()
    with caplog.at_level(_logging.WARNING, logger="trustforge.ingestion.regulatory"):
        try:
            src.fetch("", coin="")
        except regulatory.RegulatoryFTSUnavailable:
            pass
    assert "非合法 JSON" in caplog.text


def test_partial_failure_degrades_but_does_not_raise(monkeypatch, caplog):
    """(b)(c) 部分查詢詞失敗：仍回傳其餘詞命中（不拋），但標記 degraded + 計數 +
    記彙總 log。維持 #133 單詞失敗隔離精神。"""
    import logging as _logging
    terms = regulatory._QUERY_TERMS
    calls = {"n": 0}

    def _mock_fetch(url: str) -> bytes:
        calls["n"] += 1
        if calls["n"] == 1:
            raise URLError("only first term fails")
        return _single_hit_fixture(calls["n"] - 1)

    monkeypatch.setattr(regulatory, "_fetch_url", _mock_fetch)
    src = regulatory.SECFullTextSearchSource()
    with caplog.at_level(_logging.WARNING, logger="trustforge.ingestion.regulatory"):
        docs = src.fetch("", coin="")  # 不應拋
    assert len(docs) == len(terms) - 1
    assert src.last_degraded is True
    assert src.last_failures == 1
    assert src.last_attempts == len(terms)
    assert len(src.last_failed_terms) == 1
    assert "部分降級" in caplog.text


def test_empty_result_is_not_degraded_and_does_not_raise(monkeypatch):
    """關鍵不變式：所有查詢詞都成功但查無 filing（合法低頻）→ 回空清單、
    不算降級、不拋。避免把『正常無命中』誤報成『FTS 不可用』。"""
    monkeypatch.setattr(
        regulatory, "_fetch_url",
        lambda url: b'{"hits": {"hits": []}}',
    )
    src = regulatory.SECFullTextSearchSource()
    docs = src.fetch("", coin="")  # 不拋
    assert docs == []
    assert src.last_degraded is False
    assert src.last_failures == 0
    assert src.last_attempts == len(regulatory._QUERY_TERMS)


def test_healthy_fetch_resets_observability_state(monkeypatch):
    """回歸：健康抓取（有命中）→ 可觀測狀態為非降級、零失敗。"""
    monkeypatch.setattr(
        regulatory, "_fetch_url",
        lambda url: _single_hit_fixture(0),
    )
    src = regulatory.SECFullTextSearchSource()
    docs = src.fetch("", coin="")
    assert len(docs) >= 1
    assert src.last_degraded is False
    assert src.last_failures == 0
    assert src.last_failed_terms == []
