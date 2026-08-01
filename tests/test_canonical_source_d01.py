"""D0.1（#72 / #132）：repo-wide canonical source identity + scoring 正規化修復。

確保「獨立來源數」永不因同源大小寫/空白變體或別名（如 `coindesk.com` vs
`coindesk`）而灌水。同來源多 claim → 來源計數不變、不產生虛假獨立佐證。
"""
from __future__ import annotations

from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType
from trustforge.trust.scoring import (
    Claim,
    ScoredClaim,
    _canonical_source,
    _corroboration,
    _evidence_strength,
    aggregate,
    extract_claims,
    score,
)
from trustforge.agent.orchestrator import build_report, _count_independent_sources
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog


def _doc(id_: str, kind: str, source: str, text: str = "", ts: float = 1_000_000.0) -> Document:
    return Document(id=id_, kind=kind, source=source, text=text, ts=ts)


def _sc(id_: str, kind: str, source: str, trust: float, text: str = "claim") -> ScoredClaim:
    return ScoredClaim(
        claim=Claim(id=id_, text=text, doc=_doc(id_, kind, source, text)),
        trust=trust,
    )


# ─── 1. canonical source 收斂（case/whitespace + 別名）───────────────

def test_canonical_source_collapses_case_and_whitespace():
    assert _canonical_source("CoinDesk") == "coindesk"
    assert _canonical_source(" coindesk ") == "coindesk"
    assert _canonical_source("COINDESK") == "coindesk"


def test_canonical_source_resolves_domain_alias():
    assert _canonical_source("coindesk.com") == "coindesk"
    assert _canonical_source("reuters.com") == "reuters"


def test_canonical_source_resolves_platform_rename_alias():
    assert _canonical_source("twitter") == _canonical_source("x.com") == "x"


def test_canonical_source_preserves_distinct_publishers():
    assert _canonical_source("coindesk") != _canonical_source("cointelegraph")
    assert _canonical_source("reuters") != _canonical_source("bloomberg")


def test_canonical_source_handles_falsy_safely():
    assert _canonical_source("") == ""
    assert _canonical_source(None) == ""
    assert _canonical_source("   ") == ""


# ─── 2. _evidence_strength 不因同源變體灌水 ─────────────────────────

def test_evidence_strength_same_source_variants_do_not_inflate_indep():
    """同一來源以大小寫/空白變體出現三次，n_indep 應為 1（不因變體灌成 3），
    evidence_strength 應與「同一來源只出現一次」完全一致——證明無灌水。"""
    supporting_multi = [
        _sc("a", "news", "CoinDesk", 0.8, "BTC 上漲 突破 阻力"),
        _sc("b", "news", " coindesk ", 0.8, "BTC 上漲 突破 阻力"),
        _sc("c", "news", "COINDESK", 0.8, "BTC 上漲 突破 阻力"),
    ]
    supporting_single = [_sc("a", "news", "coindesk", 0.8, "BTC 上漲 突破 阻力")]

    from trustforge.agent.orchestrator import _count_independent_sources
    indep = _count_independent_sources(sc.claim.doc.source for sc in supporting_multi)
    assert indep == 1, f"同源變體應收斂為 1 個獨立來源，實得 {indep}"

    strength_multi = _evidence_strength(supporting_multi, [], 0.8)
    strength_single = _evidence_strength(supporting_single, [], 0.8)
    assert strength_multi == strength_single, (
        f"同源變體不應灌高 evidence_strength：多變體 {strength_multi} 應 == 單筆 {strength_single}"
    )


# ─── 3. _corroboration 不因同源變體虛抬 ────────────────────────────

def test_corroboration_excludes_same_source_variants():
    """target 來自 CoinDesk，兩個候選來自 ` coindesk ` / `COINDESK`（高 overlap、
    同向），修復前會被誤算成 2 個獨立佐證（corr>0），修復後應為 0（同源排除）。"""
    tgt = Claim(
        id="t",
        text="BTC 站穩 關鍵 支撐位 反彈 上漲",
        doc=_doc("t", "news", "CoinDesk", "BTC 站穩 關鍵 支撐位 反彈 上漲"),
    )
    cands = [
        Claim(
            id="c1",
            text="BTC 站穩 關鍵 支撐位 反彈 上漲",
            doc=_doc("c1", "news", " coindesk ", "BTC 站穩 關鍵 支撐位 反彈 上漲"),
        ),
        Claim(
            id="c2",
            text="BTC 站穩 關鍵 支撐位 反彈 上漲",
            doc=_doc("c2", "news", "COINDESK", "BTC 站穩 關鍵 支撐位 反彈 上漲"),
        ),
    ]
    corr = _corroboration(tgt, [tgt, *cands], stance_fn=None)
    assert corr == 0.0, f"同源變體不應計為獨立佐證，corr 應=0，實得 {corr}"


def test_corroboration_counts_truly_distinct_sources():
    """對照組：真正不同來源仍正常計為獨立佐證。"""
    tgt = Claim(
        id="t",
        text="BTC 站穩 關鍵 支撐位 反彈 上漲",
        doc=_doc("t", "news", "coindesk", "BTC 站穩 關鍵 支撐位 反彈 上漲"),
    )
    cands = [
        Claim(
            id="c1",
            text="BTC 站穩 關鍵 支撐位 反彈 上漲",
            doc=_doc("c1", "news", "reuters", "BTC 站穩 關鍵 支撐位 反彈 上漲"),
        ),
        Claim(
            id="c2",
            text="BTC 站穩 關鍵 支撐位 反彈 上漲",
            doc=_doc("c2", "news", "bloomberg", "BTC 站穩 關鍵 支撐位 反彈 上漲"),
        ),
    ]
    corr = _corroboration(tgt, [tgt, *cands], stance_fn=None)
    assert corr > 0.0, f"真正不同來源應計為獨立佐證，corr 應>0，實得 {corr}"


# ─── 4. 端到端：同源變體灌水不讓 abstain 翻 normal ─────────────────

def _run_report(brief):
    return build_report(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE, brief=brief,
        client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: 1_000_000.0),
        now_fn=lambda: 1_000_000.0,
        run_scope_id="test-canonical-source-d01",
    )


def test_end_to_end_same_source_variants_stay_abstain():
    docs = [
        _doc("d1", "news", "CoinDesk", "BTC 站穩 關鍵 支撐位 反彈 上漲。"),
        _doc("d2", "news", " coindesk ", "BTC 站穩 關鍵 支撐位 反彈 上漲。"),
        _doc("d3", "news", "COINDESK", "BTC 站穩 關鍵 支撐位 反彈 上漲。"),
    ]
    brief = aggregate(score(extract_claims(docs), now=1_000_000.0), query="分析 BTC")
    all_sources = [sc.claim.doc.source for sc in brief.supporting + brief.contrarian]
    indep = _count_independent_sources(all_sources)
    assert indep == 1, f"期望 1 個獨立來源（同源變體），實得 {indep}"
    report, _ = _run_report(brief)
    assert report.decision_state == "abstain", (
        f"同源灌水不應翻 normal，期望 abstain，實得 {report.decision_state}"
    )
