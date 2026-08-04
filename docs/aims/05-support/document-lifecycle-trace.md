# AIMS Document Lifecycle Trace

| Field | Value |
| --- | --- |
| Document ID | AIMS-SUPPORT-LIFE-TRACE-001 |
| Version / status | 0.2-draft / draft, unapproved, non-effective |
| Owner / approver | AIMS manager pending / CEO approval pending |
| Approval record / effective date | pending / not applicable (draft) |
| Review cadence / next review | annual and on supersession / set on approval |
| Classification | internal draft |
| Change summary / supersedes | demonstrates controlled draft-review-approval-obsolete evidence fields for issue #1242 / v0.1 draft |
| Repository path | `docs/aims/05-support/document-lifecycle-trace.md` |

This trace demonstrates the evidence that must exist for a real AIMS document transition. The current repository can show the draft and review-request steps through PR evidence; it does not invent approval or obsolete records that have not happened.

## Transition Requirements

| Transition | Required evidence | Required approver | Status vocabulary |
| --- | --- | --- | --- |
| draft -> in review | draft version, change rationale, reviewer list, review request URI, affected document list | document owner | `in-review` |
| in review -> approved | resolved findings, exact approved version, approval date, effective date, next review date | named approver from document metadata | `approved` |
| approved -> obsolete | superseding document/version, obsolete marker, retention path, access decision | original approver or delegated AIMS manager | `obsolete` |
| emergency correction | containment rationale, affected audience, follow-up review due date, retroactive approver | accountable executive | `emergency-corrected` until reviewed |

## Demonstration Record

| Trace field | Value |
| --- | --- |
| Candidate document | `docs/aims/05-support/document-and-communication-control.md` |
| Draft evidence URI | this branch commit and issue-development PR for #1242 |
| Review request URI | pending PR review request to `cancleeric`; legal/compliance/security reviewers pending |
| Approval evidence URI | pending; document remains draft until a named approver approves exact content |
| Obsolete evidence URI | pending; no superseding approved version exists |
| Current lifecycle status | `in-review` once PR is opened; not approved and not effective |

## Sample Future Obsolete Record

| Field | Required future value |
| --- | --- |
| Superseded document ID | `AIMS-SUPPORT-DOC-COMM-001` |
| Superseded version | exact approved version, not a branch name |
| Replacement URI | path and commit/approval URI for replacement |
| Retention decision | retain in repository history and controlled archive, or cite external archive URI |
| Access decision | public, internal, restricted, or removed with legal basis |

## Nonconforming Lifecycle Handling

If a document is published externally, relied on in an audit, or referenced by a customer before approval evidence exists, the AIMS manager must open a finding and CAPA. The finding must cite the document path, publication channel, audience, containment action, and whether any external correction is required.
