# AIMS Audit Programme And Report Template

| Field | Value |
| --- | --- |
| Document ID | AIMS-AUDIT-PLAN-001 |
| Version / status | 0.2-draft / draft, unapproved, non-effective |
| Owner / approver | Independent auditor pending / CEO approval pending |
| Approval record / effective date | pending / not applicable (draft) |
| Review cadence / next review | annual programme and issue-triggered audits / set on approval |
| Classification | internal draft |
| Change summary / supersedes | expands issue #1245 audit programme, finding template and report status / v0.1 draft |
| Repository path | `docs/aims/09-audit/audit-programme-and-report.md` |

This is a readiness template, not an internal audit record. It becomes an audit record only after an independent auditor, approved scope, criteria, samples, evidence, findings, and report approval are present.

## Audit Programme

| Audit ID | Scope | Criteria | Sampling method | Method | Planned timing | Auditor | Independence record | Output | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AUD-GOV-001 | AIMS governance, status semantics and document control | approved AIMS scope and document-control rules | all index files plus sampled linked docs | repository inspection and evidence replay | pending | independent auditor pending | auditor must not audit own authored controls | report with findings/CAPA links | planned |
| AUD-LIFE-001 | Lifecycle and supplier/source controls | issue #1243 acceptance criteria and approved lifecycle matrix | sampled source/model cards and behavior-change records | trace source card to runtime/report evidence | pending | independent auditor pending | auditor assignment pending | lifecycle effectiveness findings | planned |
| AUD-MEAS-001 | KPI, monitoring, CAPA and management review | issue #1245 acceptance criteria and approved KPI/CAPA rules | all P0/P1 CAPA plus KPI samples | register inspection and replay | pending | independent auditor pending | auditor assignment pending | KPI/CAPA finding report | planned |
| AUD-LEGAL-001 | External legal/compliance claims | issue #1264 claim guardrails and counsel-approved text | sampled external statements and repository docs | compare exact text to approval evidence | pending licensed source/counsel | Compliance Counsel or independent legal reviewer | reviewer independence documented | external-claim disposition | blocked |

## Finding Template

| Field | Required content |
| --- | --- |
| Finding ID | Stable ID as `AUD-FIND-YYYY-NNN`. |
| Source | Audit ID, scanner, incident, customer report, or reviewer finding. |
| Evidence | Repository path, PR, run artifact, scan report, or communication record. |
| Severity | P0/P1/P2/P3 with rationale and affected parties. |
| Owner | Named role or person; missing owner keeps finding open. |
| Due date | Date approved by accountable executive; missing due date counts overdue for P0/P1. |
| Containment | Immediate action to prevent further impact. |
| Correction | Specific fix for the observed nonconformity. |
| Corrective action | Systemic action to prevent recurrence. |
| Effectiveness review | Date, reviewer, method and evidence after action is used. |
| CAPA link | Required for systemic or recurrence-prone findings. |
| Status | open, contained, corrected, effectiveness-review, closed. |

## Current Report Status

No internal audit has been performed by this document. The first audit report remains pending independent auditor assignment, scope approval, sampling plan, evidence collection and management-review disposition.
