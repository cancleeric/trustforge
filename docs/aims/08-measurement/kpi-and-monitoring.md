# AIMS 量測與監測草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-MEASURE-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 AIMS Manager 指派／待 CEO 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立 KPI、monitoring 與缺值規則草案／not-applicable（初版） |
| Repository path | `docs/aims/08-measurement/kpi-and-monitoring.md` |

本文件回應 #1245。KPI 不得捏造 baseline 或 target；缺值必須按規則揭露。

| KPI ID | Metric | Formula | Source | Baseline | Target | Owner | Frequency | Missing-data rule | Evidence URI | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| AIMS-KPI-0001 | Evidence URI completeness | controls with reviewer-repeatable evidence URI / total controls | SoA and control matrices | pending | pending CEO approval | pending AIMS Manager | monthly | missing evidence counts as gap unless approved not-applicable | `docs/aims/soa/statement-of-applicability.md` | 僅計劃 |
| AIMS-KPI-0002 | P0 overdue CAPA | count of open P0 CAPA past due date | CAPA register | pending | 0 | pending CEO/CISO | weekly | unknown P0 due date counts as overdue | `docs/aims/10-capa/capa-and-management-review.md` | 僅計劃 |
| AIMS-KPI-0003 | Audit finding closure timeliness | findings closed by due date / findings due in period | audit finding log | pending | pending CEO approval | pending independent auditor | per audit cycle | no due date counts as overdue | `docs/aims/09-audit/audit-programme.md` | 僅計劃 |
| AIMS-KPI-0004 | Source-kind distribution reporting | multi-source analysis reports with distribution / multi-source analysis reports | analysis report evidence | pending | pending CPO approval | pending product owner | monthly | sparse-data abstain reports tracked separately | pending run IDs | 僅計劃 |

## Monitoring events

| Event | Trigger | Owner | Required action | Evidence |
|---|---|---|---|---|
| KPI missing source | KPI source query unavailable or not defined | AIMS Manager | mark gap; do not impute value | management-review pack |
| P0 CAPA overdue | current date past due date | CEO/CISO | escalation and containment review | CAPA record |
| Unsupported conformity claim found | docs/UI/sales text implies certification or conformity without approval | CPO/Compliance Counsel | stop external use, open CAPA | issue/CAPA evidence |
| Source-kind imbalance | rich snapshot output lacks representative source kinds | product owner | review extraction behavior and report exclusion rationale | run ID and report |
