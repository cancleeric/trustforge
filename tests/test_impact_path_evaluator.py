"""Tests for ``trustforge.impact_path_evaluator``.

Covers:
    - Multi-hop BFS traversal (2-hop, 3-hop)
    - Evidence binding (supporting / contrarian)
    - ``InsufficientCoverage`` vs ``NoMatchingEvent`` distinction
    - Language honesty contract (grep for forbidden causal words)
    - Max depth truncation
    - Max paths cap
    - Temporal window filtering
    - Confidence filtering
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trustforge.ecolink import (
    DependencyEdge,
    DependencyKind,
    ImpactDirection,
    UpgradeEvent,
    UpgradeEventStatus,
)
from trustforge.impact_path_evaluator import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_PATHS,
    DEFAULT_MIN_CONFIDENCE,
    EvidenceMap,
    Evaluated,
    ImpactPathEvaluator,
    InsufficientCoverage,
    NoMatchingEvent,
    PathBinding,
)
from trustforge.schema import Evidence

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def mk_edge(
    source: str,
    target: str,
    confidence: float = 0.9,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    kind: DependencyKind = DependencyKind.SETTLEMENT,
) -> DependencyEdge:
    return DependencyEdge(
        source_asset_id=source,
        target_asset_id=target,
        kind=kind,
        valid_from=valid_from or utc(2020, 1, 1),
        valid_until=valid_until,
        confidence=confidence,
        official_source_url="https://arbitrum.foundation/ecolink/dependencies",
        observed_at=utc(2025, 1, 1),
    )


def mk_event(
    event_id: str,
    asset_id: str,
    impacted: tuple[str, ...] = (),
    direction: ImpactDirection = ImpactDirection.POSITIVE,
) -> UpgradeEvent:
    return UpgradeEvent(
        event_id=event_id,
        asset_id=asset_id,
        title=f"Event {event_id}",
        scheduled_at=None,
        actual_at=utc(2025, 6, 1),
        status=UpgradeEventStatus.ACTIVATED,
        impact_direction=direction,
        impacted_asset_ids=impacted,
        official_source_url="https://arbitrum.foundation/upgrade/stylus",
        observed_at=utc(2025, 6, 1),
    )


def mk_evidence(
    asset_id: str,
    trust: float = 0.7,
    kind: str = "price",
) -> Evidence:
    return Evidence(
        source="test-source",
        fetched_at="2025-06-01T00:00:00Z",
        content_reference=f"Evidence for {asset_id}",
        related_claim=f"Claim about {asset_id}",
        kind=kind,
        trust=trust,
    )


# ---------------------------------------------------------------------------
# Multi-hop traversal
# ---------------------------------------------------------------------------


class TestMultiHopTraversal:
    """BFS traverses chains: A→B→C (2-hop) and A→B→C→D (3-hop)."""

    def test_two_hop_path_discovered(self) -> None:
        """A→B edge + B→C edge → 2-hop path from A→C."""
        edges = [
            mk_edge("asset:a", "asset:b", confidence=0.9),
            mk_edge("asset:b", "asset:c", confidence=0.8),
        ]
        event = mk_event("ev:two-hop", "asset:a", impacted=("asset:c",))
        evaluator = ImpactPathEvaluator(edges)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, Evaluated)
        assert result.event_id == "ev:two-hop"
        # Should contain both the 1-hop (A→B) and 2-hop (A→B→C) paths.
        two_hop_bindings = [
            binding for binding in result.paths if len(binding.path) == 2
        ]
        assert len(two_hop_bindings) == 1
        path = two_hop_bindings[0].path
        assert path[0].source_asset_id == "asset:a"
        assert path[0].target_asset_id == "asset:b"
        assert path[1].source_asset_id == "asset:b"
        assert path[1].target_asset_id == "asset:c"

    def test_three_hop_path_discovered(self) -> None:
        """A→B→C→D → 3-hop path at max depth."""
        edges = [
            mk_edge("asset:a", "asset:b", confidence=0.9),
            mk_edge("asset:b", "asset:c", confidence=0.85),
            mk_edge("asset:c", "asset:d", confidence=0.8),
        ]
        event = mk_event("ev:three-hop", "asset:a")
        evaluator = ImpactPathEvaluator(edges)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, Evaluated)
        # The 3-hop path (A→B→C→D) should be present.
        three_hop_bindings = [
            binding for binding in result.paths if len(binding.path) == 3
        ]
        assert len(three_hop_bindings) == 1
        path_edges = three_hop_bindings[0].path
        assert path_edges[0].target_asset_id == "asset:b"
        assert path_edges[1].target_asset_id == "asset:c"
        assert path_edges[2].target_asset_id == "asset:d"

    def test_all_paths_collected(self) -> None:
        """When multiple paths exist, all are collected (not just the best)."""
        edges = [
            mk_edge("asset:a", "asset:b", confidence=0.6),
            mk_edge("asset:b", "asset:c", confidence=0.5),
            mk_edge("asset:a", "asset:c", confidence=0.9),  # high-conf direct
        ]
        event = mk_event("ev:all-paths", "asset:a")
        evaluator = ImpactPathEvaluator(edges)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))
        assert isinstance(result, Evaluated)
        # Both the 1-hop (A→C) and 2-hop (A→B→C) should be present.
        path_lengths = {len(b.path) for b in result.paths}
        assert 1 in path_lengths  # direct path
        assert 2 in path_lengths  # 2-hop path


# ---------------------------------------------------------------------------
# Evidence binding
# ---------------------------------------------------------------------------


class TestEvidenceBinding:
    """Paths carry classified supporting/contrarian evidence."""

    def test_supporting_price_evidence_with_positive_direction(self) -> None:
        """Price evidence with trust >= 0.6 is supporting for POSITIVE events."""
        edges = [mk_edge("asset:a", "asset:b")]
        event = mk_event("ev:evidence-pos", "asset:a", direction=ImpactDirection.POSITIVE)
        evidence_map: EvidenceMap = {
            "asset:a": (mk_evidence("asset:a", trust=0.8, kind="price"),),
            "asset:b": (mk_evidence("asset:b", trust=0.9, kind="price"),),
        }
        evaluator = ImpactPathEvaluator(edges, evidence_map)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, Evaluated)
        assert len(result.paths) == 1
        assert len(result.paths[0].supporting) == 2  # both price, high trust, POSITIVE
        assert len(result.paths[0].contrarian) == 0

    def test_price_evidence_contrarian_for_negative_direction(self) -> None:
        """Price evidence is contrarian for NEGATIVE events."""
        edges = [mk_edge("asset:a", "asset:b")]
        event = mk_event("ev:evidence-neg", "asset:a", direction=ImpactDirection.NEGATIVE)
        evidence_map: EvidenceMap = {
            "asset:a": (mk_evidence("asset:a", trust=0.8, kind="price"),),
        }
        evaluator = ImpactPathEvaluator(edges, evidence_map)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, Evaluated)
        assert len(result.paths) == 1
        assert len(result.paths[0].supporting) == 0
        assert len(result.paths[0].contrarian) == 1  # price evidence is contrarian for NEGATIVE

    def test_news_evidence_supports_negative_direction(self) -> None:
        """News evidence supports NEGATIVE events."""
        edges = [mk_edge("asset:a", "asset:b")]
        event = mk_event("ev:news-neg", "asset:a", direction=ImpactDirection.NEGATIVE)
        evidence_map: EvidenceMap = {
            "asset:a": (mk_evidence("asset:a", trust=0.7, kind="news"),),
        }
        evaluator = ImpactPathEvaluator(edges, evidence_map)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, Evaluated)
        assert len(result.paths) == 1
        assert len(result.paths[0].supporting) == 1  # news supports NEGATIVE
        assert len(result.paths[0].contrarian) == 0

    def test_unknown_direction_always_contrarian(self) -> None:
        """UNKNOWN direction → all evidence is contrarian (conservative)."""
        edges = [mk_edge("asset:a", "asset:b")]
        event = mk_event("ev:unknown", "asset:a", direction=ImpactDirection.UNKNOWN)
        evidence_map: EvidenceMap = {
            "asset:a": (mk_evidence("asset:a", trust=0.8, kind="price"),),
        }
        evaluator = ImpactPathEvaluator(edges, evidence_map)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, Evaluated)
        assert len(result.paths) == 1
        assert len(result.paths[0].supporting) == 0
        assert len(result.paths[0].contrarian) == 1

    def test_no_evidence_map_gives_empty_binding(self) -> None:
        """Without evidence_map, both supporting and contrarian are empty."""
        edges = [mk_edge("asset:a", "asset:b")]
        event = mk_event("ev:no-evidence", "asset:a")
        evaluator = ImpactPathEvaluator(edges, evidence_map=None)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, Evaluated)
        assert len(result.paths) == 1
        assert result.paths[0].supporting == ()
        assert result.paths[0].contrarian == ()


# ---------------------------------------------------------------------------
# InsufficientCoverage
# ---------------------------------------------------------------------------


class TestInsufficientCoverage:
    """Event exists in graph but no qualifying paths survive filtering."""

    def test_all_edges_below_min_confidence(self) -> None:
        """Edges exist but all < min_confidence → InsufficientCoverage."""
        edges = [mk_edge("asset:a", "asset:b", confidence=0.2)]  # below 0.4 default
        event = mk_event("ev:low-conf", "asset:a")
        evaluator = ImpactPathEvaluator(edges)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, InsufficientCoverage)
        assert result.event_id == "ev:low-conf"

    def test_edge_outside_temporal_window(self) -> None:
        """Edge valid_until before now → filtered → InsufficientCoverage."""
        edges = [
            mk_edge(
                "asset:a",
                "asset:b",
                confidence=0.9,
                valid_from=utc(2020, 1, 1),
                valid_until=utc(2024, 12, 31),  # expired before now=2025-06-01
            )
        ]
        event = mk_event("ev:expired", "asset:a")
        evaluator = ImpactPathEvaluator(edges)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, InsufficientCoverage)

    def test_edge_valid_from_in_future(self) -> None:
        """Edge valid_from > now → filtered → InsufficientCoverage."""
        edges = [
            mk_edge(
                "asset:a",
                "asset:b",
                confidence=0.9,
                valid_from=utc(2026, 1, 1),  # future relative to now
            )
        ]
        event = mk_event("ev:future", "asset:a")
        evaluator = ImpactPathEvaluator(edges)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, InsufficientCoverage)

    def test_asset_only_appears_as_target(self) -> None:
        """Asset is a target but BFS starts from source → still insufficient."""
        edges = [
            mk_edge("asset:x", "asset:a"),  # a is target, NOT source → no outgoing
        ]
        event = mk_event("ev:target-only", "asset:a")
        evaluator = ImpactPathEvaluator(edges)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        # asset:a exists in graph (as target), but has no outgoing edges
        assert isinstance(result, InsufficientCoverage)


# ---------------------------------------------------------------------------
# NoMatchingEvent
# ---------------------------------------------------------------------------


class TestNoMatchingEvent:
    """Event's asset not found in the graph at all."""

    def test_asset_not_in_any_edge(self) -> None:
        """Event asset_id is absent from all edges."""
        edges = [mk_edge("asset:x", "asset:y")]
        event = mk_event("ev:ghost", "asset:z")
        evaluator = ImpactPathEvaluator(edges)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, NoMatchingEvent)
        assert result.event_id == "ev:ghost"


# ---------------------------------------------------------------------------
# Max depth truncation
# ---------------------------------------------------------------------------


class TestMaxDepthTruncation:
    """Path expansion stops at max_depth (inclusive)."""

    def test_depth_3_paths_stop_at_3(self) -> None:
        """With max_depth=3, a 4-hop chain only yields up to 3-hop paths."""
        edges = [
            mk_edge("asset:a", "asset:b"),
            mk_edge("asset:b", "asset:c"),
            mk_edge("asset:c", "asset:d"),
            mk_edge("asset:d", "asset:e"),  # 4th hop → beyond max_depth=3
        ]
        event = mk_event("ev:deep", "asset:a")
        evaluator = ImpactPathEvaluator(edges, max_depth=3)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, Evaluated)
        # Check no path exceeds 3 edges.
        for binding in result.paths:
            assert len(binding.path) <= 3

    def test_depth_1_allows_only_1_hop(self) -> None:
        """max_depth=1 means only 1-hop paths are collected."""
        edges = [
            mk_edge("asset:a", "asset:b"),
            mk_edge("asset:b", "asset:c"),  # 2-hop → beyond max_depth=1
        ]
        event = mk_event("ev:shallow", "asset:a")
        evaluator = ImpactPathEvaluator(edges, max_depth=1)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, Evaluated)
        assert len(result.paths) == 1
        assert result.paths[0].path[0].target_asset_id == "asset:b"


# ---------------------------------------------------------------------------
# Max paths cap
# ---------------------------------------------------------------------------


class TestMaxPathsCap:
    """BFS halts when max_paths is reached."""

    def test_max_paths_1_yields_shortest_path(self) -> None:
        """With max_paths=1, only one path is collected (BFS → shortest first)."""
        edges = [
            mk_edge("asset:a", "asset:x"),
            mk_edge("asset:a", "asset:y"),
            mk_edge("asset:a", "asset:z"),
        ]
        event = mk_event("ev:fanout", "asset:a")
        evaluator = ImpactPathEvaluator(edges, max_paths=1)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, Evaluated)
        assert len(result.paths) == 1  # shortest (1-hop)
        target = result.paths[0].path[0].target_asset_id
        assert target in ("asset:x", "asset:y", "asset:z")


# ---------------------------------------------------------------------------
# evaluate_all
# ---------------------------------------------------------------------------


class TestEvaluateAll:
    """Batch evaluation returns results in order."""

    def test_batch_returns_all_variants(self) -> None:
        edges = [
            mk_edge("asset:a", "asset:b", confidence=0.9),
            mk_edge("asset:c", "asset:d", confidence=0.2),  # low conf
        ]
        events = [
            mk_event("ev:good", "asset:a"),
            mk_event("ev:low", "asset:c"),
            mk_event("ev:ghost", "asset:z"),
        ]
        evaluator = ImpactPathEvaluator(edges)

        results = evaluator.evaluate_all(events, now=utc(2025, 6, 1))

        assert len(results) == 3
        assert isinstance(results[0], Evaluated)
        assert isinstance(results[1], InsufficientCoverage)
        assert isinstance(results[2], NoMatchingEvent)


# ---------------------------------------------------------------------------
# Language honesty contract
# ---------------------------------------------------------------------------


class TestLanguageHonestyContract:
    """Verify the module docstring and output strings comply with the language
    honesty contract: no causal language (導致/因此/caused/led to/therefore).
    """

    BANNED = ("導致", "因此", "caused", "led to", "therefore")

    def test_module_docstring_free_of_causal_language(self) -> None:
        """The evaluator module docstring must not use causal language."""
        import trustforge.impact_path_evaluator as mod

        doc = mod.__doc__ or ""
        for word in self.BANNED:
            assert word not in doc, (
                f"impact_path_evaluator module docstring contains banned word: {word!r}"
            )

    def test_output_dataclass_docstrings_free_of_causal_language(self) -> None:
        """Evaluated / InsufficientCoverage docstrings must not use causal language."""
        for cls in (Evaluated, InsufficientCoverage):
            doc = cls.__doc__ or ""
            for word in self.BANNED:
                assert word not in doc, (
                    f"{cls.__name__} docstring contains banned word: {word!r}"
                )

    def test_evaluate_output_honesty_grep(self) -> None:
        """Evaluate an event and grep the Evaluated path's edge metadata for
        banned causal words (none should appear in any edge string field)."""
        edges = [mk_edge("asset:a", "asset:b")]
        event = mk_event("ev:honesty", "asset:a")
        evaluator = ImpactPathEvaluator(edges)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))
        assert isinstance(result, Evaluated)

        # Collect all string fields from path edges.
        for binding in result.paths:
            for edge in binding.path:
                text = f"{edge.source_asset_id} {edge.target_asset_id} {edge.kind.value} {edge.official_source_url}"
                for word in self.BANNED:
                    assert word not in text, (
                        f"Edge string field contains banned word: {word!r}"
                    )


# ---------------------------------------------------------------------------
# Edge temporal window
# ---------------------------------------------------------------------------


class TestEdgeTemporalWindow:
    """valid_until=None means active indefinitely."""

    def test_null_valid_until_is_active(self) -> None:
        """valid_until=None means edge never expires."""
        edges = [
            mk_edge("asset:a", "asset:b", valid_from=utc(2020, 1, 1), valid_until=None)
        ]
        event = mk_event("ev:null-until", "asset:a")
        evaluator = ImpactPathEvaluator(edges)

        result = evaluator.evaluate(event, now=utc(2030, 1, 1))  # far future

        assert isinstance(result, Evaluated)

    def test_valid_from_exactly_now_is_active(self) -> None:
        """valid_from == now → edge is active (inclusive)."""
        now = utc(2025, 6, 1)
        edges = [mk_edge("asset:a", "asset:b", valid_from=now)]
        event = mk_event("ev:edge-now", "asset:a")
        evaluator = ImpactPathEvaluator(edges)

        result = evaluator.evaluate(event, now=now)

        assert isinstance(result, Evaluated)

    def test_valid_until_exactly_now_is_inactive(self) -> None:
        """valid_until == now → edge is NOT active (exclusive upper bound)."""
        now = utc(2025, 6, 1)
        edges = [
            mk_edge(
                "asset:a",
                "asset:b",
                valid_from=utc(2020, 1, 1),
                valid_until=now,  # expires exactly at now
            )
        ]
        event = mk_event("ev:expired-now", "asset:a")
        evaluator = ImpactPathEvaluator(edges)

        result = evaluator.evaluate(event, now=now)

        # Edge is inactive because valid_until <= now (exclusive upper bound)
        assert isinstance(result, InsufficientCoverage)


# ---------------------------------------------------------------------------
# Confidence filtering
# ---------------------------------------------------------------------------


class TestConfidenceFiltering:
    """Edges below min_confidence are skipped."""

    def test_edge_at_exact_min_confidence_is_included(self) -> None:
        """confidence == min_confidence → included."""
        edges = [mk_edge("asset:a", "asset:b", confidence=0.4)]
        event = mk_event("ev:exact-min", "asset:a")
        evaluator = ImpactPathEvaluator(edges, min_confidence=0.4)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, Evaluated)

    def test_edge_just_below_min_confidence_is_excluded(self) -> None:
        """confidence < min_confidence → excluded."""
        edges = [mk_edge("asset:a", "asset:b", confidence=0.399)]
        event = mk_event("ev:below-min", "asset:a")
        evaluator = ImpactPathEvaluator(edges, min_confidence=0.4)

        result = evaluator.evaluate(event, now=utc(2025, 6, 1))

        assert isinstance(result, InsufficientCoverage)


# ---------------------------------------------------------------------------
# Path confidence aggregation
# ---------------------------------------------------------------------------


class TestPathConfidence:
    """Geometric mean aggregation works correctly."""

    def test_single_edge_confidence_is_unchanged(self) -> None:
        evaluator = ImpactPathEvaluator([])
        path = (mk_edge("asset:a", "asset:b", confidence=0.85),)
        conf = evaluator._path_confidence(path)
        assert conf == pytest.approx(0.85)

    def test_two_edge_geometric_mean(self) -> None:
        evaluator = ImpactPathEvaluator([])
        path = (
            mk_edge("asset:a", "asset:b", confidence=0.9),
            mk_edge("asset:b", "asset:c", confidence=0.5),
        )
        conf = evaluator._path_confidence(path)
        assert conf == pytest.approx((0.9 * 0.5) ** 0.5)

    def test_empty_path_confidence_is_zero(self) -> None:
        evaluator = ImpactPathEvaluator([])
        assert evaluator._path_confidence(()) == 0.0
