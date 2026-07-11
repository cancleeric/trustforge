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
