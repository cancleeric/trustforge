| Field | Value |
| --- | --- |
| Document ID | AIMS-LIFE-LCM-001 |
| Version / status | 0.1-draft / draft, unapproved, non-effective |
| Owner / approver | CEO assignment pending / CEO approval pending |
| Approval record / effective date | pending / not-applicable (draft) |
| Review cadence / next review | set on approval / set on approval |
| Classification | internal-draft |
| Change summary / supersedes | establish AI lifecycle control matrix draft / not-applicable (initial draft) |
| Repository path | `docs/aims/06-lifecycle/lifecycle-control-matrix.md` |

# Lifecycle Control Matrix

This document is a readiness draft for issue #1243. It records intended lifecycle controls and evidence expectations only. It does not certify that any control is implemented or effective.

## Status Semantics

| Status | Meaning |
| --- | --- |
| planned | Control is designed but lacks approved operating evidence. |
| partial | Some evidence exists, but approval, coverage, or effectiveness evidence is incomplete. |
| implemented | Approved operating evidence exists and can be independently replayed. |
| not-applicable | Scope exclusion has an approved rationale and approver. |

## Lifecycle Controls

| ID | Stage | Owner | Trigger | Input | Activity | Output | Evidence URI | Exception path | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LIFE-DSN-001 | design | Product owner | new or changed intended purpose | approved scope, affected-party record | review intended purpose, misuse boundary, operator role, and user impact before implementation | design review record and residual open questions | `docs/aims/01-scope/scope.md`; `docs/aims/03-eu-ai-act/applicability-and-classification.md` | escalate unresolved legal/product classification to CEO and Compliance Counsel | partial |
| LIFE-DATA-001 | data acquisition | Engineering owner | new source, connector, cache, or evidence class | source card, PIT requirements, credentials boundary | verify provenance, visible-at semantics, degradation behavior, and sensitive-field handling | source readiness disposition | `docs/technical-docs/06-data-flow.md`; `docs/technical-docs/00-evidence-map.md` | disable source or mark unavailable until evidence is complete | partial |
| LIFE-DEV-001 | development | Engineering owner | code or contract change | issue, branch, tests, review scope | implement through branch review, focused tests, diff hygiene, and traceability to issue | reviewable PR with verification | GitHub PR evidence URI pending per change | block merge until review gate passes | planned |
| LIFE-VAL-001 | validation | Independent reviewer pending | release candidate or control change | test matrix, fixtures, expected outcomes | replay representative formal analysis flow and compare report/evidence/timeline identity | validation report with deviations | `docs/technical-docs/00-evidence-map.md` | create defect issue for any failed claim, export, or runtime truth contract | planned |
| LIFE-REL-001 | release | Accountable executive | approved release candidate | PR approvals, checks, rollback plan | confirm approvals, residual risk, rollout and rollback path | release decision record | release evidence URI pending | hold release when approval or rollback evidence is missing | planned |
| LIFE-OPS-001 | operation | Engineering owner | scheduled or manual formal run | run identity, budget, source status, model/config state | operate within budget and security gates, record degraded sources and report limits | run record and user-visible limits | runtime status/report evidence URI pending | fail closed for unavailable mandatory control, otherwise disclose partial result | partial |
| LIFE-MON-001 | monitoring | Security owner | alert, anomaly, stale source, cost threshold | telemetry, logs, ledger, scanner findings | triage severity, affected scope, and containment decision | monitoring finding or no-action record | `docs/security/` scan reports where applicable | incident path for material safety/security issue | planned |
| LIFE-INC-001 | incident | Security owner | material security, safety, data, or evidence integrity event | incident facts, affected assets, user impact | contain, classify, assign owner, preserve evidence, and define notification boundary | incident record linked to risk/CAPA | incident evidence URI pending | CEO/legal escalation for external notification or credential rotation | planned |
| LIFE-CHG-001 | change | Engineering owner | material model, prompt, source, policy, or deployment change | change request, risk link, test plan | assess effect on intended purpose, evidence contracts, suppliers, and residual risk | approved/rejected change record | change evidence URI pending | exception record when normal gate cannot be completed | planned |
| LIFE-RET-001 | retirement | Accountable executive | feature/source/model retirement | affected reports, retention rule, replacement plan | remove or disable capability, preserve required evidence, update docs and user-facing limits | retirement record | retirement evidence URI pending | extend support only with risk acceptance and owner | planned |

## Human Oversight

| Decision point | Human authority | Intervention authority | Escalation | Stop condition | Review evidence |
| --- | --- | --- | --- | --- | --- |
| Intended purpose or EU AI Act role changes | Product owner, Compliance Counsel, CEO | block implementation or external claims | CEO for unresolved classification | ambiguous role/risk classification | classification record |
| High-impact report release | Product owner and accountable executive | require manual review or abstain | CEO if business pressure conflicts with evidence limits | missing traceability or unsupported conclusion | report/evidence replay |
| Security or secret finding | Security owner and CEO | contain, disable connector, or request authorized rotation | CEO/legal for external exposure | suspected live credential or material exposure | incident/CAPA record |
| Cost-bearing autonomous job | Engineering owner and CEO | disable job or cap at zero | CEO for production re-enable | unmetered Bedrock/provider spend | cost ledger and canary evidence |

## Incident, Exception, And Change Traceability

Every lifecycle incident, exception, and material change must link to:

- One affected risk or `risk-link-pending` while AIMS-RISK is still awaiting approval.
- One affected asset, source, model, or control.
- One owner and due date.
- One evidence URI that a reviewer can replay without private secrets.
- One CAPA entry when containment alone does not remove recurrence risk.

## Formal Analysis Tabletop Draft

| Field | Planned record |
| --- | --- |
| Scenario | Formal BTC/ETH/SOL analysis run with report, evidence, timeline, and source status. |
| Expected result | Run identity remains consistent across report/evidence/export; unavailable sources are disclosed rather than fabricated. |
| Actual result | pending tabletop execution. |
| Deviation record | pending. |
| Evidence URI | pending repository-local replay artifact. |
| Reviewer | independent reviewer pending. |

This tabletop must remain `planned` until a reviewer can replay the evidence. No control is marked complete by this document alone.
