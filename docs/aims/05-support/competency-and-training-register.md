# AIMS Competency And Training Register

| Field | Value |
| --- | --- |
| Document ID | AIMS-SUPPORT-COMP-001 |
| Version / status | 0.2-draft / draft, unapproved, non-effective |
| Owner / approver | AIMS manager pending / CEO approval pending |
| Approval record / effective date | pending / not applicable (draft) |
| Review cadence / next review | annual after approval / set on approval |
| Classification | internal draft; no personal training records |
| Change summary / supersedes | expands issue #1242 competency, training, owner and evidence fields / v0.1 draft |
| Repository path | `docs/aims/05-support/competency-and-training-register.md` |

This register is a readiness artifact for issue #1242. It defines the competence and training evidence TrustForge must control before an AIMS role can be treated as active. It does not certify competence, completion, or ISO/IEC 42001 conformance.

## Role Competency Matrix

| AIMS role | Necessary competence | Current evidence | Gap | Reinforcement plan | Owner | Review date | Status | Evidence URI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Accountable executive | AIMS scope, policy approval, resource allocation, risk acceptance limits, external-claim rules | Governance draft metadata and policy drafts exist | Named executive approval and risk-acceptance authority still pending | CEO to approve RACI, accepted-risk thresholds, and review cadence | CEO | pending approval | planned | `docs/aims/02-policy/roles-raci-and-risk-acceptance.md` |
| AIMS manager | Document control, evidence URI discipline, status vocabulary, management-review pack assembly | Draft AIMS index and document-control rules exist | No appointed manager or approved document register | Assign AIMS manager and require two sampled document lifecycle traces | CEO | pending approval | planned | `docs/aims/README.md`; `docs/aims/05-support/document-lifecycle-trace.md` |
| Product owner | Intended purpose, affected parties, lifecycle decisions, user-facing statement review | AI system inventory and impact drafts exist | Product intended-purpose facts require approved owner review | Product owner signs intended-purpose and affected-party inventory | CPO / product owner | pending approval | planned | `docs/aims/02-policy/ai-system-inventory.md`; `docs/aims/04-impact/impact-assessment.md` |
| Engineering owner | AI lifecycle controls, validation gates, evidence/run identity, change traceability | Lifecycle draft and repository tests exist | Runtime evidence replay and release gate ownership not approved | Map lifecycle control rows to tests or replayable artifacts before release review | Engineering owner | pending approval | planned | `docs/aims/06-lifecycle/lifecycle-control-matrix.md` |
| Security owner | Threat review, secret handling, incident containment, supplier/security evidence review | Security issues and CISO gate rules exist | Security findings are not yet folded into approved AIMS risk/CAPA records | Security owner reviews P0/P1 risk rows and scanner-disposition policy | CISO / security owner | pending approval | planned | `docs/aims/03-risk/risk-methodology-and-register.md`; `docs/aims/10-capa/capa-and-management-review.md` |
| Compliance reviewer | External claims, EU AI Act role/risk classification, legal text source control | EU AI Act overlay draft exists with blockers | Licensed EN 18286 text and counsel disposition are pending | Counsel validates source list and forbidden public-claim language before approval | Compliance Counsel | pending legal source access | blocked | `docs/aims/03-eu-ai-act/en-18286-qms-overlay.md` |
| Independent auditor | Audit planning, sampling, independence, finding severity and CAPA linkage | Audit programme template exists | No independent auditor assignment or completed sample | Assign auditor not responsible for authored controls; execute first sample audit | CEO / independent auditor | pending assignment | planned | `docs/aims/09-audit/audit-programme-and-report.md` |

## Training Register

Training status is controlled separately from training material. A document, slide deck, or README may be training material, but it is not a completed or verified training record by itself.

| Training ID | Audience | Topic | Material URI | Planned | Completed | Verified | Verification evidence | Owner | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AIMS-TRN-001 | All AIMS roles | AIMS status vocabulary, draft/non-effective boundaries, evidence URI rules | `docs/aims/README.md` | yes | no | no | pending attendance and quiz or sign-off record | AIMS manager | planned |
| AIMS-TRN-002 | Product, compliance, executive | External ISO/EU AI Act statement restrictions | `docs/aims/05-support/document-and-communication-control.md` | yes | no | no | pending counsel-approved acknowledgement record | Compliance Counsel | planned |
| AIMS-TRN-003 | Engineering, security, product | Lifecycle evidence, run identity, source/supplier traceability | `docs/aims/06-lifecycle/lifecycle-control-matrix.md`; `docs/aims/07-suppliers/supplier-and-source-cards.md` | yes | no | no | pending tabletop attendance and reviewer observation | Engineering owner | planned |
| AIMS-TRN-004 | Audit, security, executive | Audit finding, CAPA, management-review flow | `docs/aims/09-audit/audit-programme-and-report.md`; `docs/aims/10-capa/capa-and-management-review.md` | yes | no | no | pending sample finding and CAPA closure evidence | Independent auditor | planned |

## Review Notes

- A role cannot move from `planned` to `active` until the owner, review date, and replayable evidence URI are filled.
- Completed training requires a participant record and completion timestamp; verified training additionally requires objective evidence such as quiz, observed tabletop, signed attestation, or reviewed work product.
- Privacy-sensitive individual training records must be stored outside this public repository; this document should cite only non-sensitive evidence URIs or redacted references.
