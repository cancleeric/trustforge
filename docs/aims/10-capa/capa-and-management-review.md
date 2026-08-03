# AIMS CAPA 與管理審查草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-CAPA-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 CEO 指派／待 CEO、CISO、CPO 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立 CAPA register、closure 與 management review pack 草案／not-applicable（初版） |
| Repository path | `docs/aims/10-capa/capa-and-management-review.md` |

本文件回應 #1245。任何 CAPA closure 都必須保留 containment、root cause、correction、corrective action、effectiveness review 與 closure approval；不得假結案或任意降級。

## Draft CAPA register

| CAPA ID | Source | Severity | Containment | Root cause | Correction | Corrective action | Effectiveness review | Closure approval | Owner | Due date | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| AIMS-CAPA-0001 | AIMS-FIND-0001 | P2 | Draft documents explicitly marked unapproved and non-effective | AIMS work packages lacked integrated traceability skeleton | Add index, risk, lifecycle, measurement, audit/CAPA and SoA skeletons | Complete independent review after dependencies merge and owners assigned | pending; cannot review effectiveness before approval and use | pending CEO/independent auditor | pending AIMS Manager | pending | open readiness exercise |

No P0 CAPA is recorded in this draft. If any P0 CAPA is opened later, missing owner or due date must count as overdue until corrected.

## Management review pack

| Review area | Required input | Decision needed | Evidence URI | Status |
|---|---|---|---|---|
| KPI | AIMS-KPI values and missing-data notes | approve targets, resource needs and corrective actions | `docs/aims/08-measurement/kpi-and-monitoring.md` | 僅計劃 |
| Risk | P0/P1 risks, accepted risks, residual-risk changes | accept, mitigate, avoid or transfer with owner and review date | `docs/aims/03-risk/risk-methodology-and-register.md` | 僅計劃 |
| Audit | findings, independence record, scope gaps | approve audit plan and CAPA requirements | `docs/aims/09-audit/audit-programme.md` | 僅計劃 |
| CAPA | overdue, effectiveness review, closure requests | approve closure or escalation | this document | 僅計劃 |
| Incidents | incident trends and unresolved containment | assign resource, customer/legal action | pending incident log | 僅計劃 |
| Resources | owner vacancies, tooling, legal text access | appoint owners and fund legal EN 18286 access | pending | 僅計劃 |

## Closure rules

- P0 CAPA overdue target is zero.
- CAPA cannot close without evidence URI for root cause, correction, corrective action and effectiveness review.
- Closure approver must be independent from the person who implemented the correction when feasible.
- Readiness exercises must remain labeled as readiness exercises until an independent auditor is appointed.
