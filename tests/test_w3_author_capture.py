"""W3 前置（第三輪 PR）：帳號維度資料累積驗收。

CEO 親測驗收標準：
  1. 離線 sample 跑 pipeline 後，evidence meta 含 author（有作者的源）；
     無作者的源缺鍵不炸。
  2. 快照/cache 裡能看到 author 存活。
  3. schema 相容測試：舊資料（無 author）全路徑正常。

本 PR 範圍只做資料累積前置——不做偵測演算法、不做 UI 顯示、不影響
trust 分數，這裡的測試只驗證「資料有沒有一路存活到底」。
"""
from __future__ import annotations

from trustforge import pipeline as pl
from trustforge.agent.orchestrator import (
    _sanitize_author,
    _scored_to_evidence,
    run_agent_pipeline,
)
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.ingestion.cache import doc_from_dict, doc_to_dict
from trustforge.schema import QuestionType
from trustforge.trust.scoring import Claim, ScoredClaim


# ---------------------------------------------------------------------------
# 1) 離線真樣本（demo/sample_data）跑完整 pipeline：舊資料（無 author）
#    全路徑正常——這是本 PR 部署前既有資料的真實形狀，schema 相容基準線。
# ---------------------------------------------------------------------------

def test_offline_sample_pipeline_no_crash_with_legacy_no_author_data():
    """demo/sample_data 目前完全沒有 author 欄位（本 PR 之前累積的舊資料）。
    跑完整 `pipeline.run(offline=True)`，不得崩潰，且每筆 evidence.author
    誠實為 `None`（codex vp-engineering 終審 MEDIUM，PR #107：型別已改
    `str | None = None`，缺鍵=未知，不再用空字串冒充）。"""
    report, evidence, _log = pl.run(
        "BTC", "分析 BTC", QuestionType.MULTI_SOURCE, offline=True,
        run_scope_id="test-w3-legacy-author",
    )
    assert report.coin == "BTC"
    assert len(evidence) > 0
    for ev in evidence:
        assert hasattr(ev, "author")
        assert ev.author is None


# ---------------------------------------------------------------------------
# 2) 混合資料（部分文件帶 author，部分不帶）跑 run_agent_pipeline：
#    author 從 Document.meta 一路存活到 Evidence.author，無 author 的
#    文件不受影響、不崩潰。
# ---------------------------------------------------------------------------

def _doc_with_author(id_: str, kind: str, source: str, text: str, author: str | None) -> Document:
    meta = {"content_reference": text[:60]}
    if author:
        meta["author"] = author
    return Document(id=id_, kind=kind, source=source, text=text, ts=1.0, meta=meta)


def test_mixed_authored_and_legacy_docs_author_survives_to_evidence():
    shared = "大額 機構 資金 布局 現貨 ETF 通過 推升 市場 信心"
    docs = [
        _doc_with_author("m-a", "social", "reddit-bitcoin", shared, "/u/crypto_trader_99"),
        _doc_with_author("m-b", "news", "coindesk", shared, None),   # 舊資料：無 author
        _doc_with_author("m-c", "onchain", "glassnode", shared, None),
    ]
    report, evidence = run_agent_pipeline(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: 1.0), now_fn=lambda: 1.0,
        run_scope_id="test-w3-author",
    )
    assert report.coin == "BTC"

    social_ev = next(e for e in evidence if e.source == "reddit-bitcoin")
    assert social_ev.author == "/u/crypto_trader_99"

    news_ev = next(e for e in evidence if e.source == "coindesk")
    assert news_ev.author is None

    onchain_ev = next(e for e in evidence if e.source == "glassnode")
    assert onchain_ev.author is None


# ---------------------------------------------------------------------------
# 3) cache 層：Document.meta（含 author）序列化/反序列化原樣存活；
#    舊快取 dict（無 meta 或 meta 非 dict）反序列化不炸、缺鍵=未知。
# ---------------------------------------------------------------------------

def test_doc_cache_roundtrip_preserves_author_in_meta():
    doc = Document(
        id="d1", kind="social", source="reddit-bitcoin", text="t", ts=1.0,
        meta={"content_reference": "ref", "author": "/u/hodler_jane"},
    )
    restored = doc_from_dict(doc_to_dict(doc))
    assert restored.meta.get("author") == "/u/hodler_jane"


def test_doc_cache_roundtrip_legacy_dict_without_meta_key_no_crash():
    """舊快取檔案格式（沒有 "meta" 鍵，本 PR 之前寫入的資料）反序列化
    不炸，meta 安全預設為 {}，author 自然缺鍵。"""
    legacy_dict = {"id": "d1", "kind": "news", "source": "coindesk", "text": "t", "url": "", "ts": 1.0}
    restored = doc_from_dict(legacy_dict)
    assert restored.meta == {}
    assert restored.meta.get("author") is None


def test_doc_cache_roundtrip_meta_present_but_no_author_key_no_crash():
    """舊快取（有 meta，但沒有 author 鍵，本 PR 部署前累積的真實舊資料）
    反序列化正常，author 缺鍵不炸。"""
    legacy_dict = {
        "id": "d1", "kind": "news", "source": "coindesk", "text": "t", "url": "", "ts": 1.0,
        "meta": {"content_reference": "ref"},
    }
    restored = doc_from_dict(legacy_dict)
    assert restored.meta.get("author") is None


# ---------------------------------------------------------------------------
# 4) `_sanitize_author()` 健壯性守門（codex vp-engineering 終審 MEDIUM，
#    PR #107，已實測 100KB `<script>` payload 可一路存進 90 天快照——
#    目前唯一防線是「沒人 render 它」，不可接受）。
# ---------------------------------------------------------------------------

def test_sanitize_author_accepts_normal_username():
    assert _sanitize_author("/u/crypto_trader_99") == "/u/crypto_trader_99"


def test_sanitize_author_none_and_empty_pass_through_as_none():
    assert _sanitize_author(None) is None
    assert _sanitize_author("") is None


def test_sanitize_author_rejects_over_200_chars_no_truncate():
    """超過 200 字整筆拒收（回 None），不是折衷截斷成前 200 字。"""
    exactly_200 = "a" * 200
    assert _sanitize_author(exactly_200) == exactly_200
    too_long = "a" * 201
    assert _sanitize_author(too_long) is None


def test_sanitize_author_rejects_html_tags():
    assert _sanitize_author("<script>alert(1)</script>") is None
    assert _sanitize_author("normal_user<b>bold</b>") is None
    assert _sanitize_author("<img src=x onerror=alert(1)>") is None


def test_sanitize_author_rejects_control_characters():
    assert _sanitize_author("user\x00name") is None
    assert _sanitize_author("user\nname") is None
    assert _sanitize_author("user\x7fname") is None
    assert _sanitize_author("user\ttab") is None


def test_scored_to_evidence_end_to_end_rejects_malicious_author_payload():
    """端到端：`_scored_to_evidence` 收到 100KB `<script>` payload 級的
    惡意 `doc.meta["author"]`，不會讓它流進 `Evidence.author`（CEO 實測
    的攻擊場景，見 PR #107 vp-engineering 終審）。"""
    malicious = "<script>" + "x" * 100_000 + "</script>"
    doc = Document(
        id="d1", kind="social", source="reddit-bitcoin", text="t", ts=1.0,
        meta={"content_reference": "ref", "author": malicious},
    )
    claim = Claim(id="c1", text="t", doc=doc)
    sc = ScoredClaim(claim=claim, trust=0.5, components={"reputation": 0.5})
    ev = _scored_to_evidence(sc, related="測試")
    assert ev.author is None
