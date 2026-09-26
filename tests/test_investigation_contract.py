"""Investigation Mode I1 contract tests (issue #1458, offline-only).

Covers the issue acceptance criteria:
- explicit schema/fixture and field definitions (golden fixture round-trip);
- deterministic JSON serialize/deserialize round-trip;
- a claim with no evidence never serializes as a verified conclusion;
- E2/E3/E4 semantics strictly separated from E0/E1;
- everything runs inside the offline boundary (no network modules loaded).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from trustforge.investigation_contract import (
    CLAIM_STATUSES,
    EVIDENCE_LEVELS,
    EVIDENCE_LEVEL_DESCRIPTIONS,
    INVESTIGATION_SCHEMA,
    Claim,
    Evidence,
    InvestigationContractError,
    InvestigationReport,
    InvestigationRequest,
    InvestigationSummary,
    SourceRelation,
    SourceSnapshot,
    canonical_json_bytes,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "investigation" / "report_v1_golden.json"

_H = "sha256:" + "ab" * 32
_H2 = "sha256:" + "cd" * 32
_H3 = "sha256:" + "ef" * 32


def _request() -> InvestigationRequest:
    return InvestigationRequest(
        request_id="inv-req-t-001",
        subject="unit test investigation",
        question="test question?",
        created_at="2026-09-26T00:00:00Z",
    )


def _snapshot(sid: str, source: str, kind: str = "secondary") -> SourceSnapshot:
    return SourceSnapshot(
        snapshot_id=sid,
        source_id=source,
        uri=f"https://fixture.invalid/{sid}",
        captured_at="2026-09-26T00:00:00Z",
        content_hash=_H,
        kind=kind,
    )


def _claim(cid: str, status: str = "unverified", evidence_ids: tuple[str, ...] = ()) -> Claim:
    return Claim(
        claim_id=cid,
        investigation_id="inv-t-001",
        text=f"claim {cid}",
        status=status,
        evidence_ids=evidence_ids,
    )


def _evidence(eid: str, claim_id: str, level: str, **kwargs: object) -> Evidence:
    return Evidence(evidence_id=eid, claim_id=claim_id, level=level, **kwargs)  # type: ignore[arg-type]


def _report(
    claims: tuple[Claim, ...] = (),
    evidences: tuple[Evidence, ...] = (),
    snapshots: tuple[SourceSnapshot, ...] = (),
    relations: tuple[SourceRelation, ...] = (),
) -> InvestigationReport:
    return InvestigationReport(
        report_id="report-t-001",
        investigation_id="inv-t-001",
        request=_request(),
        claims=claims,
        evidences=evidences,
        snapshots=snapshots,
        relations=relations,
        generated_at="2026-09-26T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# Golden fixture round-trip (schema + determinism)
# ---------------------------------------------------------------------------


def test_golden_fixture_round_trip_byte_stable() -> None:
    payload = FIXTURE.read_bytes().strip()
    report = InvestigationReport.from_canonical_bytes(payload)
    assert report.to_canonical_bytes() == payload


def test_fixture_declares_all_schema_vocabulary() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(data) == set(InvestigationReport.FIELDS)
    assert set(EVIDENCE_LEVELS) == {"E0", "E1", "E2", "E3", "E4"}
    assert set(EVIDENCE_LEVEL_DESCRIPTIONS) == set(EVIDENCE_LEVELS)
    assert {"unverified", "unresolved"} <= set(CLAIM_STATUSES)
    assert INVESTIGATION_SCHEMA == "trustforge.investigation/v1"


def test_round_trip_deterministic_across_repeated_serialization() -> None:
    report = InvestigationReport.from_canonical_bytes(FIXTURE.read_bytes().strip())
    first = report.to_canonical_bytes()
    for _ in range(5):
        assert report.to_canonical_bytes() == first
        assert InvestigationReport.from_dict(report.to_dict()).to_canonical_bytes() == first


def test_from_dict_rejects_unknown_fields() -> None:
    report = InvestigationReport.from_canonical_bytes(FIXTURE.read_bytes().strip())
    data = report.to_dict()
    data["extra_field"] = 1
    with pytest.raises(InvestigationContractError):
        InvestigationReport.from_dict(data)


# ---------------------------------------------------------------------------
# Acceptance: claim without evidence must never serialize as verified
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status,match", [
    ("corroborated", "E2 or above"),
    ("verified", "E3 or E4"),
])
@pytest.mark.parametrize("level", ["E0", "E1"])
def test_e0_e1_evidence_cannot_support_verified_status(status: str, level: str, match: str) -> None:
    snap = _snapshot("snap-1", "src-1")
    ev_kwargs: dict[str, object] = {"snapshot_ids": ("snap-1",)} if level == "E1" else {}
    ev = _evidence("ev-1", "c-thin", level, **ev_kwargs)
    claim = _claim("c-thin", status=status, evidence_ids=("ev-1",))
    with pytest.raises(InvestigationContractError, match=match):
        _report(claims=(claim,), evidences=(ev,), snapshots=(snap,))


@pytest.mark.parametrize("status,match", [
    ("corroborated", "E2 or above"),
    ("verified", "E3 or E4"),
])
def test_claim_without_evidence_cannot_be_verified(status: str, match: str) -> None:
    with pytest.raises(InvestigationContractError, match=match):
        _report(claims=(_claim("c-no-ev", status=status),))


def test_verified_with_e3_evidence_is_allowed() -> None:
    snap = _snapshot("snap-p", "src-p", kind="primary")
    ev = _evidence("ev-p", "c-ok", "E3", snapshot_ids=("snap-p",))
    claim = _claim("c-ok", status="verified", evidence_ids=("ev-p",))
    report = _report(claims=(claim,), evidences=(ev,), snapshots=(snap,))
    assert report.claims[0].status == "verified"


def test_claim_statuses_round_trip() -> None:
    snaps = (
        _snapshot("snap-1", "src-1"),
        _snapshot("snap-2", "src-2"),
        _snapshot("snap-p", "src-p", kind="primary"),
    )
    for status in CLAIM_STATUSES:
        claims, evidences = (), ()
        if status == "corroborated":
            evidences = (
                _evidence(
                    "ev-corrob", f"c-{status}", "E2",
                    snapshot_ids=("snap-1", "snap-2"), independence="independent",
                ),
            )
        elif status == "verified":
            evidences = (
                _evidence(f"ev-ver-{status}", f"c-{status}", "E3", snapshot_ids=("snap-p",)),
            )
        elif status == "refuted":
            evidences = (
                _evidence(f"ev-ref-{status}", f"c-{status}", "E1", snapshot_ids=("snap-1",)),
            )
        claim = _claim(
            f"c-{status}", status=status,
            evidence_ids=tuple(ev.evidence_id for ev in evidences),
        )
        claims = (claim,)
        report = _report(claims=claims, evidences=evidences, snapshots=snaps)
        assert InvestigationReport.from_dict(report.to_dict()).claims[0].status == status


def test_verified_requires_e3_or_e4_not_e2() -> None:
    snaps = (_snapshot("snap-1", "src-1"), _snapshot("snap-2", "src-2"))
    ev = _evidence(
        "ev-e2", "c-ver", "E2",
        snapshot_ids=("snap-1", "snap-2"), independence="independent",
    )
    claim = _claim("c-ver", status="verified", evidence_ids=("ev-e2",))
    with pytest.raises(InvestigationContractError, match="E3 or E4"):
        _report(claims=(claim,), evidences=(ev,), snapshots=snaps)


# ---------------------------------------------------------------------------
# Acceptance: E2/E3/E4 semantics strictly separated from E0/E1
# ---------------------------------------------------------------------------


def test_e1_requires_at_least_one_snapshot() -> None:
    with pytest.raises(InvestigationContractError, match="at least one snapshot"):
        _evidence("ev-e1", "c-1", "E1")


def test_e2_requires_two_snapshots() -> None:
    snap = _snapshot("snap-1", "src-1")
    with pytest.raises(InvestigationContractError, match=">=2 independent snapshots"):
        _evidence(
            "ev-e2", "c-1", "E2",
            snapshot_ids=("snap-1",), independence="independent",
        )


def test_e2_requires_declared_independence() -> None:
    snaps = (_snapshot("snap-1", "src-1"), _snapshot("snap-2", "src-2"))
    with pytest.raises(InvestigationContractError, match="independence='independent'"):
        _evidence(
            "ev-e2", "c-1", "E2",
            snapshot_ids=("snap-1", "snap-2"), independence="same_entity",
        )


def test_e3_requires_primary_snapshot_in_report() -> None:
    snap_secondary = _snapshot("snap-s", "src-s", kind="secondary")
    ev = _evidence("ev-e3", "c-1", "E3", snapshot_ids=("snap-s",))
    claim = _claim("c-1", status="verified", evidence_ids=("ev-e3",))
    with pytest.raises(InvestigationContractError, match="kind 'primary'"):
        _report(claims=(claim,), evidences=(ev,), snapshots=(snap_secondary,))


def test_e4_requires_reproduced_from_digest() -> None:
    with pytest.raises(InvestigationContractError, match="reproduced_from"):
        _evidence("ev-e4", "c-1", "E4", snapshot_ids=("snap-1",))


def test_e4_accepts_valid_reproduction_digest() -> None:
    snap = _snapshot("snap-1", "src-1")
    ev = _evidence(
        "ev-e4", "c-1", "E4", snapshot_ids=("snap-1",), reproduced_from=_H2,
        note="recomputed from raw inputs",
    )
    claim = _claim("c-1", status="verified", evidence_ids=("ev-e4",))
    report = InvestigationReport(
        report_id="report-t-001", investigation_id="inv-t-001", request=_request(),
        claims=(claim,), evidences=(ev,), snapshots=(snap,),
        artifact_digests=(_H2,),
        generated_at="2026-09-26T00:00:00Z",
    )
    assert report.evidences[0].level == "E4"


def test_e4_digest_must_resolve_to_report_artifact() -> None:
    # Review P2 round 6: a syntactically valid digest that identifies no
    # bundled artifact does not establish independent reproduction.
    snap = _snapshot("snap-1", "src-1")
    ev = _evidence(
        "ev-e4", "c-1", "E4", snapshot_ids=("snap-1",), reproduced_from=_H2,
        note="recomputed from raw inputs",
    )
    claim = _claim("c-1", status="verified", evidence_ids=("ev-e4",))
    with pytest.raises(InvestigationContractError, match="artifact set"):
        _report(claims=(claim,), evidences=(ev,), snapshots=(snap,))
    report = InvestigationReport(
        report_id="report-t-001", investigation_id="inv-t-001", request=_request(),
        claims=(claim,), evidences=(ev,), snapshots=(snap,),
        artifact_digests=(_H2,),
        generated_at="2026-09-26T00:00:00Z",
    )
    assert report.evidences[0].reproduced_from in report.artifact_digests


def test_artifact_digests_must_be_unique_sha256() -> None:
    claim = _claim("c-1")
    with pytest.raises(InvestigationContractError, match="sha256"):
        InvestigationReport(
            report_id="report-t-001", investigation_id="inv-t-001", request=_request(),
            claims=(claim,), artifact_digests=("not-a-digest",),
            generated_at="2026-09-26T00:00:00Z",
        )
    with pytest.raises(InvestigationContractError, match="duplicates"):
        InvestigationReport(
            report_id="report-t-001", investigation_id="inv-t-001", request=_request(),
            claims=(claim,), artifact_digests=(_H2, _H2),
            generated_at="2026-09-26T00:00:00Z",
        )


def test_e2_constructible_with_two_independent_snapshots() -> None:
    snaps = (_snapshot("snap-1", "src-1"), _snapshot("snap-2", "src-2"))
    ev = _evidence(
        "ev-e2", "c-1", "E2",
        snapshot_ids=("snap-1", "snap-2"), independence="independent",
    )
    claim = _claim("c-1", status="corroborated", evidence_ids=("ev-e2",))
    report = _report(claims=(claim,), evidences=(ev,), snapshots=snaps)
    assert report.claims[0].status == "corroborated"


def test_e_level_descriptions_distinguish_e2plus_from_e0_e1() -> None:
    low = {EVIDENCE_LEVEL_DESCRIPTIONS["E0"], EVIDENCE_LEVEL_DESCRIPTIONS["E1"]}
    high = {
        EVIDENCE_LEVEL_DESCRIPTIONS["E2"],
        EVIDENCE_LEVEL_DESCRIPTIONS["E3"],
        EVIDENCE_LEVEL_DESCRIPTIONS["E4"],
    }
    assert low.isdisjoint(high)


# ---------------------------------------------------------------------------
# Structural integrity
# ---------------------------------------------------------------------------


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(InvestigationContractError, match="duplicate claim_id"):
        _report(claims=(_claim("c-dup"), _claim("c-dup")))


def test_claim_cannot_reference_unknown_evidence() -> None:
    claim = _claim("c-1", evidence_ids=("ev-missing",))
    with pytest.raises(InvestigationContractError, match="unknown evidence"):
        _report(claims=(claim,))


def test_unlinked_e3_evidence_does_not_verify_claim() -> None:
    # Review P1: an E3 evidence item that names the claim but is not linked
    # from the claim's evidence_ids must not elevate its status.
    snap = _snapshot("snap-p", "src-p", kind="primary")
    ev = _evidence("ev-p", "c-1", "E3", snapshot_ids=("snap-p",))
    claim = _claim("c-1", status="verified", evidence_ids=())
    with pytest.raises(InvestigationContractError, match="E3 or E4"):
        _report(claims=(claim,), evidences=(ev,), snapshots=(snap,))


def test_e2_rejects_duplicate_snapshot_ids() -> None:
    snap = _snapshot("snap-1", "src-1")
    ev = _evidence(
        "ev-e2", "c-1", "E2",
        snapshot_ids=("snap-1", "snap-1"), independence="independent",
    )
    claim = _claim("c-1", status="corroborated", evidence_ids=("ev-e2",))
    with pytest.raises(InvestigationContractError, match="duplicate snapshots"):
        _report(claims=(claim,), evidences=(ev,), snapshots=(snap,))


def test_e2_rejects_same_source_snapshots() -> None:
    snaps = (
        _snapshot("snap-1", "src-same"),
        _snapshot("snap-2", "src-same"),
    )
    ev = _evidence(
        "ev-e2", "c-1", "E2",
        snapshot_ids=("snap-1", "snap-2"), independence="independent",
    )
    claim = _claim("c-1", status="corroborated", evidence_ids=("ev-e2",))
    with pytest.raises(InvestigationContractError, match="same source group"):
        _report(claims=(claim,), evidences=(ev,), snapshots=snaps)


def test_claim_cannot_link_another_claims_evidence() -> None:
    snap = _snapshot("snap-1", "src-1")
    ev = _evidence("ev-b", "c-b", "E1", snapshot_ids=("snap-1",))
    claim_a = _claim("c-a", status="unverified", evidence_ids=("ev-b",))
    claim_b = _claim("c-b", evidence_ids=("ev-b",))
    with pytest.raises(InvestigationContractError, match="owned by claim"):
        _report(claims=(claim_a, claim_b), evidences=(ev,), snapshots=(snap,))


def test_relation_endpoints_must_resolve() -> None:
    snap = _snapshot("snap-1", "src-1")
    rel = SourceRelation(
        relation_id="rel-x", from_snapshot="snap-absent", to_snapshot="snap-1",
        relation="corroborates",
    )
    with pytest.raises(InvestigationContractError, match="unknown"):
        _report(snapshots=(snap,), relations=(rel,))


def test_e2_rejects_snapshots_related_as_same_group() -> None:
    # Review P1 round 2: declared same_source_group/same_entity relations
    # override an E2 evidence's independence declaration.
    snaps = (_snapshot("snap-1", "src-1"), _snapshot("snap-2", "src-2"))
    ev = _evidence(
        "ev-e2", "c-1", "E2",
        snapshot_ids=("snap-1", "snap-2"), independence="independent",
    )
    claim = _claim("c-1", status="corroborated", evidence_ids=("ev-e2",))
    for relation, independence in [
        ("same_source_group", "unknown"),
        ("corroborates", "same_entity"),
    ]:
        rel = SourceRelation(
            relation_id="rel-g", from_snapshot="snap-1", to_snapshot="snap-2",
            relation=relation, independence=independence,
        )
        with pytest.raises(InvestigationContractError, match="same source group"):
            _report(claims=(claim,), evidences=(ev,), snapshots=snaps, relations=(rel,))


def test_claim_from_other_investigation_rejected() -> None:
    foreign = Claim(
        claim_id="c-foreign", investigation_id="inv-other",
        text="foreign conclusion", status="unverified", evidence_ids=(),
    )
    with pytest.raises(InvestigationContractError, match="belongs to investigation"):
        _report(claims=(foreign,))


def test_oversized_excerpt_rejected_at_construction() -> None:
    with pytest.raises(InvestigationContractError, match="excerpt"):
        SourceSnapshot(
            snapshot_id="snap-x", source_id="src-x",
            uri="https://fixture.invalid/x",
            captured_at="2026-09-26T00:00:00Z",
            content_hash=_H, excerpt="x" * 5000,
        )


def test_oversized_notes_rejected_at_construction() -> None:
    snap = _snapshot("snap-1", "src-1")
    with pytest.raises(InvestigationContractError, match="note"):
        _evidence("ev-1", "c-1", "E1", snapshot_ids=("snap-1",), note="n" * 5000)
    with pytest.raises(InvestigationContractError, match="note"):
        SourceRelation(
            relation_id="rel-n", from_snapshot="snap-1", to_snapshot="snap-1",
            relation="corroborates", note="n" * 5000,
        )


def test_reproduced_from_must_be_sha256_when_present() -> None:
    snap = _snapshot("snap-1", "src-1")
    with pytest.raises(InvestigationContractError, match="reproduced_from"):
        _evidence(
            "ev-e4", "c-1", "E4",
            snapshot_ids=("snap-1",), reproduced_from="not-a-digest",
        )


def _e2_case() -> tuple[Claim, Evidence, tuple[SourceSnapshot, ...]]:
    snaps = (_snapshot("snap-1", "src-1"), _snapshot("snap-2", "src-2"))
    ev = _evidence(
        "ev-e2", "c-1", "E2",
        snapshot_ids=("snap-1", "snap-2"), independence="independent",
    )
    claim = _claim("c-1", status="corroborated", evidence_ids=("ev-e2",))
    return claim, ev, snaps


def test_e2_rejects_contradicting_snapshots() -> None:
    # Review P1 round 3: a declared contradicts edge between cited snapshots
    # disqualifies them as mutual corroboration.
    claim, ev, snaps = _e2_case()
    rel = SourceRelation(
        relation_id="rel-ct", from_snapshot="snap-1", to_snapshot="snap-2",
        relation="contradicts",
    )
    with pytest.raises(InvestigationContractError, match="contradicting"):
        _report(claims=(claim,), evidences=(ev,), snapshots=snaps, relations=(rel,))


def test_e2_rejects_indirect_same_group_snapshots() -> None:
    # Review P2 round 3: same-origin components are transitive — A~B and B~C
    # means A and C share one origin even without a direct edge.
    claim, ev, snaps = _e2_case()
    rels = (
        SourceRelation(
            relation_id="rel-g1", from_snapshot="snap-1", to_snapshot="snap-mid",
            relation="same_source_group",
        ),
        SourceRelation(
            relation_id="rel-g2", from_snapshot="snap-mid", to_snapshot="snap-2",
            relation="same_source_group",
        ),
    )
    all_snaps = snaps + (_snapshot("snap-mid", "src-mid"),)
    with pytest.raises(InvestigationContractError, match="same source group"):
        _report(claims=(claim,), evidences=(ev,), snapshots=all_snaps, relations=rels)


def test_e2_rejects_derived_snapshots() -> None:
    # Review P1 round 4: a repost/derivative traces to the same origin and
    # cannot corroborate its upstream source as independent.
    claim, ev, snaps = _e2_case()
    rel = SourceRelation(
        relation_id="rel-dv", from_snapshot="snap-2", to_snapshot="snap-1",
        relation="derives_from",
    )
    with pytest.raises(InvestigationContractError, match="same source group"):
        _report(claims=(claim,), evidences=(ev,), snapshots=snaps, relations=(rel,))


@pytest.mark.parametrize("status", ["verified", "corroborated", "refuted"])
def test_standalone_claim_to_dict_refuses_settled_without_evidence(status: str) -> None:
    # Review P2 rounds 4/6: standalone serialization must not emit a settled
    # conclusion for a claim with no linked evidence.
    claim = _claim("c-solo", status=status, evidence_ids=())
    with pytest.raises(InvestigationContractError, match="settled conclusion"):
        claim.to_dict()


def test_refuted_without_sourced_evidence_rejected() -> None:
    # Review P1 round 6: refuted is also a conclusion — it needs sourced
    # evidence (E1 or above), not merely an assertion.
    claim = _claim("c-ref", status="refuted", evidence_ids=())
    with pytest.raises(InvestigationContractError, match="status 'refuted'"):
        _report(claims=(claim,))
    snap = _snapshot("snap-1", "src-1")
    ev = _evidence("ev-r", "c-ref", "E1", snapshot_ids=("snap-1",))
    claim_ok = _claim("c-ref", status="refuted", evidence_ids=("ev-r",))
    report = _report(claims=(claim_ok,), evidences=(ev,), snapshots=(snap,))
    assert report.claims[0].status == "refuted"


def test_e2_rejects_same_source_id_via_relation_chain() -> None:
    # Review P1 round 5: source_id merges snapshots into one origin group even
    # when the evidence cites snapshots without a direct relation.
    snaps = (
        _snapshot("snap-a", "src-shared"),
        _snapshot("snap-b", "src-shared"),
        _snapshot("snap-c", "src-c"),
    )
    ev = _evidence(
        "ev-e2", "c-1", "E2",
        snapshot_ids=("snap-a", "snap-c"), independence="independent",
    )
    claim = _claim("c-1", status="corroborated", evidence_ids=("ev-e2",))
    rel = SourceRelation(
        relation_id="rel-bc", from_snapshot="snap-b", to_snapshot="snap-c",
        relation="derives_from",
    )
    with pytest.raises(InvestigationContractError, match="same source group"):
        _report(claims=(claim,), evidences=(ev,), snapshots=snaps, relations=(rel,))


def test_e2_rejects_group_level_contradiction() -> None:
    # Review P1 round 5: A and B share an origin; B contradicts C, so A and C
    # are from contradicting groups even though A itself has no direct edge.
    snaps = (
        _snapshot("snap-a", "src-g1"),
        _snapshot("snap-b", "src-g1"),
        _snapshot("snap-c", "src-g2"),
    )
    ev = _evidence(
        "ev-e2", "c-1", "E2",
        snapshot_ids=("snap-a", "snap-c"), independence="independent",
    )
    claim = _claim("c-1", status="corroborated", evidence_ids=("ev-e2",))
    rels = (
        SourceRelation(
            relation_id="rel-ab", from_snapshot="snap-a", to_snapshot="snap-b",
            relation="same_source_group",
        ),
        SourceRelation(
            relation_id="rel-bc", from_snapshot="snap-b", to_snapshot="snap-c",
            relation="contradicts",
        ),
    )
    with pytest.raises(InvestigationContractError, match="contradicting source groups"):
        _report(claims=(claim,), evidences=(ev,), snapshots=snaps, relations=rels)


def test_e0_must_not_carry_snapshots() -> None:
    # Review P2 round 5: E0 means no captured source; snapshots require E1+.
    with pytest.raises(InvestigationContractError, match="must not carry snapshots"):
        _evidence("ev-e0", "c-1", "E0", snapshot_ids=("snap-1",))


def test_evidence_cannot_reference_unknown_snapshot() -> None:
    ev = _evidence("ev-1", "c-1", "E1", snapshot_ids=("snap-missing",))
    claim = _claim("c-1", evidence_ids=("ev-1",))
    with pytest.raises(InvestigationContractError, match="unknown snapshot"):
        _report(claims=(claim,), evidences=(ev,))


def test_invalid_status_and_level_rejected() -> None:
    with pytest.raises(InvestigationContractError):
        _claim("c-1", status="definitely-true")
    with pytest.raises(InvestigationContractError):
        _evidence("ev-1", "c-1", "E5")


def test_content_hash_must_be_sha256() -> None:
    with pytest.raises(InvestigationContractError, match="sha256"):
        SourceSnapshot(
            snapshot_id="snap-x",
            source_id="src-x",
            uri="https://fixture.invalid/x",
            captured_at="2026-09-26T00:00:00Z",
            content_hash="md5:" + "ab" * 16,
        )


def test_impossible_calendar_timestamp_rejected() -> None:
    # Review P2 round 7: regex-shaped but invalid dates must fail early.
    with pytest.raises(InvestigationContractError, match="valid UTC timestamp"):
        InvestigationRequest(
            request_id="inv-req-bad-ts",
            subject="bad timestamp",
            question="q",
            created_at="2026-99-99T99:99:99Z",
        )


def test_scope_tags_rejects_bare_string_and_oversized_entries() -> None:
    # Review P2 round 8: a bare string must not silently split into
    # per-character tags, and each tag is length-bounded at construction.
    with pytest.raises(InvestigationContractError, match="sequence of strings"):
        InvestigationRequest(
            request_id="inv-req-tags",
            subject="tags",
            question="q",
            created_at="2026-09-26T00:00:00Z",
            scope_tags="fraud",  # type: ignore[arg-type]
        )
    with pytest.raises(InvestigationContractError, match="scope_tags entries"):
        InvestigationRequest(
            request_id="inv-req-tags2",
            subject="tags",
            question="q",
            created_at="2026-09-26T00:00:00Z",
            scope_tags=("ok", "x" * 5000),
        )


def test_e4_requires_documented_reproduction_method() -> None:
    # Review P2 round 8: digest membership alone must not grant E4 — the
    # reproduction method has to be documented.
    snap = _snapshot("snap-1", "src-1")
    with pytest.raises(InvestigationContractError, match="reproduction method"):
        _evidence(
            "ev-e4", "c-1", "E4",
            snapshot_ids=("snap-1",), reproduced_from=_H2, note="",
        )
    ev = _evidence(
        "ev-e4", "c-1", "E4",
        snapshot_ids=("snap-1",), reproduced_from=_H2,
        note="recomputed aggregate from raw ledger rows",
    )
    claim = _claim("c-1", status="verified", evidence_ids=("ev-e4",))
    report = InvestigationReport(
        report_id="report-t-001", investigation_id="inv-t-001", request=_request(),
        claims=(claim,), evidences=(ev,), snapshots=(snap,),
        artifact_digests=(_H2,),
        generated_at="2026-09-26T00:00:00Z",
    )
    assert report.evidences[0].level == "E4"


def test_settled_claim_rejected_when_evidence_contradicts_itself() -> None:
    # Review P1 round 9: high-level evidence does not settle a claim when
    # other linked evidence comes from a contradicting snapshot — the honest
    # status is unresolved.
    snap_p = _snapshot("snap-p", "src-p", kind="primary")
    snap_c = _snapshot("snap-c", "src-c")
    ev_high = _evidence("ev-hi", "c-1", "E3", snapshot_ids=("snap-p",))
    ev_low = _evidence("ev-lo", "c-1", "E1", snapshot_ids=("snap-c",))
    rel = SourceRelation(
        relation_id="rel-ct", from_snapshot="snap-c", to_snapshot="snap-p",
        relation="contradicts",
    )
    claim = _claim("c-1", status="verified", evidence_ids=("ev-hi", "ev-lo"))
    with pytest.raises(InvestigationContractError, match="contradictory evidence"):
        _report(
            claims=(claim,), evidences=(ev_high, ev_low),
            snapshots=(snap_p, snap_c), relations=(rel,),
        )


def test_canonical_json_rejects_cycles_and_nonfinite() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(InvestigationContractError, match="cycle"):
        canonical_json_bytes(cyclic)
    with pytest.raises(InvestigationContractError):
        canonical_json_bytes({"nan": float("nan")})
    with pytest.raises(InvestigationContractError):
        canonical_json_bytes({"custom": object()})


# ---------------------------------------------------------------------------
# Summary rollup
# ---------------------------------------------------------------------------


def test_summary_counts_by_status() -> None:
    report = InvestigationReport.from_canonical_bytes(FIXTURE.read_bytes().strip())
    summary = InvestigationSummary.from_report(report)
    assert summary.total_claims == 4
    assert summary.verified == 1
    assert summary.corroborated == 1
    assert summary.unresolved == 1
    assert summary.unverified == 1
    assert report.unresolved_claim_ids() == ("claim-unresolved-1",)
    assert InvestigationSummary.from_dict(summary.to_dict()) == summary


# ---------------------------------------------------------------------------
# Offline boundary: module and tests load no network stack
# ---------------------------------------------------------------------------


def test_module_imports_no_network_stack() -> None:
    src = pathlib.Path(__file__).parent.parent / "src" / "trustforge" / "investigation_contract.py"
    text = src.read_text(encoding="utf-8")
    for banned in ("requests", "httpx", "urllib", "socket", "aiohttp"):
        assert f"import {banned}" not in text and f"from {banned}" not in text


def test_import_stays_offline_in_fresh_interpreter() -> None:
    repo_root = pathlib.Path(__file__).parent.parent
    code = (
        "import sys; "
        "import trustforge.investigation_contract as m; "
        "banned = [n for n in ('requests', 'httpx', 'urllib3', 'aiohttp') if n in sys.modules]; "
        "print('BANNED=' + ','.join(banned)); "
        "print('OK' if not banned else 'FAIL')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env={"PYTHONPATH": str(repo_root / "src"), "PATH": "/usr/bin:/bin"},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout, result.stdout
