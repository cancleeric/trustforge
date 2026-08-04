# AIMS CAPA And Management Review

| Field | Value |
| --- | --- |
| Document ID | AIMS-CAPA-001 |
| Version / status | 0.2-draft / draft, unapproved, non-effective |
| Owner / approver | AIMS manager pending / CEO, CISO, CPO approval pending |
| Approval record / effective date | pending / not applicable (draft) |
| Review cadence / next review | management review and after material finding closure / set on approval |
| Classification | internal draft |
| Change summary / supersedes | expands issue #1245 CAPA register, effectiveness and management review loop / v0.1 draft |
| Repository path | `docs/aims/10-capa/capa-and-management-review.md` |

This document defines the draft corrective-action loop. It does not close any real finding until containment, correction, corrective action, effectiveness review and closure approval evidence exist.

## CAPA Register

| CAPA ID | Source | Severity | Containment | Root cause | Correction | Corrective action | Effectiveness review | Closure approval | Owner | Due date | Status | Evidence URI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AIMS-CAPA-0001 | AIMS-FIND-0001 | P2 | Draft documents are explicitly marked unapproved and non-effective | AIMS work packages initially had skeleton documents with incomplete traceability | Add role, risk, lifecycle, measurement, audit, CAPA and SoA traceability fields | Complete independent review after dependencies merge and owners are assigned | pending; cannot verify until approved documents are used | pending CEO / independent auditor | AIMS manager pending | pending | open readiness exercise | this PR and #1242-#1246 |
| AIMS-CAPA-0002 | AIMS-RISK-0001 | P1 | Reports must not claim source diversity without source-kind evidence | Rich snapshots can degrade into narrow report claims | Implement source-kind distribution tests and report disclosure in #1340 | Add regression to prevent sparse-data abstain regression | pending implementation | Product owner pending | Product owner pending | pending #1340 PR | planned | `https://github.com/cancleeric/trustforge/issues/1340` |
| AIMS-CAPA-0003 | AIMS-RISK-0002 | P1 legal | External EN/EU claims remain draft/gap-assessment only | Licensed legal source and counsel disposition unavailable | Maintain prohibited-claim guardrail in support and EU overlay docs | Counsel review before any effective or external publication | pending legal review | Compliance Counsel pending | Compliance Counsel pending | pending legal source access | blocked | `docs/aims/03-eu-ai-act/en-18286-qms-overlay.md` |

No P0 CAPA is recorded in this draft. If any P0 CAPA is opened later, missing owner or due date must count as overdue until corrected.

## Management Review Pack

| Review area | Required input | Decision needed | Evidence URI | Status |
| --- | --- | --- | --- | --- |
| KPI | KPI values, missing-data notes, trends, unreplayable sample count | approve targets, resource needs and corrective actions | `docs/aims/08-measurement/kpi-and-monitoring-register.md` | planned |
| Risk | P0/P1 risks, accepted risks, residual-risk changes, revocation triggers | accept, mitigate, avoid, transfer or monitor with owner and review date | `docs/aims/03-risk/risk-methodology-and-register.md` | planned |
| Audit | findings, independence record, scope gaps, unresolved reviewer comments | approve audit plan and CAPA requirements | `docs/aims/09-audit/audit-programme-and-report.md` | planned |
| CAPA | overdue actions, effectiveness review, closure requests | approve closure, extend due date with rationale, or escalate | this document | planned |
| Incidents | incident trends, unresolved containment, external communication needs | assign resources and legal/customer action | pending incident log | planned |
| Resources | owner vacancies, tooling, legal-text access, reviewer capacity | appoint owners and fund blockers | issue #1264; #1242 role register | planned |

## Closure Rules

- CAPA closure requires evidence that correction and corrective action were implemented and independently reviewed.
- A CAPA cannot close only because a document exists; the effectiveness review must show the corrected process was used.
- Legal, security or cost CAPA requires the corresponding reviewer before closure.
- Management review must record decisions, owner, due date, and evidence URI for every unresolved P0/P1 item.
