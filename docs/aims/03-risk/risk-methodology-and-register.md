# AIMS Risk Methodology And Register

| Field | Value |
| --- | --- |
| Document ID | AIMS-RISK-001 |
| Version / status | 0.2-draft / draft, unapproved, non-effective |
| Owner / approver | Risk owner pending / CEO, CPO, CISO, Compliance Counsel approval pending |
| Approval record / effective date | pending / not applicable (draft) |
| Review cadence / next review | quarterly after approval and on material AI-system change / set on approval |
| Classification | internal draft |
| Change summary / supersedes | repairs and expands issue #1244 methodology, taxonomy, register and treatment fields / v0.1 draft |
| Repository path | `docs/aims/03-risk/risk-methodology-and-register.md` |

This document defines a draft AIMS risk method for TrustForge. It does not mean any risk is accepted, any control is effective, or any EU AI Act / EN 18286 classification has been legally determined.

## Method

| Dimension | Draft rule | Evidence requirement |
| --- | --- | --- |
| Likelihood | 1 rare, 2 unlikely, 3 possible, 4 likely, 5 frequent | cite incident, test, replay, scanner, issue, or expert review URI |
| Impact | 1 negligible, 2 minor, 3 moderate, 4 major, 5 severe | describe customer, individual, regulatory, security, operational and business impact |
| Inherent score | likelihood x impact before treatment | explicit rationale |
| Residual score | likelihood x impact after implemented and verified controls | implemented-control evidence; `not scored` when evidence is pending |
| Treatment | mitigate, avoid, transfer, accept, or monitor | owner, due date, review date, and revocation condition |
| Acceptance | only accountable executive may accept residual risk; security/legal risks require CISO or counsel review | signed approval or PR/review URI |

## Taxonomy

| Taxonomy ID | Area | Example scenarios | Affected parties |
| --- | --- | --- | --- |
| RISK-DATA | Data and evidence integrity | stale snapshot, low-trust source, source-kind imbalance, missing evidence URI | customers, analysis reviewer |
| RISK-MODEL | Model and agent behavior | hallucination, unsupported synthesis, overconfident summary, authority leakage into prompt | customers, internal operators |
| RISK-SEC | Security and credentials | secret exposure, stale credential cache, unauthorized admin action, scanner finding | HurricaneSoft, customers |
| RISK-LEGAL | Legal and external claims | EU role misclassification, unsupported conformity/certification claim, proprietary text misuse | HurricaneSoft, customers, EU users |
| RISK-OPS | Operations and release | failed job, broken release gate, unreplayable run artifact, rollback failure | HurricaneSoft |
| RISK-SUP | Suppliers and third parties | Bedrock cost/provider change, data-provider outage, legal-source unavailability | HurricaneSoft, customers |

## Draft Risk Register

| Risk ID | Source | Scenario | Affected parties | Owner | Due date | Inherent | Controls | Treatment | Residual | Status | Evidence URI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AIMS-RISK-0001 | #1340 | Rich market snapshots may collapse into narrow price-only evidence, hiding diverse source kinds. | customers, analysis reviewer | Product owner pending | pending | 4 x 4 = 16 | source-kind distribution and rich/sparse regression tests proposed | mitigate through implementation issue #1340 | not scored; control not verified | open | `https://github.com/cancleeric/trustforge/issues/1340` |
| AIMS-RISK-0002 | #1264 | EU operator role, risk classification, or EN 18286 readiness may be asserted before licensed source and counsel approval. | HurricaneSoft, EU users, customers | Compliance Counsel pending | pending legal source access | 3 x 5 = 15 | EU overlay marks licensed text and counsel disposition blockers | avoid unsupported claims; continue gap assessment | not scored; legal approval pending | blocked | `docs/aims/03-eu-ai-act/en-18286-qms-overlay.md` |
| AIMS-RISK-0003 | #1242 | AIMS personnel may rely on draft documents as completed training or approved competence evidence. | HurricaneSoft, customers | AIMS manager pending | pending | 3 x 4 = 12 | competency/training register separates planned, completed and verified | mitigate through role assignment and training evidence | not scored; training not complete | open | `docs/aims/05-support/competency-and-training-register.md` |
| AIMS-RISK-0004 | #1245 | KPI or CAPA status may be reported as passing when evidence is missing. | HurricaneSoft, customers | AIMS manager pending | pending | 3 x 4 = 12 | monitoring rules require `not measured`; CAPA requires owner and due date | mitigate through management-review pack | not scored; baseline pending | open | `docs/aims/08-measurement/kpi-and-monitoring-register.md`; `docs/aims/10-capa/capa-and-management-review.md` |
| AIMS-RISK-0005 | #1406 | Dual-asset comparison may spend for two formal child analyses without atomic budget admission or evidence-bound synthesis. | HurricaneSoft, customers | Engineering owner pending | pending implementation PR | 3 x 5 = 15 | issue requires all-or-none budget admission and evidence/claim-ID bounded synthesis | mitigate in feature implementation; security/cost review required | not scored; implementation pending | open | `https://github.com/cancleeric/trustforge/issues/1406` |

## Risk Acceptance Rules

- Every P0/P1 accepted risk requires owner, due date, accountable-executive approval, review date, and revocation condition.
- Security risks additionally require CISO review; legal/external-claim risks require Compliance Counsel review.
- Lack of evidence is never a reason to mark a control not applicable. It remains a gap until evidence exists or a documented, approved rationale excludes it.
- Accepted risks must be re-opened when the triggering assumption changes, evidence expires, or a related incident/finding occurs.

## Review Blockers

- AIMS governance scope, role assignments, approval authority, and risk-acceptance thresholds remain draft.
- Compliance Counsel has not approved EU intended purpose, operator role, risk classification, EN 18286 source status, or public claim wording.
- Independent reviewer, harper/CISO, gray/CPO, and final adversarial review remain pending before this draft can become effective.
