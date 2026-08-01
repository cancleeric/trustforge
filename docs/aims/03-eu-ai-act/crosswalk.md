# EN 18286／EU AI Act／ISO 42001／TrustForge Crosswalk

> Document ID: AIMS-EU-MAP-001
>
> Version: 0.1-draft
>
> Status: Draft — awaiting licensed EN 18286 text — non-effective
>
> Tracking issue: #1264

## Use constraints

- Do not reconstruct EN 18286 clauses from search snippets or secondary commentary.
- Populate EN clause identifiers and normative language only from a company-licensed authoritative copy.
- A mapped document is not evidence that a control is implemented, verified, approved or effective.
- Record evidence against an exact version or commit and preserve unresolved gaps.

## Mapping states

| State | Meaning |
|---|---|
| Awaiting licensed text | EN clause cannot yet be mapped reliably |
| Not assessed | Authoritative requirement is available but assessment has not begun |
| Gap | No adequate control or evidence exists |
| Partial | Some control or evidence exists but the requirement is not fully satisfied |
| Implemented | Control exists but independent verification or approval is incomplete |
| Verified | Evidence was independently checked against an exact version |
| Not applicable | Approved rationale establishes non-applicability |

## Preliminary framework

| EN 18286 clause | EU AI Act area | ISO/IEC 42001／AIMS area | TrustForge artifact or evidence | State | Owner／next action |
|---|---|---|---|---|---|
| Awaiting licensed text | Article 17 quality management system | AIMS scope, policy, roles, document control | `docs/aims/01-scope/`, `docs/aims/02-policy/` | Partial | Obtain licensed text; Compliance review |
| Awaiting licensed text | Article 9 risk management | Risk and objectives | Issue #1244 | Gap | Execute AIMS-RISK with EU overlay |
| Awaiting licensed text | Article 10 data and data governance | Lifecycle and operational controls | Production-only PII boundary; source and Evidence records | Partial | Define dataset quality and provenance controls |
| Awaiting licensed text | Article 11 and Annex IV technical documentation | Documented information | Architecture, QA and runbook documents are distributed | Gap | Create release-bound technical file index |
| Awaiting licensed text | Article 12 record keeping | Operational controls and evidence | Execution logs and Evidence exist | Partial | Approve retention, integrity and access controls |
| Awaiting licensed text | Article 13 transparency and instructions | Communication and lifecycle controls | Product documentation exists | Gap | Create controlled EU instructions for use |
| Awaiting licensed text | Article 14 human oversight | Roles and operational controls | Human approval boundaries exist | Partial | Define and test oversight measures |
| Awaiting licensed text | Article 15 accuracy, robustness and cybersecurity | Objectives, lifecycle, monitoring | Tests and security controls exist | Partial | Define regulatory thresholds and release evidence |
| Awaiting licensed text | Article 72 post-market monitoring | Measurement, audit, management review and CAPA | Issue #1245 | Gap | Create PMS and feedback loop |
| Awaiting licensed text | Article 73 serious incident reporting | Incident and corrective action | No EU-specific workflow | Gap | Create reporting classification and playbook |
| Awaiting licensed text | Operator and supplier obligations | Interested parties and supplier controls | AI system inventory is a foundation | Partial | Complete operator and supplier registers |
| Awaiting licensed text | Conformity assessment, registration and CE | Readiness and SoA | Issue #1246 | Gap | Keep claims blocked pending legal path |

## Required completion evidence

1. Licensed standard identifier, edition, language, source and usage authority.
2. Compliance Counsel clause interpretation and applicability disposition.
3. CPO approval of intended purpose and product boundaries.
4. CISO approval of security, resilience, logging, supplier and incident controls.
5. Exact-version control implementation and independent verification evidence.
6. Complete unresolved-gap and non-applicability rationale register.
7. Final management decision that does not overstate certification or conformity.
