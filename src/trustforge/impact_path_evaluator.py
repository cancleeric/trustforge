"""ImpactPath evaluator with multi-hop BFS traversal and evidence binding.

IMPORTANT -- Language Honesty Contract (MANDATORY):
    This module assembles *correlation paths* between upgrade events and
    dependency edges. Nothing in this module establishes that an upgrade
    event actually had a downstream effect on another asset.

    - Output text MUST only use hedged language such as "可能相關"
      ("may be related").
    - Output text MUST NOT present correlation as causation.
      This module is prohibited from using any phrase that implies
      a causal link between the upgrade event and downstream asset
      states.  All descriptions must remain hedged (correlation,
      association, "may be related").
    - The ``InsufficientCoverage`` variant is explicitly distinct from an
      empty ``Evaluated`` tuple and MUST NOT be conflated with "no impact"
      or "causal disproof".

Design:
    Starting from ``event.asset_id``, a bounded BFS follows each
    ``DependencyEdge`` whose ``confidence >= min_confidence`` and whose
    temporal window (``valid_from <= now <= valid_until``) is active. Each
    discovered path binds supporting and contrarian ``Evidence`` from a
    caller-supplied evidence pool and returns one of three outputs:
    ``Evaluated``, ``InsufficientCoverage``, or ``NoMatchingEvent``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from trustforge.ecolink import DependencyEdge, ImpactDirection, UpgradeEvent
from trustforge.schema import Evidence

# ---------------------------------------------------------------------------
# Output variants
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PathBinding:
    """A single multi-hop path bound with supporting and contrarian evidence."""

    path: tuple[DependencyEdge, ...]
    """Ordered tuple of edges forming the multi-hop path from event.asset_id
    toward the terminal target."""

    supporting: tuple[Evidence, ...]
    """Evidence that aligns with the event's declared impact direction."""

    contrarian: tuple[Evidence, ...]
    """Evidence that contradicts the event's declared impact direction."""


@dataclass(frozen=True)
class Evaluated:
    """A collection of multi-hop paths from the event's asset to downstream
    targets, each bound with supporting and contrarian evidence.

    WARNING: These are *correlation* paths only. Do NOT render this as a
    causal chain. Use "可能相關" / "may be related" language only.
    """

    event_id: str
    paths: tuple[PathBinding, ...]
    """All discovered qualifying paths, each with its evidence binding."""


@dataclass(frozen=True)
class InsufficientCoverage:
    """The event exists and matches known assets, but no qualifying edge
    chain was found (all paths filtered by confidence too low, temporal
    window mismatch, or depth truncated without reaching a terminal).

    This is NOT equivalent to "no impact" or "causal disproof" -- it means
    we do not have enough data to trace a path. Callers should render this
    as "insufficient data to evaluate" / "資料不足以評估", never as a
    causal conclusion.
    """

    event_id: str


@dataclass(frozen=True)
class NoMatchingEvent:
    """The supplied event does not match any known asset in the edge graph
    (i.e., no dependency edge has the event's ``asset_id`` as source or
    target)."""

    event_id: str


# A mapping from asset_id to its associated evidence pool.
EvidenceMap = dict[str, tuple[Evidence, ...]]

# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_PATHS = 20
DEFAULT_MIN_CONFIDENCE = 0.4


class ImpactPathEvaluator:
    """Multi-hop BFS impact path evaluator along ``DependencyEdge`` chains.

    Parameters:
        edges:
            The full set of ``DependencyEdge`` objects to traverse.
        evidence_map:
            Optional mapping of ``asset_id -> (Evidence, ...)``. When
            provided, each ``Evaluated`` path carries classified supporting
            and contrarian evidence. When ``None``, both tuples are empty.
        min_confidence:
            Minimum edge confidence for inclusion (default 0.4).
        max_depth:
            Maximum BFS depth (default 3). Depth is measured in number of
            edges; a depth of 1 means at most one hop.
        max_paths:
            Maximum number of paths to collect before halting BFS (default 20).
    """

    def __init__(
        self,
        edges: Iterable[DependencyEdge],
        evidence_map: EvidenceMap | None = None,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_paths: int = DEFAULT_MAX_PATHS,
    ) -> None:
        self._edges = tuple(edges)
        self._evidence_map: EvidenceMap = evidence_map or {}
        self._min_confidence = min_confidence
        self._max_depth = max_depth
        self._max_paths = max_paths

        # Build adjacency index: source_asset_id -> list of edges.
        self._adj: dict[str, list[DependencyEdge]] = {}
        for edge in self._edges:
            self._adj.setdefault(edge.source_asset_id, []).append(edge)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        event: UpgradeEvent,
        *,
        now: datetime | None = None,
    ) -> Evaluated | InsufficientCoverage | NoMatchingEvent:
        """Evaluate multi-hop impact paths for a single ``UpgradeEvent``.

        Returns:
            ``Evaluated`` if at least one qualifying path is found.
            ``InsufficientCoverage`` if the event's asset exists in the
            edge graph but no path survives filtering.
            ``NoMatchingEvent`` if the event's asset is absent from the
            adjacency graph entirely.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        paths = self._bfs_paths(event.asset_id, now=now)

        if paths:
            return self._build_evaluated(event, paths)

        if event.asset_id in self._adj or any(
            edge.target_asset_id == event.asset_id for edge in self._edges
        ):
            return InsufficientCoverage(event_id=event.event_id)

        return NoMatchingEvent(event_id=event.event_id)

    def evaluate_all(
        self,
        events: Iterable[UpgradeEvent],
        *,
        now: datetime | None = None,
    ) -> tuple[Evaluated | InsufficientCoverage | NoMatchingEvent, ...]:
        """Batch-evaluate multiple events.

        Returns a tuple of results in the same order as the input events.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        return tuple(self.evaluate(event, now=now) for event in events)

    # ------------------------------------------------------------------
    # BFS
    # ------------------------------------------------------------------

    def _bfs_paths(
        self, start_asset: str, *, now: datetime
    ) -> list[tuple[DependencyEdge, ...]]:
        """Bounded BFS from *start_asset* along outgoing edges.

        Returns:
            List of paths (each a tuple of edges). Empty list means no
            qualifying paths were found.
        """
        paths: list[tuple[DependencyEdge, ...]] = []
        # queue entries: (current_asset_id, path_so_far)
        queue: deque[tuple[str, tuple[DependencyEdge, ...]]] = deque()
        queue.append((start_asset, ()))

        while queue and len(paths) < self._max_paths:
            current_asset, path_so_far = queue.popleft()

            if len(path_so_far) >= self._max_depth:
                continue

            for edge in self._adj.get(current_asset, ()):
                if not self._edge_active(edge, now=now):
                    continue
                if edge.confidence < self._min_confidence:
                    continue

                new_path = path_so_far + (edge,)
                paths.append(new_path)

                # If we've reached the path cap, stop expanding.
                if len(paths) >= self._max_paths:
                    break

                queue.append((edge.target_asset_id, new_path))

            # Early exit outer loop if cap reached (covers the case where
            # the inner loop was broken).
            if len(paths) >= self._max_paths:
                break

        return paths

    @staticmethod
    def _edge_active(edge: DependencyEdge, *, now: datetime) -> bool:
        """Check if *edge* is active at *now* based on its temporal window."""
        if edge.valid_from > now:
            return False
        if edge.valid_until is not None and edge.valid_until <= now:
            return False
        return True

    # ------------------------------------------------------------------
    # Evaluation assembly
    # ------------------------------------------------------------------

    def _build_evaluated(
        self, event: UpgradeEvent, paths: list[tuple[DependencyEdge, ...]]
    ) -> Evaluated:
        """Build an ``Evaluated`` with all qualifying paths, each bound with
        supporting and contrarian evidence."""
        bindings: list[PathBinding] = []
        for path in paths:
            supporting, contrarian = self._bind_evidence(event, path)
            bindings.append(
                PathBinding(
                    path=path,
                    supporting=tuple(supporting),
                    contrarian=tuple(contrarian),
                )
            )
        return Evaluated(
            event_id=event.event_id,
            paths=tuple(bindings),
        )

    @staticmethod
    def _path_confidence(path: tuple[DependencyEdge, ...]) -> float:
        """Aggregate confidence across a path.

        Uses the geometric mean: each edge's confidence is an independent
        filter, so multiplying them captures the compounding uncertainty.
        """
        product = 1.0
        for edge in path:
            product *= edge.confidence
        return product ** (1.0 / len(path)) if path else 0.0

    def _bind_evidence(
        self,
        event: UpgradeEvent,
        path: tuple[DependencyEdge, ...],
    ) -> tuple[list[Evidence], list[Evidence]]:
        """Classify evidence from all path nodes as supporting or contrarian
        relative to the event's declared ``impact_direction``.

        Evidence is drawn from ``self._evidence_map`` keyed by asset_id.
        If no evidence map was provided, both lists are empty.
        """
        if not self._evidence_map:
            return [], []

        # Collect unique asset IDs across the entire path.
        asset_ids: set[str] = {event.asset_id}
        for edge in path:
            asset_ids.add(edge.source_asset_id)
            asset_ids.add(edge.target_asset_id)

        supporting: list[Evidence] = []
        contrarian: list[Evidence] = []

        for asset_id in asset_ids:
            for ev in self._evidence_map.get(asset_id, ()):
                if self._evidence_supports(ev, event.impact_direction):
                    supporting.append(ev)
                else:
                    contrarian.append(ev)

        return supporting, contrarian

    @staticmethod
    def _evidence_supports(
        evidence: Evidence, direction: ImpactDirection
    ) -> bool:
        """Heuristic: classify evidence as supporting if its declared
        ``kind`` aligns in a way that is consistent with the event's
        ``impact_direction``. This is deliberately conservative; unknown
        directions default to contrarian to avoid over-claiming.
        """
        # For non-price evidence, we use a simple heuristic:
        #   positive direction → regulatory/onchain/social news align → supporting
        #   negative direction → the same evidence kinds may be contrarian
        # When direction is UNKNOWN or MIXED, we err on the contrarian side.
        if direction == ImpactDirection.UNKNOWN:
            return False

        # Evidence with very low trust is inherently contrarian regardless
        # of kind alignment.
        if evidence.trust < 0.2:
            return False

        # Strong evidence (trust >= 0.6) that is price or onchain data
        # is treated as directionally aligned with the event: if the event
        # is POSITIVE, strong evidence supports; if NEGATIVE, it contrarians.
        # This is a correlation heuristic, NOT a causal claim.
        neutral_kinds = {"price", "onchain"}
        if evidence.kind in neutral_kinds and evidence.trust >= 0.6:
            return direction == ImpactDirection.POSITIVE

        # For all other evidence, we do not claim alignment unless the
        # direction is explicitly negative (i.e., negative news supports a
        # negative event).
        if evidence.kind in {"news", "social"}:
            return direction == ImpactDirection.NEGATIVE

        return False
