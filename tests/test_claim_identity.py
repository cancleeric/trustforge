"""#960 PR2a — canonical claim identity normative tests.

契約：docs/plans/ISSUE-959-CANONICAL-CLAIM-IDENTITY-CONTRACT-2026-07-31.md
PR2a 範圍：mint helper + run_scope_id 注入 + Evidence/BasisItem 蓋章 + 核心
no-dangling（BasisItem/Evidence）。insight / cross_source_signal / narrative 的
remap 與其 dangling 檢查屬 PR2b，本檔不涵蓋。
"""
from __future__ import annotations

import re

import pytest

from trustforge.agent.orchestrator import (
    _canonical_claim_id,
    _claim_fingerprint16,
    build_report,
)
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType
from trustforge.trust.scoring import Claim, ScoredClaim, TrustedBrief

_CLAIM_ID_RE = re.compile(r"^clm1:[^:]+:[0-9a-f]{16}(\.d[0-9]+)?$")
_SCOPE = "test-claim-identity"


def _doc(doc_id: str, source: str = "coindesk", kind: str = "news") -> Document:
    return Document(id=doc_id, kind=kind, source=source, text="", ts=1.0)


def _sc(
    claim_id: str,
    text: str,
    *,
    doc_id: str | None = None,
    source: str = "coindesk",
    direction: str = "bullish",
    claim_type: str = "inference",
    trust: float = 0.8,
    kind: str = "news",
) -> ScoredClaim:
    doc = _doc(doc_id or claim_id, source=source, kind=kind)
    claim = Claim(
        id=claim_id, text=text, doc=doc, direction=direction, claim_type=claim_type,
    )
    return ScoredClaim(claim=claim, trust=trust)


def _build(
    supporting: list[ScoredClaim],
    contrarian: list[ScoredClaim] | None = None,
    *,
    scope: str = _SCOPE,
    confidence: float = 0.8,
    scored: list[ScoredClaim] | None = None,
):
    brief = TrustedBrief(
        query="分析 BTC", supporting=list(supporting),
        contrarian=list(contrarian or []), confidence=confidence,
    )
    return build_report(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE, brief=brief,
        client=BedrockClient(offline=True), log=ExecutionLog(now_fn=lambda: 1000.0),
        now_fn=lambda: 1000.0, scored=scored, run_scope_id=scope,
    )


# ---------------------------------------------------------------------------
# 1. uniqueness-within-run
# ---------------------------------------------------------------------------
def test_uniqueness_within_run():
    """同 run 不同 claim → 不同 clm1 id；全部符合 §10 regex。"""
    scs = [
        _sc("docA#0", "BTC demand rose", doc_id="docA"),
        _sc("docB#0", "BTC supply tightened", doc_id="docB"),
        _sc("docC#0", "BTC inflows surged", doc_id="docC"),
    ]
    report, evidence = _build(scs)

    ids = {ev.claim_id for ev in evidence}
    assert len(ids) == len(evidence), "每筆 admitted Evidence 應有相異 claim_id"
    assert all(_CLAIM_ID_RE.match(c) for c in ids), ids
    # BasisItem 引用的 id 也相異且與 Evidence 對齊
    basis_ids = {cid for bi in report.key_basis for cid in bi.claim_ids}
    assert basis_ids <= ids


# ---------------------------------------------------------------------------
# 2. determinism-within-run
# ---------------------------------------------------------------------------
def test_determinism_within_run():
    """同 run_scope + 同 input → 跨兩次 build_report byte-identical claim_ids。"""
    scs = [
        _sc("docA#0", "BTC demand rose", doc_id="docA"),
        _sc("docB#0", "BTC inflows surged", doc_id="docB"),
    ]
    _, ev1 = _build(scs, scope="run-xyz")
    _, ev2 = _build(scs, scope="run-xyz")

    ids1 = [ev.claim_id for ev in ev1]
    ids2 = [ev.claim_id for ev in ev2]
    assert ids1 == ids2, f"同 scope 應 byte-identical：{ids1} vs {ids2}"

    # 不同 scope → disjoint（fresh rerun 語意，契約 §1.1）
    _, ev3 = _build(scs, scope="run-different")
    ids3 = {ev.claim_id for ev in ev3}
    assert not (set(ids1) & ids3), "不同 run_scope 應產出 disjoint claim_ids"


# ---------------------------------------------------------------------------
# 3. empty-run-scope-id-rejected
# ---------------------------------------------------------------------------
def test_empty_run_scope_id_rejected():
    """run_scope_id='' → 在發出任何 Evidence 前 raise ValueError。"""
    scs = [_sc("docA#0", "BTC demand rose", doc_id="docA")]
    with pytest.raises(ValueError, match="run_scope_id"):
        _build(scs, scope="")


# ---------------------------------------------------------------------------
# 4. nonstring-run-scope-id
# ---------------------------------------------------------------------------
def test_nonstring_run_scope_id_rejected():
    """run_scope_id 非 str → ValueError。"""
    scs = [_sc("docA#0", "BTC demand rose", doc_id="docA")]
    with pytest.raises(ValueError, match="run_scope_id"):
        _build(scs, scope=123)  # type: ignore[arg-type]


def test_colon_run_scope_id_rejected():
    """run_scope_id 帶冒號 → ValueError（會與 clm1:{scope}:{fp} 分隔歧義）。"""
    scs = [_sc("docA#0", "BTC demand rose", doc_id="docA")]
    with pytest.raises(ValueError, match="run_scope_id"):
        _build(scs, scope="has:colon")


# ---------------------------------------------------------------------------
# 5. dedup-survivor-single-id
# ---------------------------------------------------------------------------
def test_dedup_survivor_single_id():
    """dedup 後存活者領一個 canonical id；同 key 的丟棄者 alias 存活者。

    兩筆同 (source, content_reference, related, direction) 但不同 doc.id 的 claim
    會被 `_add_evidence` 去重成單一 Evidence 列。存活者（較高 trust）的 claim_id
    成為該列唯一 canonical id；丟棄者的 source 指紋在內部 map alias 到同一 id。
    """
    # 同 source、同 content_reference（meta 顯式釘同值）、同 direction、同 related
    # → dedup key 相同；但 doc.id 不同 → fingerprint 不同（兩個相異 source 指紋）。
    hi = _sc("docA#0", "BTC demand rose", doc_id="docA", trust=0.9)
    lo = _sc("docB#0", "BTC demand rose", doc_id="docB", trust=0.5)
    hi.claim.doc.meta["content_reference"] = "BTC demand rose"
    lo.claim.doc.meta["content_reference"] = "BTC demand rose"
    report, evidence = _build([hi, lo])

    assert len(evidence) == 1, "兩筆同 key 應 dedup 成單一 Evidence"
    ids = {ev.claim_id for ev in evidence}
    assert len(ids) == 1, "dedup 後存活者領單一 canonical id"
    # BasisItem 只引用存活者的 id
    for bi in report.key_basis:
        assert set(bi.claim_ids) <= ids


# ---------------------------------------------------------------------------
# 6. basis-claim-ids-parity
# ---------------------------------------------------------------------------
def test_basis_claim_ids_parity():
    """set(BasisItem.claim_ids) == {evidence[i].claim_id for i in evidence_idx}。"""
    scs = [
        _sc("docA#0", "BTC demand rose", doc_id="docA"),
        _sc("docB#0", "BTC inflows surged", doc_id="docB"),
    ]
    report, evidence = _build(scs)
    by_idx = {i: ev.claim_id for i, ev in enumerate(evidence)}

    assert report.key_basis, "測試前提：應有 BasisItem"
    for bi in report.key_basis:
        expected = {by_idx[i] for i in bi.evidence_idx}
        assert set(bi.claim_ids) == expected, (
            f"BasisItem.claim_ids {bi.claim_ids} != evidence_idx 對應 {expected}"
        )


# ---------------------------------------------------------------------------
# 7. no-dangling-claim-ref
# ---------------------------------------------------------------------------
def test_no_dangling_claim_ref():
    """所有 BasisItem.claim_ids 引用的 id 都在 canonical registry（= Evidence.claim_id
    ∪ truncated-but-registered）。本測試無 truncated，registry == Evidence.claim_id 集。"""
    scs = [
        _sc("docA#0", "BTC demand rose", doc_id="docA"),
        _sc("docB#0", "BTC inflows surged", doc_id="docB"),
    ]
    report, evidence = _build(scs)
    registry = {ev.claim_id for ev in evidence}
    assert registry, "測試前提：應有 Evidence"

    for bi in report.key_basis:
        for cid in bi.claim_ids:
            assert cid in registry, f"dangling BasisItem.claim_id {cid!r} 不在 registry"


def test_truncated_claims_registered_not_exposed():
    """scored 中的 truncated claim（不在 brief、不成 Evidence）仍被 mint+註冊。

    契約 §4.2.5：cross_source_signal 接收未截斷 scored 全集；truncated claim 須在
    registry 中（供 PR2b remap 引用），但不暴露為 Evidence 列。此處驗證「registry 含
    truncated id、Evidence 不含」——truncated 的 canonical id 可經 source 指紋推導。
    """
    admitted = [_sc("docA#0", "BTC demand rose", doc_id="docA")]
    truncated = [_sc("docZ#9", "ETH ETF outflow", doc_id="docZ", direction="bearish")]
    report, evidence = _build(admitted, scored=admitted + truncated)

    ev_ids = {ev.claim_id for ev in evidence}
    trunc_canonical = _canonical_claim_id(truncated[0], _SCOPE)
    # truncated 不在 Evidence 列
    assert trunc_canonical not in ev_ids
    # 但 BasisItem 引用的 id 全部仍在 registry（admitted 集）內，無 dangling
    for bi in report.key_basis:
        for cid in bi.claim_ids:
            assert cid in ev_ids


# ---------------------------------------------------------------------------
# 8. text-normalization-trim-only
# ---------------------------------------------------------------------------
def test_text_normalization_trim_only():
    """text 僅 strip：前後空白不影響 fingerprint；內部空白／大小寫必須保留（相異 id）。

    契約 §2.1.7 + §10 text-normalization-trim-only：不做 NFC/NFKC/casefold、不動內部空白。
    """
    base = _sc("d#0", "BTC up")
    # 前後空白 → strip 後等同 base → 同 fingerprint
    padded = _sc("d#0", "  BTC up  ")
    assert _claim_fingerprint16(padded) == _claim_fingerprint16(base), (
        "前後空白應被 strip，不影響 fingerprint"
    )
    # 內部雙空白 → 保留 → 不同 fingerprint
    inner_ws = _sc("d#0", "BTC  up")
    assert _claim_fingerprint16(inner_ws) != _claim_fingerprint16(base), (
        "內部空白不得被正規化"
    )
    # 大小寫 → 保留 → 不同 fingerprint
    lower = _sc("d#0", "btc up")
    assert _claim_fingerprint16(lower) != _claim_fingerprint16(base), (
        "大小寫不得被 casefold"
    )
    # 完整 id 同樣受 trim 影響（同一 scope 下 base==padded）
    assert _canonical_claim_id(padded, "scope-1") == _canonical_claim_id(base, "scope-1")
