| Field | Value |
| --- | --- |
| Document ID | AIMS-MEASURE-KPI-001 |
| Version / status | 0.1-draft / draft, unapproved, non-effective |
| Owner / approver | CEO assignment pending / CEO approval pending |
| Approval record / effective date | pending / not-applicable (draft) |
| Review cadence / next review | set on approval / set on approval |
| Classification | internal-draft |
| Change summary / supersedes | establish AIMS KPI and monitoring register draft / not-applicable (initial draft) |
| Repository path | `docs/aims/08-measurement/kpi-and-monitoring-register.md` |

# KPI And Monitoring Register

This register is a readiness draft for issue #1245. KPI rows are not operating evidence until the owner, source, baseline, target, collection method, and approval record are completed.

| KPI ID | Objective | Formula | Source | Baseline | Target | Owner | Frequency | Missing-data rule | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| KPI-EVID-001 | Evidence traceability completeness | runs with report/evidence/timeline identity match divided by sampled formal runs | replay sample and report artifacts | pending | pending CEO-approved threshold | Engineering owner pending | per release or management review | mark `not measured`; do not infer pass | planned |
| KPI-SRC-001 | Source degradation honesty | degraded/unavailable sources disclosed divided by degraded/unavailable sources observed in sampled runs | runtime status and report limits | pending | pending | Product owner pending | per release | mark sample invalid if runtime evidence missing | planned |
| KPI-SEC-001 | Open high-severity security findings | count of unresolved approved HIGH/CRITICAL findings | security scan reports and issue tracker | pending | 0 unresolved approved critical; P0 threshold pending | Security owner pending | weekly during active release | mark unknown when scanner mode/version unavailable | planned |
| KPI-COST-001 | Cost-gated provider calls | metered provider calls divided by cost-bearing provider calls | cost ledger and job records | pending | 100% for production cost-bearing calls | Engineering owner pending | per release/canary | fail closed when ledger unavailable | planned |
| KPI-CAPA-001 | Overdue P0 CAPA count | count of open P0 CAPA past due date | CAPA register | pending | 0 | Accountable executive pending | management review | unknown if CAPA register not approved | planned |

## Monitoring Rules

- KPI values must cite evidence that a reviewer can replay without exposing secrets.
- Unknown or unavailable evidence is recorded as `not measured`, never as a passing value.
- Baselines and targets require owner approval before external reporting.
- KPI changes require document-control review and an updated management review pack.
