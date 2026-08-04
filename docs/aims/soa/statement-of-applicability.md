# AIMS Statement Of Applicability And Readiness Review

| Field | Value |
| --- | --- |
| Document ID | AIMS-SOA-001 |
| Version / status | 0.2-draft / draft, unapproved, non-effective |
| Owner / approver | Compliance owner pending / CEO, Compliance Counsel, CISO, CPO approval pending |
| Approval record / effective date | pending / not applicable (draft) |
| Review cadence / next review | annual and before certification assessment / set on approval |
| Classification | internal draft |
| Change summary / supersedes | expands issue #1246 SoA, evidence index and readiness gap review / v0.1 draft |
| Repository path | `docs/aims/soa/statement-of-applicability.md` |

This draft supports issues #1246 and #1264. It does not copy ISO/IEC 42001 or EN 18286 text. A compliance owner must review a lawfully obtained current standard before any control disposition becomes effective.

## Statement Of Applicability

| SoA ID | Requirement area | Applicability | Rationale | Owner | Disposition | Evidence URI | Review dates |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AIMS-SOA-0001 | AIMS scope, stakeholders and system inventory | applicable draft | TrustForge operates AI-assisted market-analysis workflows that need bounded scope and affected-party records | AIMS manager pending | aligned draft | `docs/aims/01-scope/scope.md`; `docs/aims/02-policy/ai-system-inventory.md` | pending |
| AIMS-SOA-0002 | Policy, roles, RACI and risk acceptance | applicable draft | AIMS decisions require accountable executive, product, security, compliance and audit roles | CEO pending | partial implementation | `docs/aims/02-policy/ai-policy.md`; `docs/aims/02-policy/roles-raci-and-risk-acceptance.md` | pending |
| AIMS-SOA-0003 | Risk and impact assessment | applicable draft | AI outputs may affect customers, evidence integrity, security, legal claims and operating decisions | Risk owner pending | partial implementation | `docs/aims/03-risk/risk-methodology-and-register.md`; `docs/aims/04-impact/impact-assessment.md` | pending |
| AIMS-SOA-0004 | Support: competence, training, communication and document control | applicable draft | Roles, training evidence and external claim controls are required before operation or assessment | AIMS manager pending | aligned draft | `docs/aims/05-support/competency-and-training-register.md`; `docs/aims/05-support/document-and-communication-control.md` | pending |
| AIMS-SOA-0005 | AI lifecycle and supplier/source controls | applicable draft | Source/model/provider behavior must be traceable through design, validation, operation and retirement | Engineering owner pending | partial implementation | `docs/aims/06-lifecycle/lifecycle-control-matrix.md`; `docs/aims/07-suppliers/supplier-and-source-cards.md` | pending |
| AIMS-SOA-0006 | Measurement, audit, CAPA and management review | applicable draft | Effectiveness claims require KPIs, independent audit, findings, CAPA and management decisions | Independent auditor pending | aligned draft | `docs/aims/08-measurement/kpi-and-monitoring-register.md`; `docs/aims/09-audit/audit-programme-and-report.md`; `docs/aims/10-capa/capa-and-management-review.md` | pending |
| AIMS-SOA-0007 | EU AI Act / EN 18286 overlay | applicability pending | Intended purpose, operator role, risk classification and legal text status are not approved | Compliance Counsel pending | delegated gap assessment | `docs/aims/03-eu-ai-act/README.md`; `docs/aims/03-eu-ai-act/en-18286-qms-overlay.md` | pending licensed source |

Excluded controls must include a concrete rationale and approver. Lack of evidence is a gap, not a reason to mark a control not applicable.

## Evidence Index

| Evidence set | Primary documents | Current status | Missing evidence |
| --- | --- | --- | --- |
| Governance baseline | `docs/aims/README.md`; `docs/aims/01-scope/scope.md`; `docs/aims/02-policy/roles-raci-and-risk-acceptance.md` | draft | named owners, approvals, effective dates |
| Support package | `docs/aims/05-support/competency-and-training-register.md`; `docs/aims/05-support/document-and-communication-control.md`; `docs/aims/05-support/document-lifecycle-trace.md` | aligned draft | training completion/verification records, approver evidence |
| Lifecycle package | `docs/aims/06-lifecycle/lifecycle-control-matrix.md`; `docs/aims/07-suppliers/supplier-and-source-cards.md` | partial implementation | runtime replay evidence and independent effectiveness review |
| Measurement package | `docs/aims/08-measurement/kpi-and-monitoring-register.md`; `docs/aims/09-audit/audit-programme-and-report.md`; `docs/aims/10-capa/capa-and-management-review.md` | aligned draft | KPI baselines, first audit, CAPA effectiveness review |
| EU AI Act overlay | `docs/aims/03-eu-ai-act/applicability-and-classification.md`; `docs/aims/03-eu-ai-act/en-18286-qms-overlay.md` | blocked / delegated gap assessment | licensed EN 18286 text, official bibliographic/OJ status, counsel disposition |

## Readiness Gap Review

| Gap ID | Category | Gap | Blocker | Disposition |
| --- | --- | --- | --- | --- |
| AIMS-GAP-0001 | Owner / approval | Most owners, approvers, review dates and effective dates remain pending | CEO assignment and governance approval | delegated gap assessment |
| AIMS-GAP-0002 | Legal text | EN 18286 licensed text and official status are not available in this repository | company-licensed authoritative copy and Compliance Counsel review | delegated gap assessment |
| AIMS-GAP-0003 | Evidence | Several controls are documented but lack replayable runtime or audit evidence | issue-specific implementation PRs, tests and first audit | certification preparation |
| AIMS-GAP-0004 | External assessment | No certification body or independent auditor has approved the AIMS | external assessment engagement | certification preparation |
| AIMS-GAP-0005 | Public claims | External statements can become misleading if draft status is omitted | counsel-approved exact text and communication-control approval | aligned draft |

## Disposition Boundary

The allowed disposition values in this draft are `aligned draft`, `partial implementation`, `delegated gap assessment`, `certification preparation`, `blocked`, and `not applicable with approved rationale`. The values `certified`, `compliant`, `conformant`, `CE-ready`, or equivalent unqualified external claims are prohibited until the applicable legal, auditor and executive approvals exist.
