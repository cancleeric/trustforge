# AIMS KPI And Monitoring Register

| Field | Value |
| --- | --- |
| Document ID | AIMS-MEASURE-KPI-001 |
| Version / status | 0.2-draft / draft, unapproved, non-effective |
| Owner / approver | AIMS manager pending / CEO approval pending |
| Approval record / effective date | pending / not applicable (draft) |
| Review cadence / next review | per release and management review / set on approval |
| Classification | internal draft |
| Change summary / supersedes | expands issue #1245 KPI, monitoring and missing-data controls / v0.1 draft |
| Repository path | `docs/aims/08-measurement/kpi-and-monitoring-register.md` |

This register is a measurement readiness draft. KPI rows are not operating evidence until owner, baseline, target, collection method, sampling scope, and approval record are complete.

## KPI Register

| KPI ID | Objective | Formula | Source | Baseline | Target | Owner | Frequency | Missing-data rule | Escalation | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| KPI-EVID-001 | Evidence traceability completeness | sampled formal runs with matching report ID, run ID, evidence URI and execution-log identity / sampled formal runs | replay sample report artifacts | pending | pending CEO-approved threshold | Engineering owner pending | per release | mark `not measured`; do not infer pass | open finding if production release relies on missing evidence | planned |
| KPI-SRC-001 | Source degradation honesty | degraded or unavailable sources disclosed / degraded or unavailable sources observed | runtime status and report limits | pending | pending product-approved threshold | Product owner pending | per release | sample invalid if runtime evidence missing | open CAPA after repeated undisclosed degradation | planned |
| KPI-DIV-001 | Representative source-kind preservation | reports with source-kind distribution and excluded-claim reasons / reports with rich multi-source snapshots | issue #1340 tests and replay artifacts | pending | pending | Product owner pending | per formal-analysis release | mark `not measured` until rich/sparse fixtures exist | risk AIMS-RISK-0001 | planned |
| KPI-SEC-001 | Open high-severity security findings | count of unresolved approved HIGH/CRITICAL findings | security scan reports and issue tracker | pending | 0 unresolved approved critical findings | Security owner pending | weekly during release work | scanner unavailable means `not measured`, not pass | block security-sensitive merge pending review | planned |
| KPI-COST-001 | Cost-gated provider calls | metered provider calls / cost-bearing provider calls | cost ledger and job records | pending | 100% production cost-bearing calls | Engineering owner pending | per release/canary | fail closed when ledger unavailable | cost review required | planned |
| KPI-CAPA-001 | Overdue P0/P1 CAPA count | open P0/P1 CAPA past due date | CAPA register | pending | 0 overdue | Accountable executive pending | management review | missing owner/due date counts as overdue | CEO escalation | planned |

## Monitoring Rules

- KPI values must cite replayable evidence that does not expose secrets or private training records.
- Unknown, stale, or unavailable evidence is recorded as `not measured`, never as `pass`.
- Baselines and targets require owner approval before external or management reporting.
- KPI changes require document-control review and inclusion in the next management-review pack.
- Repeated KPI failure, missing-data abuse, or unsupported pass reporting must open a finding and CAPA.
