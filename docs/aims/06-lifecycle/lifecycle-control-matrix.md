# AIMS Lifecycle Control Matrix

| Field | Value |
| --- | --- |
| Document ID | AIMS-LIFE-001 |
| Version / status | 0.2-draft / draft, unapproved, non-effective |
| Owner / approver | Engineering owner pending / CEO, CPO, CISO approval pending |
| Approval record / effective date | pending / not applicable (draft) |
| Review cadence / next review | per material AI-system change and annually / set on approval |
| Classification | internal draft |
| Change summary / supersedes | expands issue #1243 lifecycle owners, evidence URIs, status and gap review / v0.1 draft |
| Repository path | `docs/aims/06-lifecycle/lifecycle-control-matrix.md` |

This matrix describes lifecycle controls for TrustForge market-analysis AI behavior. It is a controlled draft and must not be used as a certification, conformity, or operational-effectiveness claim.

## Lifecycle Controls

| Stage | Control ID | Required control | Owner | Status | Evidence URI | Completion gap |
| --- | --- | --- | --- | --- | --- | --- |
| Design | AIMS-LIFE-DES-001 | Intended purpose, affected parties, prohibited claims, and user-impact assumptions are recorded before implementation | Product owner | partial implementation | `docs/aims/02-policy/ai-system-inventory.md`; `docs/aims/04-impact/impact-assessment.md` | owner approval and legal classification pending |
| Data acquisition | AIMS-LIFE-DAT-001 | Source kinds, supplier identity, freshness, confidence, and exclusion rules are preserved through report generation | Engineering owner | partial implementation | `docs/aims/07-suppliers/supplier-and-source-cards.md`; issue #1340 | representative-claim preservation tests still tracked separately |
| Development | AIMS-LIFE-DEV-001 | Changes link issue, branch, tests, evidence contract, and reviewer findings | Engineering owner | aligned draft | repository PR workflow and AGENTS gate | pre-push evidence must be attached per PR |
| Validation | AIMS-LIFE-VAL-001 | Regression tests cover sparse-data abstain behavior, rich-data multi-source behavior, and evidence identity | QA owner | partial implementation | `tests/` and issue-specific PR evidence | validation matrix not yet approved |
| Release | AIMS-LIFE-REL-001 | Release gate blocks unsupported external claims, missing reviewer evidence, and unresolved security/cost findings | Accountable executive | partial implementation | `.githooks/pre-push`; `.kiro/steering/pr-review-gate.md` | final production cutover outside this draft |
| Operation | AIMS-LIFE-OPS-001 | Runtime jobs preserve job ID, run ID, execution log identity, evidence URI, and source distribution | Engineering owner | partial implementation | issue #1406; API/report artifacts | dual-asset orchestration remains open |
| Monitoring | AIMS-LIFE-MON-001 | KPIs and source degradation are monitored with replayable evidence and explicit missing-data handling | AIMS manager | planned | `docs/aims/08-measurement/kpi-and-monitoring-register.md` | baselines and targets pending approval |
| Incident | AIMS-LIFE-INC-001 | Material evidence-integrity, security, or external-claim incidents open finding/CAPA and communication record | Security owner | planned | `docs/aims/10-capa/capa-and-management-review.md` | incident log and owner assignment pending |
| Change | AIMS-LIFE-CHG-001 | AI behavior changes require impact, risk, supplier, validation, and communication review before effective use | Engineering owner | aligned draft | this document; `docs/aims/03-risk/risk-methodology-and-register.md` | approval workflow pending |
| Retirement | AIMS-LIFE-RET-001 | Retired model/source/control artifacts keep evidence URI, obsolete marker, retention and user-impact disposition | AIMS manager | planned | `docs/aims/05-support/document-lifecycle-trace.md` | no retired artifact sample yet |

## Oversight Points

| Decision | Required reviewer | Evidence |
| --- | --- | --- |
| Pre-release analysis behavior change | Product owner and QA owner | test report, impact row, source-card update |
| Formal market-analysis output template change | Independent reviewer | golden fixture or replay artifact |
| EU AI Act, EN 18286, ISO certification, conformity, or CE-related statement | Compliance Counsel, CPO, CEO | legal source disposition and approved exact text |
| Security-sensitive lifecycle change | CISO / security owner | threat review and resolved findings |
| Cost-sensitive lifecycle change | Cost owner / Harper equivalent | budget admission evidence and cost limit review |

## Gap Review

| Gap ID | Category | Gap | Disposition | Blocker |
| --- | --- | --- | --- | --- |
| AIMS-LIFE-GAP-001 | Owner/status | Several lifecycle controls have draft owners, not approved named people | delegated gap assessment | CEO role assignments |
| AIMS-LIFE-GAP-002 | Evidence | Some controls cite planned issue/PR evidence instead of replayable runtime artifacts | certification preparation only | issue-specific implementation and tests |
| AIMS-LIFE-GAP-003 | External assessment | No independent auditor has validated lifecycle effectiveness | delegated gap assessment | independent reviewer assignment |
| AIMS-LIFE-GAP-004 | Public statements | External statements may overstate readiness if copied without status qualifiers | aligned with guardrail draft | Compliance Counsel approval |

## Tabletop Scenario

| Tabletop ID | Scenario | Expected evidence | Status |
| --- | --- | --- | --- |
| AIMS-TT-0001 | Formal BTC analysis has a rich snapshot but final report shows only narrow price-source claims | source-kind distribution, excluded-claim reason, risk row, regression test, reviewer finding disposition | planned; implementation issue #1340 remains separate |
