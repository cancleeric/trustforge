# EN 18286／EU AI Act／ISO 42001／TrustForge Crosswalk

> Document ID: AIMS-EU-MAP-001
>
> Version: 0.1-draft
>
> Status: Draft — awaiting licensed EN 18286 text — non-effective
>
> Parent tracking issue: #1264; this PR-A slice: #1265

## Use constraints

- Do not reconstruct EN 18286 clauses from search snippets or secondary commentary.
- Populate EN clause identifiers and normative language only from a company-licensed authoritative copy.
- A mapped document is not evidence that a control is implemented, verified, approved or effective.
- Record evidence against an exact version or commit and preserve unresolved gaps.
- Artifact paths below are unverified observations only. Existence, content, activation, ownership and effectiveness remain pending until checked against an exact commit and evidence record.
- Do not assign `Gap`, `Partial`, `Implemented` or `Verified` before the obligated actor and applicability are approved.

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

Effective dates are planning assumptions only: Article 113's general application date is 2026-08-02, while Article 6(1) and corresponding obligations are planned for 2027-08-02. Every row remains subject to re-verification for amendments, transitional provisions and Commission action before reliance.

| EN 18286 clause | EU AI Act area | Applicability | Obligated actor | Effective-date checkpoint | ISO/IEC 42001／AIMS area | Unverified artifact observation | Maturity | Owner／next action |
|---|---|---|---|---|---|---|---|---|
| Authoritative text pending | Article 9 risk management | Conditional; classification pending | High-risk provider; role pending | 2026-08-02; reverify | Risk and objectives | Issue #1244 is referenced; content／completion unverified | Not assessed | Legal applicability, then exact-version evidence review |
| Authoritative text pending | Article 10 data governance | Conditional; classification and training-technique distinction pending | High-risk provider; role pending | 2026-08-02; reverify | Lifecycle and operational controls | PII-boundary and source-record claims unverified; not Article 10 evidence | Not assessed | Determine training technique and applicable paragraphs |
| Authoritative text pending | Articles 11–15 | Conditional; classification pending | High-risk provider; role pending | 2026-08-02; reverify | Documentation, records, transparency, oversight, assurance | Distributed docs／logs／tests are alleged; exact artifacts and activation unverified | Not assessed | Verify artifact, commit, owner, activation and effectiveness |
| Authoritative text pending | Article 17 QMS | Conditional; classification pending | High-risk provider; role pending | 2026-08-02; reverify | Scope, policy, roles, document control | `docs/aims/01-scope/` and `02-policy/` paths observed only | Not assessed | Confirm role and applicability before maturity scoring |
| Authoritative text pending | Article 25 provider transition | Conditional on rebranding, substantial modification or intended-purpose change | Actor becoming provider; pending | 2026-08-02; reverify | Change and supplier controls | White-label／modification controls unverified | Not assessed | Separate provider transition from misuse records |
| Authoritative text pending | Article 26 deployer duties | Conditional; deployer and use pending | Deployer; pending | 2026-08-02; reverify | Operational monitoring and escalation | No exact-version deployer evidence verified | Not assessed | Map monitoring, logs, escalation and instructions interfaces |
| Authoritative text pending | Article 50 transparency | Independent feature/output/deployment assessment pending | Provider and/or deployer by paragraph; pending | 2026-08-02; reverify | Communication and output controls | Output modalities and marking/disclosure behavior unverified | Not assessed | Complete standalone Article 50 assessment |
| Authoritative text pending | Article 72 post-market monitoring | Conditional; classification pending | High-risk provider; role pending | 2026-08-02; reverify | Measurement, review and CAPA | Issue #1245 referenced; content／completion unverified | Not assessed | Confirm actor, scope and exact-version PMS evidence |
| Authoritative text pending | Article 73 serious incident reporting | Conditional; classification pending | Provider; role pending; other actors need escalation interface | 2026-08-02; reverify | Incident and corrective action | EU workflow existence unverified | Not assessed | Define actor-specific detection, escalation and reporting |
| Authoritative text pending | Article 6(1) and corresponding obligations | Conditional on Annex I / safety-component facts | Provider and other applicable actors; pending | 2027-08-02; reverify | Classification and readiness | Regulated-product facts unverified | Not assessed | Reverify legal text, amendments and Commission action |
| Authoritative text pending | Conformity, registration and CE | Conditional; path and role pending | Applicable economic operator; pending | Depends on classification; reverify | Readiness and SoA | Issue #1246 referenced; content／completion unverified | Not assessed | Keep claims blocked pending approved legal path |

## Required completion evidence

1. Licensed standard identifier, edition, language, source and usage authority.
2. Compliance Counsel clause interpretation and applicability disposition.
3. CPO approval of intended purpose and product boundaries.
4. CISO approval of security, resilience, logging, supplier and incident controls.
5. Exact-version control implementation and independent verification evidence.
6. Complete unresolved-gap and non-applicability rationale register.
7. Final management decision that does not overstate certification or conformity.
