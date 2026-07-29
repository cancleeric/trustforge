"""#862 退件修正：evidence_grouper production 缺陷邊界測試。

覆蓋：
  1. direction 隔離：bullish vs bearish 同 source/kind 不聚合
  2. 單位一致性：同 metric 不同 unit → value_range=None
  3. canonical alias：coindesk.com + coindesk → 同組
  4. key_basis 前 3 條面向多樣性
  5. 全覆蓋不變式在 direction 分桶後仍成立
"""
from __future__ import annotations

import pytest

from trustforge.agent.evidence_grouper import (
    EvidenceGroup,
    _direction_bucket,
    _normalize_source,
    group_evidence,
)
from trustforge.schema import Evidence


# ---------------------------------------------------------------------------
# Fixture 工廠
# ---------------------------------------------------------------------------

def _ev(
    source: str = "coindesk",
    kind: str = "news",
    trust: float = 0.7,
    content_reference: str = "算力: 891 TH/s",
    fetched_at: str = "2025-06-15T00:00:00Z",
    related_claim: str = "BTC 市場判斷",
    trust_components: dict | None = None,
) -> Evidence:
    """快速建立測試用 Evidence。"""
    return Evidence(
        source=source,
        fetched_at=fetched_at,
        content_reference=content_reference,
        related_claim=related_claim,
        source_url="",
        kind=kind,
        trust=trust,
        trust_components=trust_components or {},
    )


# ===========================================================================
# Test: direction 隔離（FR-1）
# ===========================================================================

class TestDirectionIsolation:
    """bullish（supporting）與 bearish（contrarian）Evidence 不聚合為同組。"""

    def test_same_source_kind_different_direction_not_merged(self):
        """同 source、同 kind、同 metric，但方向不同 → 分開成組。"""
        evidence = [
            _ev(source="coindesk", kind="news", trust=0.8,
                content_reference="BTC ETF 資金流入: 500M USD",
                related_claim="BTC 市場判斷",
                fetched_at="2025-06-15T01:00:00Z"),
            _ev(source="coindesk", kind="news", trust=0.6,
                content_reference="BTC ETF 資金流入: 480M USD",
                related_claim="BTC 市場判斷",
                fetched_at="2025-06-15T02:00:00Z"),
            _ev(source="coindesk", kind="news", trust=0.5,
                content_reference="BTC ETF 資金流入: 300M USD",
                related_claim="反方／低信任訊號",
                fetched_at="2025-06-15T03:00:00Z"),
        ]
        groups = group_evidence(evidence)

        # 前兩筆（supporting）可聚合；第三筆（contrarian）必須獨立
        supporting_groups = [g for g in groups if evidence[g.representative_idx].related_claim == "BTC 市場判斷"]
        contrarian_groups = [g for g in groups if evidence[g.representative_idx].related_claim == "反方／低信任訊號"]

        # contrarian 筆一定獨立
        assert any(2 in g.member_indices for g in contrarian_groups)
        # supporting 兩筆應在同一組
        merged = [g for g in supporting_groups if len(g.member_indices) >= 2]
        assert len(merged) >= 1
        assert 0 in merged[0].member_indices
        assert 1 in merged[0].member_indices
        # contrarian 筆不與 supporting 混合
        for g in groups:
            member_directions = {evidence[i].related_claim for i in g.member_indices}
            if len(g.member_indices) >= 2:
                # 多筆群組內方向必須一致
                assert len(member_directions) == 1, (
                    f"群組 {g.member_indices} 混合了不同 direction: {member_directions}"
                )

    def test_direction_bucket_function(self):
        """_direction_bucket 正確分類。"""
        ev_support = _ev(related_claim="BTC 市場判斷")
        ev_contra = _ev(related_claim="反方／低信任訊號")
        ev_other = _ev(related_claim="ETH 市場判斷")

        assert _direction_bucket(ev_support) == "supporting"
        assert _direction_bucket(ev_contra) == "contrarian"
        assert _direction_bucket(ev_other) == "supporting"

    def test_coverage_invariant_with_direction(self):
        """加入 direction 分桶後，全覆蓋不變式仍成立。"""
        evidence = [
            _ev(source="coindesk", kind="news", trust=0.8,
                content_reference="算力: 891 TH/s",
                related_claim="BTC 市場判斷",
                fetched_at="2025-06-15T01:00:00Z"),
            _ev(source="coindesk", kind="news", trust=0.6,
                content_reference="算力: 828 TH/s",
                related_claim="反方／低信任訊號",
                fetched_at="2025-06-15T02:00:00Z"),
            _ev(source="cointelegraph", kind="news", trust=0.5,
                content_reference="Gas Fee: 12.5 Gwei",
                related_claim="BTC 市場判斷",
                fetched_at="2025-06-15T03:00:00Z"),
        ]
        groups = group_evidence(evidence)
        all_indices = set()
        for g in groups:
            all_indices.update(g.member_indices)
        assert all_indices == set(range(len(evidence)))


# ===========================================================================
# Test: 單位一致性（FR-2）
# ===========================================================================

class TestUnitConsistency:
    """同 metric 不同 unit 時不計算 value_range/trend。"""

    def test_different_units_no_value_range(self):
        """同 source/kind/metric 但不同 unit → 聚合成組但 value_range=None。"""
        evidence = [
            _ev(source="glassnode", kind="onchain", trust=0.8,
                content_reference="活躍地址: 900000 個",
                fetched_at="2025-06-15T01:00:00Z"),
            _ev(source="glassnode", kind="onchain", trust=0.7,
                content_reference="活躍地址: 850 K",
                fetched_at="2025-06-15T02:00:00Z"),
        ]
        groups = group_evidence(evidence)
        # 找到含兩筆的群組
        merged = [g for g in groups if len(g.member_indices) == 2]
        assert len(merged) == 1
        # 單位不一致 → value_range 和 trend 應為 None
        g = merged[0]
        assert g.value_range is None
        assert g.trend is None

    def test_same_unit_value_range_computed(self):
        """同 unit 時正常計算 value_range。"""
        evidence = [
            _ev(source="glassnode", kind="onchain", trust=0.8,
                content_reference="算力: 891 TH/s",
                fetched_at="2025-06-15T01:00:00Z"),
            _ev(source="glassnode", kind="onchain", trust=0.7,
                content_reference="算力: 828 TH/s",
                fetched_at="2025-06-14T01:00:00Z"),
        ]
        groups = group_evidence(evidence)
        merged = [g for g in groups if len(g.member_indices) == 2]
        assert len(merged) == 1
        g = merged[0]
        assert g.value_range is not None
        assert "828" in g.value_range
        assert "891" in g.value_range

    def test_unit_mismatch_group_still_formed(self):
        """單位不一致時群組仍成立（member_indices 不受影響）。"""
        evidence = [
            _ev(source="glassnode", kind="onchain", trust=0.8,
                content_reference="費用: 100 USD",
                fetched_at="2025-06-15T01:00:00Z"),
            _ev(source="glassnode", kind="onchain", trust=0.7,
                content_reference="費用: 0.003 BTC",
                fetched_at="2025-06-15T02:00:00Z"),
        ]
        groups = group_evidence(evidence)
        merged = [g for g in groups if len(g.member_indices) == 2]
        assert len(merged) == 1
        # 群組成立但無數值摘要
        g = merged[0]
        assert g.value_range is None
        assert g.latest_value is None


# ===========================================================================
# Test: canonical source alias（FR-3）
# ===========================================================================

class TestCanonicalSourceAlias:
    """來源正規化沿用 canonical_source() alias 規則。"""

    def test_coindesk_com_and_coindesk_merge(self):
        """coindesk.com 與 coindesk 應被視為同一來源 → 同桶可聚合。"""
        evidence = [
            _ev(source="coindesk.com", kind="news", trust=0.8,
                content_reference="算力: 891 TH/s",
                fetched_at="2025-06-15T01:00:00Z"),
            _ev(source="coindesk", kind="news", trust=0.7,
                content_reference="算力: 828 TH/s",
                fetched_at="2025-06-14T01:00:00Z"),
        ]
        groups = group_evidence(evidence)
        merged = [g for g in groups if len(g.member_indices) == 2]
        assert len(merged) == 1

    def test_twitter_and_x_merge(self):
        """twitter 與 x.com 應被視為同一來源，同 metric 可聚合。"""
        evidence = [
            _ev(source="twitter", kind="social", trust=0.6,
                content_reference="BTC sentiment: 75 score",
                fetched_at="2025-06-15T01:00:00Z"),
            _ev(source="x.com", kind="social", trust=0.5,
                content_reference="BTC sentiment: 72 score",
                fetched_at="2025-06-15T02:00:00Z"),
        ]
        groups = group_evidence(evidence)
        merged = [g for g in groups if len(g.member_indices) == 2]
        assert len(merged) == 1

    def test_normalize_source_uses_canonical(self):
        """_normalize_source 使用 canonical_source，不只是 casefold。"""
        assert _normalize_source("coindesk.com") == "coindesk"
        assert _normalize_source("CoinDesk.com") == "coindesk"
        assert _normalize_source("twitter") == "x"
        assert _normalize_source("X.com") == "x"
        assert _normalize_source("sec edgar") == "sec-gov"

    def test_different_sources_stay_separate(self):
        """真正不同的來源不會被錯誤合併。"""
        evidence = [
            _ev(source="coindesk", kind="news", trust=0.8,
                content_reference="算力: 891 TH/s",
                fetched_at="2025-06-15T01:00:00Z"),
            _ev(source="cointelegraph", kind="news", trust=0.7,
                content_reference="算力: 828 TH/s",
                fetched_at="2025-06-14T01:00:00Z"),
        ]
        groups = group_evidence(evidence)
        # 不同來源不應合併為同一群組
        merged = [g for g in groups if len(g.member_indices) == 2]
        assert len(merged) == 0


# ===========================================================================
# Test: key_basis 前 3 條面向多樣性（FR-4）
# ===========================================================================

class TestKeyBasisDiversity:
    """key_basis 前 3 條保證不同 (source, kind) 面向。"""

    def test_top3_different_facets(self):
        """前 3 條 BasisItem 的 (source, kind) 必須互異。"""
        from trustforge.agent.evidence_grouper import group_evidence
        from trustforge.agent.orchestrator import _normalize_source_key
        from trustforge.schema import BasisItem

        # 建立 6 筆 evidence，前 4 筆同 source/kind
        evidence = [
            _ev(source="coindesk", kind="news", trust=0.9, content_reference="BTC ETF 流入 500M"),
            _ev(source="coindesk", kind="news", trust=0.85, content_reference="BTC ETF 流入 480M"),
            _ev(source="coindesk", kind="news", trust=0.82, content_reference="BTC ETF 流入 470M"),
            _ev(source="coindesk", kind="news", trust=0.80, content_reference="BTC ETF 流入 460M"),
            _ev(source="glassnode", kind="onchain", trust=0.75, content_reference="活躍地址上升 12%"),
            _ev(source="ohlcv-csv", kind="price", trust=0.70, content_reference="BTC C=67500 USD"),
        ]

        # 模擬 orchestrator 的 key_basis 建立流程
        key_basis = [
            BasisItem(claim=f"claim_{i}", explanation=f"exp_{i}", evidence_idx=[i])
            for i in range(len(evidence))
        ]

        ev_groups = group_evidence(evidence)

        # 反查 map
        _idx_to_group: dict[int, int] = {}
        for gi, g in enumerate(ev_groups):
            for mi in g.member_indices:
                _idx_to_group[mi] = gi

        # 執行去重邏輯（複製自 orchestrator）
        _seen_groups: set[int] = set()
        _seen_source_kind: set[tuple[str, str]] = set()
        deduped_basis: list[BasisItem] = []
        for bi in key_basis:
            if not bi.evidence_idx:
                deduped_basis.append(bi)
                continue
            primary_idx = bi.evidence_idx[0]
            grp_id = _idx_to_group.get(primary_idx)
            if grp_id is not None and grp_id in _seen_groups:
                continue
            ev_rep = evidence[primary_idx]
            sk_key = (_normalize_source_key(ev_rep.source), ev_rep.kind)
            if sk_key in _seen_source_kind and len(deduped_basis) < 3:
                continue  # 前 3 條強制跳過重複面向
            if grp_id is not None:
                _seen_groups.add(grp_id)
                g = ev_groups[grp_id]
                if len(g.member_indices) >= 2:
                    bi = BasisItem(
                        claim=bi.claim,
                        explanation=bi.explanation,
                        evidence_idx=list(g.member_indices),
                    )
            _seen_source_kind.add(sk_key)
            deduped_basis.append(bi)

        # 驗證前 3 條面向互異
        top3 = deduped_basis[:3]
        top3_facets = set()
        for bi in top3:
            idx = bi.evidence_idx[0] if bi.evidence_idx else 0
            ev = evidence[idx]
            facet = (_normalize_source_key(ev.source), ev.kind)
            top3_facets.add(facet)

        assert len(top3_facets) == len(top3), (
            f"前 {len(top3)} 條 BasisItem 面向不全互異：{top3_facets}"
        )
        # 應包含 news, onchain, price 三種面向
        kinds = {f[1] for f in top3_facets}
        assert "news" in kinds
        assert "onchain" in kinds
        assert "price" in kinds
def test_named_and_missing_unit_do_not_produce_numeric_summary():
    """有單位與空單位混用時不可產生誤導值域。"""
    evidence = [
        _ev(content_reference="price: 68000 USD",
            fetched_at="2025-06-14T01:00:00Z"),
        _ev(content_reference="price: 2.3",
            fetched_at="2025-06-15T01:00:00Z"),
    ]
    group = next(g for g in group_evidence(evidence)
                 if len(g.member_indices) == 2)
    assert group.value_range is None
    assert group.trend is None
    assert group.latest_value is None


def test_explicit_claim_directions_override_presentation_role():
    """相同 related_claim 但相反 Claim.direction 不得聚合。"""
    evidence = [
        _ev(content_reference="算力: 828 TH/s",
            fetched_at="2025-06-14T01:00:00Z",
            related_claim="BTC 市場判斷"),
        _ev(content_reference="算力: 891 TH/s",
            fetched_at="2025-06-15T01:00:00Z",
            related_claim="BTC 市場判斷"),
    ]
    groups = group_evidence(evidence, directions=["bullish", "bearish"])
    assert sorted(len(g.member_indices) for g in groups) == [1, 1]
