| Field | Value |
| --- | --- |
| Document ID | AIMS-SUPPORT-LIFE-TRACE-001 |
| Version / status | 0.1-draft / draft, unapproved, non-effective |
| Owner / approver | CEO assignment pending / CEO approval pending |
| Approval record / effective date | pending / not-applicable (draft) |
| Review cadence / next review | set on approval / set on approval |
| Classification | internal-draft |
| Change summary / supersedes | establish document lifecycle trace register / not-applicable (initial draft) |
| Repository path | `docs/aims/05-support/document-lifecycle-trace.md` |

# Document Lifecycle Trace

This register is the support-control evidence structure for issue #1242. It does not claim any controlled document has completed approval or obsolescence until the evidence URI and approver fields are populated.

## Trace Requirements

| Transition | Required evidence | Required approver | Failure rule |
| --- | --- | --- | --- |
| draft -> in-review | draft version, change rationale, reviewer list, review request URI | document owner | Git commit alone is not approval evidence. |
| in-review -> approved | resolved findings, exact approved version, approval date, effective date, next review date | authorised approver | Missing approval keeps the document `draft / unapproved / non-effective`. |
| approved -> obsolete | superseding version, obsolete marker, retention path, access decision | authorised approver | Obsolete documents cannot be used as current operating instruction. |
| emergency correction | containment rationale, affected audience, follow-up review due date | accountable executive | Emergency use expires unless normal approval is completed. |

## Initial Demonstration Slot

| Field | Planned value |
| --- | --- |
| Candidate document | `docs/aims/05-support/document-and-communication-control.md` |
| Draft evidence URI | pending PR / commit URI |
| In-review evidence URI | pending reviewer request and findings URI |
| Approval evidence URI | pending authorised approval |
| Obsolete evidence URI | pending after a superseding version exists |
| Current status | planned; no lifecycle demonstration completed |

## Guardrails

- Do not copy private review notes or restricted evidence into this repository to make a trace look complete.
- Do not mark training as `completed` without dated attendance or equivalent evidence.
- Do not mark training as `verified` without objective assessment evidence.
- External claims about certification, compliance, conformity, CE marking, or presumption of conformity require exact approved text and Compliance approval.
