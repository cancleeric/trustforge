"""Investigation Mode I1 contract: request/claim/evidence/report data shapes.

Platform-owned pure contract for the Investigation Mode chain (issue #1458,
blocked-by #1457).  It defines serialization-stable dataclasses for the six
shapes named by the issue — ``InvestigationRequest``, ``Claim``, ``Evidence``,
``SourceSnapshot``, ``SourceRelation``, ``InvestigationReport`` — the E0–E4
evidence levels, and the ``unverified`` / ``unresolved`` claim statuses.

Hard rules enforced here (issue acceptance criteria):
- JSON round-trip is deterministic: ``to_dict``/``from_dict`` are inverse and
  the canonical byte encoding is stable across processes.
- A claim with **no evidence** must never serialize as a verified conclusion
  (``corroborated`` requires evidence of level E2 or above; ``verified`` is
  strictly stronger and requires E3/E4).
- E2/E3/E4 semantics are strictly separated from E0/E1 via per-level
  structural requirements on ``Evidence``.

This module is pure: no IO, no network, no clocks.  All timestamps are caller
supplied ISO-8601 UTC strings so fixtures and reports stay reproducible.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Mapping

INVESTIGATION_SCHEMA = "trustforge.investigation/v1"

# ---------------------------------------------------------------------------
# Bounded canonical JSON (mirrors trustforge.agent.shadow_contracts, but this
# platform module may not import agent — web/agent both consume investigation
# contracts, so the encoder stays self-contained here).
# ---------------------------------------------------------------------------

_MAX_TEXT = 4096
_MAX_DEPTH = 24
_MAX_NODES = 50_000
_MAX_INTEGER = 2**63 - 1
_MAX_COLLECTION_ITEMS = 10_000

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class InvestigationContractError(ValueError):
    """An investigation contract input violates the shared schema."""


def _validate_canonical_json(value: Any, *, seen: set[int], nodes: list[int], depth: int) -> None:
    nodes[0] += 1
    if nodes[0] > _MAX_NODES or depth > _MAX_DEPTH:
        raise InvestigationContractError("canonical JSON exceeds structural limits")
    if isinstance(value, str):
        if len(value.encode()) > _MAX_TEXT:
            raise InvestigationContractError("canonical JSON string exceeds size limit")
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, int):
        if abs(value) > _MAX_INTEGER:
            raise InvestigationContractError("canonical JSON integer exceeds range")
    elif isinstance(value, float):
        if not (value == value and abs(value) != float("inf")):
            raise InvestigationContractError("canonical JSON number must be finite")
    elif isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            raise InvestigationContractError("canonical JSON cycle detected")
        seen.add(marker)
        if len(value) > _MAX_COLLECTION_ITEMS or any(not isinstance(k, str) for k in value):
            raise InvestigationContractError("canonical JSON object is invalid or oversized")
        for key, item in value.items():
            _validate_canonical_json(key, seen=seen, nodes=nodes, depth=depth + 1)
            _validate_canonical_json(item, seen=seen, nodes=nodes, depth=depth + 1)
        seen.remove(marker)
    elif isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in seen:
            raise InvestigationContractError("canonical JSON cycle detected")
        seen.add(marker)
        if len(value) > _MAX_COLLECTION_ITEMS:
            raise InvestigationContractError("canonical JSON collection exceeds size limit")
        for item in value:
            _validate_canonical_json(item, seen=seen, nodes=nodes, depth=depth + 1)
        seen.remove(marker)
    else:
        raise InvestigationContractError("value is not canonical JSON")


def canonical_json_bytes(value: Any) -> bytes:
    """Deterministic bounded JSON encoding (sorted keys, tight separators)."""
    _validate_canonical_json(value, seen=set(), nodes=[0], depth=0)
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvestigationContractError(f"canonical JSON encode failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Field vocabularies
# ---------------------------------------------------------------------------

# Evidence levels E0–E4.  E0/E1 are *unsourced or single-sourced* statements;
# E2+ require structured multi-source or primary-source verification.  The
# per-level structural requirements live on ``Evidence.validate``.
EVIDENCE_LEVELS: tuple[str, ...] = ("E0", "E1", "E2", "E3", "E4")
EVIDENCE_LEVEL_DESCRIPTIONS: dict[str, str] = {
    "E0": "assertion with no captured source attached",
    "E1": "single captured snapshot; not independently checked",
    "E2": "two or more independent snapshots corroborate the claim",
    "E3": "verified against a primary/authoritative source snapshot",
    "E4": "independently reproduced from raw inputs (reproducible artifact)",
}

# Claim statuses.  ``unverified`` = no usable evidence yet; ``unresolved`` =
# evidence exists but sources conflict (contradiction not settled).
CLAIM_STATUSES: tuple[str, ...] = (
    "unverified",
    "corroborated",
    "verified",
    "refuted",
    "unresolved",
)

SNAPSHOT_KINDS: tuple[str, ...] = ("primary", "secondary", "tertiary")

SOURCE_RELATIONS: tuple[str, ...] = (
    "corroborates",
    "contradicts",
    "derives_from",
    "same_source_group",
)
SOURCE_INDEPENDENCE: tuple[str, ...] = ("independent", "same_entity", "unknown")

_REQUEST_ID = "request_id"
_CLAIM_ID = "claim_id"
_EVIDENCE_ID = "evidence_id"
_SNAPSHOT_ID = "snapshot_id"
_RELATION_ID = "relation_id"
_REPORT_ID = "report_id"


def _check_id(name: str, value: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise InvestigationContractError(f"{name} must be a 1-128 char id, got {value!r}")
    return value


def _check_iso(name: str, value: str) -> str:
    if not isinstance(value, str) or not re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z", value):
        raise InvestigationContractError(f"{name} must be an ISO-8601 UTC string, got {value!r}")
    # Review P2 (#1458 round 7): the regex alone accepts impossible dates like
    # 2026-99-99T99:99:99Z — validate the actual calendar values so downstream
    # consumers can parse or sort without a late failure.
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise InvestigationContractError(
            f"{name} must be a valid UTC timestamp, got {value!r}"
        ) from exc
    return value


def _check_choice(name: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise InvestigationContractError(f"{name} must be one of {allowed}, got {value!r}")
    return value


def _strict_fields(cls_name: str, data: Mapping[str, Any], expected: set[str]) -> None:
    unknown = set(data) - expected
    if unknown:
        raise InvestigationContractError(f"{cls_name}: unknown fields {sorted(unknown)}")


def _require_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise InvestigationContractError(f"{name} must be a non-empty string")
    if len(value.encode()) > _MAX_TEXT:
        raise InvestigationContractError(f"{name} exceeds {_MAX_TEXT} bytes")
    return value


def _check_note(name: str, value: Any) -> None:
    if not isinstance(value, str) or len(value.encode()) > _MAX_TEXT:
        raise InvestigationContractError(f"{name} must be a string <= {_MAX_TEXT} bytes")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InvestigationRequest:
    """What is being investigated and why."""

    request_id: str
    subject: str
    question: str
    created_at: str
    scope_tags: tuple[str, ...] = ()

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"request_id", "subject", "question", "created_at", "scope_tags"}
    )

    def __post_init__(self) -> None:
        _check_id(_REQUEST_ID, self.request_id)
        _require_str("subject", self.subject)
        _require_str("question", self.question)
        _check_iso("created_at", self.created_at)
        # Review P2 (#1458 round 8): a bare string would silently split into
        # per-character tags — require a real sequence of bounded strings.
        if isinstance(self.scope_tags, (str, bytes)) or not isinstance(
            self.scope_tags, (list, tuple)
        ):
            raise InvestigationContractError(
                "scope_tags must be a sequence of strings, "
                f"got {type(self.scope_tags).__name__}"
            )
        for tag in self.scope_tags:
            if not isinstance(tag, str) or not tag or len(tag.encode()) > _MAX_TEXT:
                raise InvestigationContractError(
                    f"scope_tags entries must be non-empty strings <= {_MAX_TEXT} bytes"
                )
        object.__setattr__(self, "scope_tags", tuple(self.scope_tags))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scope_tags"] = list(self.scope_tags)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InvestigationRequest":
        _strict_fields(cls.__name__, data, set(cls.FIELDS))
        try:
            return cls(
                request_id=data["request_id"],
                subject=data["subject"],
                question=data["question"],
                created_at=data["created_at"],
                scope_tags=tuple(data.get("scope_tags", ())),
            )
        except KeyError as exc:
            raise InvestigationContractError(f"InvestigationRequest missing field {exc}") from exc


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """An immutable capture of one source at one point in time."""

    snapshot_id: str
    source_id: str
    uri: str
    captured_at: str
    content_hash: str
    kind: str = "secondary"
    excerpt: str = ""

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"snapshot_id", "source_id", "uri", "captured_at", "content_hash", "kind", "excerpt"}
    )

    def __post_init__(self) -> None:
        _check_id(_SNAPSHOT_ID, self.snapshot_id)
        _check_id("source_id", self.source_id)
        _require_str("uri", self.uri)
        _check_iso("captured_at", self.captured_at)
        if not isinstance(self.content_hash, str) or not _SHA256.match(self.content_hash):
            raise InvestigationContractError(
                f"content_hash must match sha256:<64 hex>, got {self.content_hash!r}"
            )
        _check_choice("kind", self.kind, SNAPSHOT_KINDS)
        # Review P2 (#1458): cap free-text fields at construction so no
        # report can exist that fails its promised canonical serialization.
        if not isinstance(self.excerpt, str) or len(self.excerpt.encode()) > _MAX_TEXT:
            raise InvestigationContractError(
                f"excerpt must be a string <= {_MAX_TEXT} bytes"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceSnapshot":
        _strict_fields(cls.__name__, data, set(cls.FIELDS))
        try:
            return cls(
                snapshot_id=data["snapshot_id"],
                source_id=data["source_id"],
                uri=data["uri"],
                captured_at=data["captured_at"],
                content_hash=data["content_hash"],
                kind=data.get("kind", "secondary"),
                excerpt=data.get("excerpt", ""),
            )
        except KeyError as exc:
            raise InvestigationContractError(f"SourceSnapshot missing field {exc}") from exc


@dataclass(frozen=True, slots=True)
class SourceRelation:
    """How two snapshots relate: corroboration, contradiction, lineage, grouping."""

    relation_id: str
    from_snapshot: str
    to_snapshot: str
    relation: str
    independence: str = "unknown"
    note: str = ""

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"relation_id", "from_snapshot", "to_snapshot", "relation", "independence", "note"}
    )

    def __post_init__(self) -> None:
        _check_id(_RELATION_ID, self.relation_id)
        _check_id("from_snapshot", self.from_snapshot)
        _check_id("to_snapshot", self.to_snapshot)
        _check_choice("relation", self.relation, SOURCE_RELATIONS)
        _check_choice("independence", self.independence, SOURCE_INDEPENDENCE)
        _check_note("note", self.note)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceRelation":
        _strict_fields(cls.__name__, data, set(cls.FIELDS))
        try:
            return cls(
                relation_id=data["relation_id"],
                from_snapshot=data["from_snapshot"],
                to_snapshot=data["to_snapshot"],
                relation=data["relation"],
                independence=data.get("independence", "unknown"),
                note=data.get("note", ""),
            )
        except KeyError as exc:
            raise InvestigationContractError(f"SourceRelation missing field {exc}") from exc


@dataclass(frozen=True, slots=True)
class Evidence:
    """One evidence item backing a claim, at exactly one E-level.

    Per-level structural requirements (keeps E2/E3/E4 strictly separated
    from E0/E1):
    - E0: no snapshot required.
    - E1: at least one snapshot.
    - E2: at least two snapshots AND declared ``independence="independent"``.
    - E3: at least one snapshot of kind ``primary``.
    - E4: ``reproduced_from`` artifact digest required, plus >=1 snapshot.
    """

    evidence_id: str
    claim_id: str
    level: str
    snapshot_ids: tuple[str, ...] = ()
    independence: str = "unknown"
    reproduced_from: str = ""
    note: str = ""

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "evidence_id",
            "claim_id",
            "level",
            "snapshot_ids",
            "independence",
            "reproduced_from",
            "note",
        }
    )

    def __post_init__(self) -> None:
        _check_id(_EVIDENCE_ID, self.evidence_id)
        _check_id(_CLAIM_ID, self.claim_id)
        _check_choice("level", self.level, EVIDENCE_LEVELS)
        _check_choice("independence", self.independence, SOURCE_INDEPENDENCE)
        _check_note("note", self.note)
        if not isinstance(self.reproduced_from, str) or (
            self.reproduced_from and not _SHA256.match(self.reproduced_from)
        ):
            raise InvestigationContractError(
                f"reproduced_from must be empty or match sha256:<64 hex>, "
                f"got {self.reproduced_from!r}"
            )
        object.__setattr__(self, "snapshot_ids", tuple(str(s) for s in self.snapshot_ids))
        if self.level == "E0" and self.snapshot_ids:
            raise InvestigationContractError(
                f"E0 evidence {self.evidence_id} must not carry snapshots "
                "(E0 means no captured source); use E1 or above"
            )
        if self.level in {"E1", "E3", "E4"} and not self.snapshot_ids:
            raise InvestigationContractError(
                f"{self.level} evidence {self.evidence_id} requires at least one snapshot"
            )
        if self.level == "E2":
            if len(self.snapshot_ids) < 2:
                raise InvestigationContractError(
                    f"E2 evidence {self.evidence_id} requires >=2 independent snapshots"
                )
            if self.independence != "independent":
                raise InvestigationContractError(
                    f"E2 evidence {self.evidence_id} requires independence='independent'"
                )
        if self.level == "E4":
            if not isinstance(self.reproduced_from, str) or not _SHA256.match(self.reproduced_from):
                raise InvestigationContractError(
                    f"E4 evidence {self.evidence_id} requires reproduced_from sha256 digest"
                )
            # Review P2 (#1458 round 8): E4 must document the reproduction
            # method — a bare digest pair proves nothing about independent
            # reproduction.  (The residual trust boundary: artifact contents
            # live outside this serialization contract; consumers verify the
            # digest against the actual artifact bundle.)
            if not self.note.strip():
                raise InvestigationContractError(
                    f"E4 evidence {self.evidence_id} requires a note documenting "
                    "the reproduction method"
                )

    def validate_against_snapshots(self, snapshots: Mapping[str, SourceSnapshot]) -> None:
        """Resolve snapshot references and enforce E3's primary-source rule."""
        for sid in self.snapshot_ids:
            if sid not in snapshots:
                raise InvestigationContractError(
                    f"{self.level} evidence {self.evidence_id} references unknown snapshot {sid!r}"
                )
        if self.level == "E3" and not any(
            snapshots[sid].kind == "primary" for sid in self.snapshot_ids
        ):
            raise InvestigationContractError(
                f"E3 evidence {self.evidence_id} requires a snapshot of kind 'primary'"
            )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["snapshot_ids"] = list(self.snapshot_ids)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        _strict_fields(cls.__name__, data, set(cls.FIELDS))
        try:
            return cls(
                evidence_id=data["evidence_id"],
                claim_id=data["claim_id"],
                level=data["level"],
                snapshot_ids=tuple(data.get("snapshot_ids", ())),
                independence=data.get("independence", "unknown"),
                reproduced_from=data.get("reproduced_from", ""),
                note=data.get("note", ""),
            )
        except KeyError as exc:
            raise InvestigationContractError(f"Evidence missing field {exc}") from exc


@dataclass(frozen=True, slots=True)
class Claim:
    """A single atomic statement under investigation.

    ``status`` defaults to ``unverified``.  ``unresolved`` marks conflicting
    evidence; ``corroborated``/``verified`` are only serializable inside a
    report when backed by evidence (enforced by ``InvestigationReport``).
    """

    claim_id: str
    investigation_id: str
    text: str
    status: str = "unverified"
    evidence_ids: tuple[str, ...] = ()

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"claim_id", "investigation_id", "text", "status", "evidence_ids"}
    )

    def __post_init__(self) -> None:
        _check_id(_CLAIM_ID, self.claim_id)
        _check_id("investigation_id", self.investigation_id)
        _require_str("text", self.text)
        _check_choice("status", self.status, CLAIM_STATUSES)
        object.__setattr__(self, "evidence_ids", tuple(str(e) for e in self.evidence_ids))

    def to_dict(self) -> dict[str, Any]:
        # Review P2 (#1458 round 4/6): a standalone claim has no report
        # context, so it can never prove a settled conclusion — refuse to
        # emit one without linked evidence ids.  (Evidence-level thresholds
        # are still enforced report-wide by InvestigationReport.)
        if self.status in {"corroborated", "verified", "refuted"} and not self.evidence_ids:
            raise InvestigationContractError(
                f"claim {self.claim_id} status {self.status!r} has no linked evidence "
                "and cannot be serialized as a settled conclusion"
            )
        data = asdict(self)
        data["evidence_ids"] = list(self.evidence_ids)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Claim":
        _strict_fields(cls.__name__, data, set(cls.FIELDS))
        try:
            return cls(
                claim_id=data["claim_id"],
                investigation_id=data["investigation_id"],
                text=data["text"],
                status=data.get("status", "unverified"),
                evidence_ids=tuple(data.get("evidence_ids", ())),
            )
        except KeyError as exc:
            raise InvestigationContractError(f"Claim missing field {exc}") from exc


@dataclass(frozen=True, slots=True)
class InvestigationReport:
    """Assembled container: request + claims + evidence + snapshots + relations.

    Serialization guarantees (issue acceptance criteria):
    - deterministic round-trip: ``from_dict(r.to_dict()) == r`` and stable
      canonical bytes;
    - a claim with no evidence IDs can never serialize with status
      ``corroborated`` or ``verified``;
    - status thresholds are strictly separated: ``corroborated`` needs
      evidence of level E2+ (independent multi-source agreement), while
      ``verified`` needs E3/E4 (primary-source confirmation or independent
      reproduction) — single-source E0/E1 evidence backs neither.
    """

    report_id: str
    investigation_id: str
    request: InvestigationRequest
    claims: tuple[Claim, ...] = ()
    evidences: tuple[Evidence, ...] = ()
    snapshots: tuple[SourceSnapshot, ...] = ()
    relations: tuple[SourceRelation, ...] = ()
    artifact_digests: tuple[str, ...] = ()
    generated_at: str = "1970-01-01T00:00:00Z"

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "report_id",
            "investigation_id",
            "request",
            "claims",
            "evidences",
            "snapshots",
            "relations",
            "artifact_digests",
            "generated_at",
        }
    )

    def __post_init__(self) -> None:
        _check_id(_REPORT_ID, self.report_id)
        _check_id("investigation_id", self.investigation_id)
        _check_iso("generated_at", self.generated_at)
        for name in ("claims", "evidences", "snapshots", "relations", "artifact_digests"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for digest in self.artifact_digests:
            if not isinstance(digest, str) or not _SHA256.match(digest):
                raise InvestigationContractError(
                    f"artifact_digests entries must match sha256:<64 hex>, got {digest!r}"
                )
        if len(set(self.artifact_digests)) != len(self.artifact_digests):
            raise InvestigationContractError("artifact_digests contains duplicates")
        # Review P2 (#1458 round 6): an E4 reproduction digest must resolve to
        # an artifact actually bundled with the report — a bare digest string
        # does not establish independent reproduction.
        for ev in self.evidences:
            if ev.level == "E4" and ev.reproduced_from not in self.artifact_digests:
                raise InvestigationContractError(
                    f"E4 evidence {ev.evidence_id} reproduction digest "
                    f"{ev.reproduced_from!r} is not in the report artifact set"
                )
        # Structural integrity: ids unique, references resolvable.
        self._index_by_id(self.claims, _CLAIM_ID)
        self._index_by_id(self.evidences, _EVIDENCE_ID)
        self._index_by_id(self.snapshots, _SNAPSHOT_ID)
        self._index_by_id(self.relations, _RELATION_ID)
        snapshot_map = {s.snapshot_id: s for s in self.snapshots}
        # Relation endpoints must resolve before component analysis below.
        for relation in self.relations:
            for endpoint in (relation.from_snapshot, relation.to_snapshot):
                if endpoint not in snapshot_map:
                    raise InvestigationContractError(
                        f"relation {relation.relation_id} references unknown "
                        f"snapshot {endpoint!r}"
                    )
        # Review P1/P2 (#1458 rounds 3-5): connected components over declared
        # same-origin edges (same_source_group relation, same_entity
        # independence, derives_from lineage) and shared source_id, plus
        # contradiction pairs applied at group level.  E2 corroboration must
        # span components and must never cite contradicting groups.
        same_group_edges: list[tuple[str, str]] = []
        contradiction_edges: list[tuple[str, str]] = []
        for relation in self.relations:
            if (
                relation.relation in {"same_source_group", "derives_from"}
                or relation.independence == "same_entity"
            ):
                same_group_edges.append((relation.from_snapshot, relation.to_snapshot))
            if relation.relation == "contradicts":
                contradiction_edges.append((relation.from_snapshot, relation.to_snapshot))
        parent = {sid: sid for sid in snapshot_map}

        def _find(node: str) -> str:
            root = node
            while parent[root] != root:
                root = parent[root]
            while parent[node] != root:
                parent[node], node = root, parent[node]
            return root

        for left, right in same_group_edges:
            parent[_find(left)] = _find(right)
        # Round 5 P1: snapshots sharing a source_id are the same origin even
        # without an explicit relation — merge them before group checks.
        by_source: dict[str, list[str]] = {}
        for snap in self.snapshots:
            by_source.setdefault(snap.source_id, []).append(snap.snapshot_id)
        for members in by_source.values():
            for other in members[1:]:
                parent[_find(members[0])] = _find(other)
        # Round 5 P1: contradiction applies at group level — if any member of
        # one origin group contradicts any member of another, those groups
        # cannot corroborate each other.
        forbidden_group_pairs = {
            frozenset((_find(left), _find(right))) for left, right in contradiction_edges
        }
        # Review P1 (#1458 round 9): a settled conclusion (corroborated /
        # verified / refuted) cannot rest on internally contradictory evidence
        # — such a claim must be reported as unresolved instead.
        if contradiction_edges:
            linked_evidence = {e.evidence_id: e for e in self.evidences}
            for claim in self.claims:
                if claim.status not in {"corroborated", "verified", "refuted"}:
                    continue
                cited: list[str] = []
                for eid in claim.evidence_ids:
                    evidence_item = linked_evidence.get(eid)
                    if evidence_item is not None:
                        cited.extend(evidence_item.snapshot_ids)
                cited_roots = [_find(sid) for sid in cited]
                for index, left_root in enumerate(cited_roots):
                    for right_root in cited_roots[index + 1:]:
                        if frozenset((left_root, right_root)) in forbidden_group_pairs:
                            raise InvestigationContractError(
                                f"claim {claim.claim_id} status {claim.status!r} rests on "
                                "contradictory evidence; mark it unresolved instead"
                            )

        for ev in self.evidences:
            ev.validate_against_snapshots(snapshot_map)
            if ev.level == "E2":
                # P1 fix (#1458 review): E2 corroboration must span distinct
                # snapshots from distinct sources — a duplicated snapshot id or
                # two captures of one source is single-source evidence.
                if len(set(ev.snapshot_ids)) != len(ev.snapshot_ids):
                    raise InvestigationContractError(
                        f"E2 evidence {ev.evidence_id} lists duplicate snapshots"
                    )
                roots = [_find(sid) for sid in ev.snapshot_ids]
                if len(set(roots)) != len(roots):
                    raise InvestigationContractError(
                        f"E2 evidence {ev.evidence_id} declares independence over "
                        f"snapshots in the same source group: {list(ev.snapshot_ids)}"
                    )
                for index, left in enumerate(roots):
                    for right in roots[index + 1:]:
                        if frozenset((left, right)) in forbidden_group_pairs:
                            raise InvestigationContractError(
                                f"E2 evidence {ev.evidence_id} cites snapshots from "
                                f"contradicting source groups {list(ev.snapshot_ids)}"
                            )
        # Review P2 (#1458 round 2): a report may only contain claims from its
        # own investigation — foreign conclusions must not leak across reports.
        for claim in self.claims:
            if claim.investigation_id != self.investigation_id:
                raise InvestigationContractError(
                    f"claim {claim.claim_id} belongs to investigation "
                    f"{claim.investigation_id!r}, not {self.investigation_id!r}"
                )
        claim_map = {c.claim_id: c for c in self.claims}
        for ev in self.evidences:
            if ev.claim_id not in claim_map:
                raise InvestigationContractError(
                    f"evidence {ev.evidence_id} references unknown claim {ev.claim_id!r}"
                )
        evidence_map = self._index_by_id(self.evidences, _EVIDENCE_ID)
        for claim in self.claims:
            for eid in claim.evidence_ids:
                linked = evidence_map.get(eid)
                if linked is None:
                    raise InvestigationContractError(
                        f"claim {claim.claim_id} references unknown evidence {eid!r}"
                    )
                # P2 fix (#1458 review): the claim must own the evidence it
                # links; another claim's evidence cannot support this status.
                if linked.claim_id != claim.claim_id:
                    raise InvestigationContractError(
                        f"claim {claim.claim_id} links evidence {eid} owned by "
                        f"claim {linked.claim_id!r}"
                    )
        self._enforce_verification_gate()

    @staticmethod
    def _index_by_id(items: tuple[Any, ...], id_field: str) -> dict[str, Any]:
        index: dict[str, Any] = {}
        for item in items:
            key = getattr(item, id_field)
            if key in index:
                raise InvestigationContractError(f"duplicate {id_field} {key!r}")
            index[key] = item
        return index

    def _max_evidence_level(
        self, claim: Claim, evidence_map: Mapping[str, Evidence]
    ) -> str | None:
        """Highest level among evidence the claim actually links.

        Only evidence referenced by the claim's own ``evidence_ids`` counts —
        an E3 item that names the claim but is not linked to it must not
        elevate the claim's status (P1 fix, #1458 review).  ``evidence_map``
        is built once by the caller so gate validation stays linear in the
        number of claims (P2 fix, #1458 round 7).
        """
        level_rank = {level: rank for rank, level in enumerate(EVIDENCE_LEVELS)}
        levels = [
            evidence_map[eid].level for eid in claim.evidence_ids if eid in evidence_map
        ]
        if not levels:
            return None
        return max(levels, key=lambda level: level_rank[level])

    def _enforce_verification_gate(self) -> None:
        """No-evidence claims must never serialize as settled conclusions.

        ``corroborated`` requires evidence of level E2 or above (independent
        multi-source agreement); ``verified`` is strictly stronger and
        requires E3 (primary-source confirmation) or E4 (independent
        reproduction); ``refuted`` is also a conclusion and requires at least
        one linked sourced evidence item (E1 or above) — single-source E0/E1
        can never back corroborated/verified, and no evidence at all can
        never back any settled status.
        """
        evidence_map = {e.evidence_id: e for e in self.evidences}
        for claim in self.claims:
            if claim.status == "corroborated":
                max_level = self._max_evidence_level(claim, evidence_map)
                if max_level is None or max_level in {"E0", "E1"}:
                    raise InvestigationContractError(
                        f"claim {claim.claim_id} status 'corroborated' requires evidence "
                        f"of level E2 or above, got {max_level!r}"
                    )
            elif claim.status == "verified":
                max_level = self._max_evidence_level(claim, evidence_map)
                if max_level not in {"E3", "E4"}:
                    raise InvestigationContractError(
                        f"claim {claim.claim_id} status 'verified' requires evidence "
                        f"of level E3 or E4, got {max_level!r}"
                    )
            elif claim.status == "refuted":
                max_level = self._max_evidence_level(claim, evidence_map)
                if max_level is None or max_level == "E0":
                    raise InvestigationContractError(
                        f"claim {claim.claim_id} status 'refuted' requires sourced "
                        f"evidence of level E1 or above, got {max_level!r}"
                    )

    def to_dict(self) -> dict[str, Any]:
        self._enforce_verification_gate()
        return {
            "report_id": self.report_id,
            "investigation_id": self.investigation_id,
            "request": self.request.to_dict(),
            "claims": [c.to_dict() for c in self.claims],
            "evidences": [e.to_dict() for e in self.evidences],
            "snapshots": [s.to_dict() for s in self.snapshots],
            "relations": [r.to_dict() for r in self.relations],
            "artifact_digests": list(self.artifact_digests),
            "generated_at": self.generated_at,
        }

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InvestigationReport":
        _strict_fields(cls.__name__, data, set(cls.FIELDS))
        try:
            return cls(
                report_id=data["report_id"],
                investigation_id=data["investigation_id"],
                request=InvestigationRequest.from_dict(data["request"]),
                claims=tuple(Claim.from_dict(c) for c in data.get("claims", ())),
                evidences=tuple(Evidence.from_dict(e) for e in data.get("evidences", ())),
                snapshots=tuple(SourceSnapshot.from_dict(s) for s in data.get("snapshots", ())),
                relations=tuple(SourceRelation.from_dict(r) for r in data.get("relations", ())),
                artifact_digests=tuple(data.get("artifact_digests", ())),
                generated_at=data.get("generated_at", "1970-01-01T00:00:00Z"),
            )
        except KeyError as exc:
            raise InvestigationContractError(f"InvestigationReport missing field {exc}") from exc

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "InvestigationReport":
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvestigationContractError(f"report payload is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise InvestigationContractError("report payload must be a JSON object")
        return cls.from_dict(data)

    def unresolved_claim_ids(self) -> tuple[str, ...]:
        return tuple(c.claim_id for c in self.claims if c.status == "unresolved")


@dataclass(frozen=True, slots=True)
class InvestigationSummary:
    """Convenience rollup of one report's disposition (derived, not stored)."""

    report_id: str = field(default="")
    total_claims: int = 0
    verified: int = 0
    corroborated: int = 0
    refuted: int = 0
    unresolved: int = 0
    unverified: int = 0

    FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "report_id",
            "total_claims",
            "verified",
            "corroborated",
            "refuted",
            "unresolved",
            "unverified",
        }
    )

    @classmethod
    def from_report(cls, report: InvestigationReport) -> "InvestigationSummary":
        counts = {status: 0 for status in CLAIM_STATUSES}
        for claim in report.claims:
            counts[claim.status] += 1
        return cls(
            report_id=report.report_id,
            total_claims=len(report.claims),
            verified=counts["verified"],
            corroborated=counts["corroborated"],
            refuted=counts["refuted"],
            unresolved=counts["unresolved"],
            unverified=counts["unverified"],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InvestigationSummary":
        _strict_fields(cls.__name__, data, set(cls.FIELDS))
        try:
            return cls(
                report_id=data["report_id"],
                total_claims=int(data["total_claims"]),
                verified=int(data["verified"]),
                corroborated=int(data["corroborated"]),
                refuted=int(data["refuted"]),
                unresolved=int(data["unresolved"]),
                unverified=int(data["unverified"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvestigationContractError(f"InvestigationSummary invalid: {exc}") from exc


__all__ = [
    "CLAIM_STATUSES",
    "EVIDENCE_LEVELS",
    "EVIDENCE_LEVEL_DESCRIPTIONS",
    "INVESTIGATION_SCHEMA",
    "InvestigationContractError",
    "InvestigationReport",
    "InvestigationRequest",
    "InvestigationSummary",
    "Claim",
    "Evidence",
    "SNAPSHOT_KINDS",
    "SOURCE_INDEPENDENCE",
    "SOURCE_RELATIONS",
    "SourceRelation",
    "SourceSnapshot",
    "canonical_json_bytes",
]
