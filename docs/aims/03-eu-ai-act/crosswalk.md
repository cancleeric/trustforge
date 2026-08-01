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

Effective dates are planning assumptions only. Regulation (EU) 2026/1744 amended Article 113 so that Chapter III Sections 1–3 apply from 2027-12-02 for Article 6(2)／Annex III high-risk systems and from 2028-08-02 for Article 6(1)／Annex I high-risk systems. The separate Article 50(2) transition and the amended timing for Articles 102–110 remain exact-text legal-verification checkpoints; this draft assigns neither an operative date. Every row remains subject to re-verification against the current consolidated text, transitional provisions and Commission action before reliance. The superseded 2026-08-02／2027-08-02 frame is historical only and is not an active checkpoint.

| EN 18286 clause | EU AI Act area | Applicability | Obligated actor | Effective-date checkpoint | ISO/IEC 42001／AIMS area | Unverified artifact observation | Maturity | Owner／next action |
|---|---|---|---|---|---|---|---|---|
| Authoritative text pending | Articles 9–17 | Conditional; Article 6(2)／Annex III classification pending | High-risk provider; role pending | 2027-12-02 if Article 6(2)／Annex III; reverify | Risk, lifecycle, documentation and QMS | Distributed docs／logs／tests are alleged; exact artifacts and activation unverified | Not assessed | Determine classification, technique-specific Article 10 scope and exact-version evidence |
| Authoritative text pending | Article 25 provider transition | Conditional high-risk scope; statutory Article 25(1) condition pending, including changed intended purpose making a previously non-high-risk system high-risk under Article 25(1)(c) | Actor becoming provider; pending | 2027-12-02 for Article 6(2)／Annex III path; 2028-08-02 for Article 6(1)／Annex I path; reverify | Change and supplier controls | White-label／modification controls unverified | Not assessed | Verify high-risk scope and separate provider transition from misuse records |
| Authoritative text pending | Article 26 deployer duties | Conditional; deployer, use and high-risk path pending | Deployer; pending | 2027-12-02 for Article 6(2)／Annex III path; 2028-08-02 for Article 6(1)／Annex I path; reverify | Operational monitoring and escalation | No exact-version deployer evidence verified | Not assessed | Map monitoring, logs, escalation and instructions interfaces |
| Authoritative text pending | Article 50 transparency | Independent feature/output/deployment assessment pending | Provider and/or deployer by paragraph; pending | Article 50(2) transition: exact amended date pending legal verification | Communication and output controls | Output modalities and marking/disclosure behavior unverified | Not assessed | Verify Regulation (EU) 2026/1744 transition and complete standalone assessment |
| Authoritative text pending | Articles 72–73 | Conditional; classification pending | High-risk provider／provider; other actors need escalation interface | 2027-12-02 for Article 6(2)／Annex III path; 2028-08-02 for Article 6(1)／Annex I path; reverify | Measurement, CAPA and incident action | PMS and EU reporting workflow existence unverified | Not assessed | Confirm actor, scope, escalation and exact-version evidence |
| Authoritative text pending | Article 6(1) and corresponding obligations | Conditional on Annex I／safety-component facts | Provider and other applicable actors; pending | 2028-08-02; reverify | Classification and readiness | Regulated-product facts unverified | Not assessed | Reverify amended legal text and Commission action |
| Authoritative text pending | Articles 102–110 amendments and penalties | Conditional on article, actor and infringement facts | Applicable economic operator; pending | Exact amended date pending legal verification; do not reuse the historical general date | Governance, enforcement and readiness | No exact-version enforcement assessment verified | Not assessed | Verify Regulation (EU) 2026/1744 and consolidated Article 113 |
| Authoritative text pending | Conformity, registration and CE | Conditional; path and role pending | Applicable economic operator; pending | Depends on classification; reverify | Readiness and SoA | Issue #1246 referenced; content／completion unverified | Not assessed | Keep claims blocked pending approved legal path |

## Required completion evidence

1. Licensed standard identifier, edition, language, source and usage authority.
2. Compliance Counsel clause interpretation and applicability disposition.
3. CPO approval of intended purpose and product boundaries.
4. CISO approval of security, resilience, logging, supplier and incident controls.
5. Exact-version control implementation and independent verification evidence.
6. Complete unresolved-gap and non-applicability rationale register.
7. Final management decision that does not overstate certification or conformity.
