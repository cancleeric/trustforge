"""測試「同一來源只算一個獨立聲音」不變量（issue #106）。

本檔驗證 aggregate_trust_by_kind／detect_cross_source_signal／build_report 的
n_indep 三處呼叫端統一透過 _independent_source_keys / _count_independent_sources
（以 _normalize_source_key 正規化去重）收斂，確保同一來源的大小寫/空白變體
（如 "CoinDesk" vs " coindesk " vs "COINDESK"）不再被誤判成多個獨立來源，
避免同源大小寫/空白變體灌水而虛增獨立來源計數。
"""
from __future__ import annotations

from trustforge.agent.orchestrator import (
    _count_independent_sources,
    _independent_source_keys,
    aggregate_trust_by_kind,
    build_report,
    detect_cross_source_signal,
)
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.schema import Evidence, QuestionType
from trustforge.trust.scoring import Claim, ScoredClaim, aggregate, extract_claims, score


def _doc(id_: str, kind: str, source: str, text: str = "", ts: float = 1_000_000.0, meta: dict | None = None) -> Document:
    return Document(id=id_, kind=kind, source=source, text=text, ts=ts, meta=meta or {})


def _sc(id_: str, kind: str, source: str, direction: str, trust: float) -> ScoredClaim:
    doc = _doc(id_, kind, source)
    claim = Claim(id=id_, text=f"claim-{id_}", doc=doc, direction=direction)
    return ScoredClaim(claim=claim, trust=trust)


def _ev(source: str, kind: str, trust: float, content_reference: str = "ref") -> Evidence:
    return Evidence(
        source=source,
        fetched_at="2026-01-01T00:00:00Z",
        content_reference=content_reference,
        related_claim="c1",
        kind=kind,
        trust=trust,
    )


def _run_report(brief, qtype=QuestionType.MULTI_SOURCE, query="分析 BTC", now: float = 1_000_000.0):
    return build_report(
        query=query, coin="BTC", qtype=qtype, brief=brief,
        client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: now),
        now_fn=lambda: now,
        run_scope_id="test-source-dedup",
    )


def _aggregate_from_docs(docs: list[Document], query: str = "分析 BTC", now: float = 1_000_000.0):
    return aggregate(score(extract_claims(docs), now=now), query=query)


# ─── 1. 純函式單元測試 ──────────────────────────────────────────────

def test_independent_source_keys_collapses_case_and_whitespace_variants():
    """大小寫/空白變體應收斂為同一個正規化 key，集合長度為 1、count 為 1。"""
    sources = ["CoinDesk", " coindesk ", "COINDESK"]
    keys = _independent_source_keys(sources)
    count = _count_independent_sources(sources)
    assert len(keys) == 1, f"期望 1 個獨立來源，實得 {len(keys)}: {keys}"
    assert count == 1, f"期望 count=1，實得 {count}"


def test_independent_source_keys_skips_falsy_entries_safely():
    """None 與空字串等 falsy 項目應被安全跳過，不拋例外、不計入結果。"""
    sources = ["a", None, "", "b"]
    keys = _independent_source_keys(sources)
    count = _count_independent_sources(sources)
    assert len(keys) == 2, f"期望 2 個獨立來源（'a', 'b'），實得 {len(keys)}: {keys}"
    assert count == 2, f"期望 count=2，實得 {count}"


def test_independent_source_keys_preserves_truly_distinct_sources():
    """真正不同的來源不應被過度去重，兩個不同來源應得長度 2。"""
    sources = ["binance", "coindesk"]
    keys = _independent_source_keys(sources)
    count = _count_independent_sources(sources)
    assert len(keys) == 2, f"期望 2 個獨立來源，實得 {len(keys)}: {keys}"
    assert count == 2, f"期望 count=2，實得 {count}"


def test_independent_source_keys_does_not_count_whitespace_only_as_ghost_source():
    """qa-lead LOW-3 回歸：純空白字串 " " 在 `if s` 過濾時是 truthy（非空
    字串），但正規化（`.strip().casefold()`）後會變成 ""——這種「正規化後
    才變空」的情況也要被視為沒有來源，不能被誤判成一個幽靈獨立來源。
    `_independent_source_keys([" ", "CoinDesk"])` 應只得 {"coindesk"}
    （count=1），而不是誤把 " " 正規化後的空字串也算進去變成 2。"""
    sources = [" ", "CoinDesk"]
    keys = _independent_source_keys(sources)
    count = _count_independent_sources(sources)
    assert keys == {"coindesk"}, f"期望 {{'coindesk'}}（純空白不計幽靈來源），實得 {keys}"
    assert count == 1, f"期望 count=1，實得 {count}"


# ─── 2. aggregate_trust_by_kind 不變量 ─────────────────────────────

def test_aggregate_trust_by_kind_same_source_variants_yields_single_source():
    """同一 kind 下兩筆 Evidence 的 source 為大小寫/空白變體時，
    n_sources 應為 1、single_source 應為 True。"""
    evs = [
        _ev(source="CoinDesk", kind="news", trust=0.8),
        _ev(source=" coindesk ", kind="news", trust=0.7),
    ]
    result = aggregate_trust_by_kind(evs)
    assert "news" in result, f"期望 kind 'news' 存在，實得 keys: {list(result.keys())}"
    news_entry = result["news"]
    assert news_entry["n_sources"] == 1, (
        f"期望 n_sources=1（同源大小寫變體），實得 {news_entry['n_sources']}"
    )
    assert news_entry["single_source"] is True, (
        f"期望 single_source=True（同源大小寫變體），實得 {news_entry['single_source']}"
    )


# ─── 3. detect_cross_source_signal 不變量 ──────────────────────────

def test_detect_cross_source_signal_none_when_same_source_case_variants():
    """objective + sentiment 兩筆 ScoredClaim 的 source 為同一來源的大小寫變體，
    去重後僅 1 個獨立來源（未達 2 的門檻），detect_cross_source_signal 應回傳 None。"""
    scored = [
        _sc(id_="c1", kind="price", source="Binance", direction="bullish", trust=0.7),
        _sc(id_="c2", kind="social", source=" binance ", direction="bearish", trust=0.6),
    ]
    result = detect_cross_source_signal(scored)
    assert result is None, (
        f"期望回傳 None（僅 1 個獨立來源，未達門檻），實得 {result}"
    )


# ─── 4. n_indep／abstain 不變量（端到端）──────────────────────────

def test_build_report_abstain_when_same_source_variants_do_not_inflate_n_indep():
    """兩份不同 Document 產生高信任 supporting claim，但 source 為同一來源的
    大小寫/空白變體。去重後僅 1 個獨立來源，report 應維持 abstain、direction 為「不明」，
    證明同源大小寫變體灌水不會虛增 n_indep。"""
    doc1 = _doc(
        id_="d1",
        kind="news",
        source="exch-a",
        text="BTC 站穩 關鍵 支撐位 反彈 上漲。",
    )
    doc2 = _doc(
        id_="d2",
        kind="news",
        source=" Exch-A ",
        text="BTC 站穩 關鍵 支撐位 反彈 上漲。",
    )
    brief = _aggregate_from_docs([doc1, doc2])

    # 同源大小寫/空白變體（`exch-a` vs ` Exch-A `）經 canonical source 正規化
    # 收斂為同一來源：不自相佐證（corr 不灌水），因此 trust 不會被虛抬。
    # 去重後整池只有 1 個獨立來源，應維持 abstain。
    all_sources = [sc.claim.doc.source for sc in brief.supporting + brief.contrarian]
    indep_count = _count_independent_sources(all_sources)
    assert indep_count == 1, (
        f"期望 1 個獨立來源（同源大小寫/空白變體），實得 {indep_count}"
    )

    report, _evidence = _run_report(brief)

    assert report.decision_state == "abstain", (
        f"期望 decision_state='abstain'（同源不應虛增 n_indep），"
        f"實得 '{report.decision_state}'"
    )
    assert report.direction == "不明", (
        f"期望 direction='不明'（abstain 時應為不明），實得 '{report.direction}'"
    )


# ─── 5. 對照組：真正不同來源仍正常運作 ─────────────────────────────

def test_count_independent_sources_two_for_truly_distinct_sources():
    """對照組：兩份文件使用真正不同的來源（'exch-a' 和 'sec-gov'），
    去重後應為 2 個獨立來源，不應因為去重函式而被錯誤合併為同源。"""
    doc1 = _doc(
        id_="d1",
        kind="news",
        source="exch-a",
        text="BTC 站穩 關鍵 支撐位 反彈 上漲。",
    )
    doc2 = _doc(
        id_="d2",
        kind="news",
        source="sec-gov",
        text="BTC 站穩 關鍵 支撐位 反彈 上漲。",
    )
    brief = _aggregate_from_docs([doc1, doc2])

    # 前提檢查
    assert len(brief.supporting) == 2, (
        f"期望 2 筆 supporting claim（前提檢查），實得 {len(brief.supporting)}"
    )

    indep_count = _count_independent_sources(sc.claim.doc.source for sc in brief.supporting)
    assert indep_count == 2, (
        f"期望 2 個獨立來源（真正不同來源），實得 {indep_count}"
    )
