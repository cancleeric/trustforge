# AIMS Statement of Applicability 與 Readiness 草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-SOA-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 compliance owner 指派／待 CEO、Compliance Counsel、CISO、CPO 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立 SoA、traceability 與 readiness gap review 草案／not-applicable（初版） |
| Repository path | `docs/aims/soa/statement-of-applicability.md` |

本文件回應 #1246 並支援 #1264。Repo 不提交 ISO/IEC 42001 或 EN 18286 標準全文；合規 owner 必須以合法取得的最新版標準覆核後，才能把 control disposition 轉為有效。

## SoA skeleton

| SoA ID | Control / requirement area | Applicability | Rationale | Owner | Status | Evidence URI | Review dates |
|---|---|---|---|---|---|---|---|
| AIMS-SOA-0001 | Scope and document control | applicable draft | TrustForge AIMS docs require owner/status/evidence URI controls | pending AIMS Manager | 部分實作 | `docs/aims/README.md`; `docs/aims/02-policy/document-control.md` | pending |
| AIMS-SOA-0002 | Risk management | applicable draft | AI market-analysis, legal, security and supplier risks are in scope | pending risk owner | 僅計劃 | `docs/aims/03-risk/risk-methodology-and-register.md` | pending |
| AIMS-SOA-0003 | Impact assessment | applicable draft | output misuse, customers and EU users require impact review | pending CPO | 僅計劃 | `docs/aims/04-impact/impact-assessment.md` | pending |
| AIMS-SOA-0004 | Lifecycle operation controls | applicable draft | controls are needed from design through retirement | pending AIMS Manager | 僅計劃 | `docs/aims/06-lifecycle/lifecycle-control-matrix.md` | pending |
| AIMS-SOA-0005 | Supplier and source controls | applicable draft | models, cloud services and market data are material sources | pending supplier owner | 僅計劃 | `docs/aims/07-suppliers/supplier-and-source-cards.md` | pending |
| AIMS-SOA-0006 | Measurement, audit and CAPA | applicable draft | readiness needs KPI, audit independence and corrective action tracking | pending CEO | 僅計劃 | `docs/aims/08-measurement/kpi-and-monitoring.md`; `docs/aims/09-audit/audit-programme.md`; `docs/aims/10-capa/capa-and-management-review.md` | pending |
| AIMS-SOA-0007 | EU AI Act / EN 18286 overlay | conditional draft | role, classification and licensed EN text are pending | pending Compliance Counsel | 部分實作 | `docs/aims/03-eu-ai-act/README.md`; `docs/aims/03-eu-ai-act/crosswalk.md` | pending |

Excluded controls must have concrete rationale and approver. Lack of evidence alone is a gap, not a reason to mark a control not applicable.

## Traceability

| Control | Risk | Asset/source | Impact | Lifecycle | Audit/CAPA |
|---|---|---|---|---|---|
| AIMS-SOA-0002 | AIMS-RISK-0001..0004 | AI system inventory and supplier cards pending | AIMS-IMP-0001..0004 | AIMS-LIFE-* | AIMS-FIND-0001 / AIMS-CAPA-0001 |
| AIMS-SOA-0005 | AIMS-RISK-0001, AIMS-RISK-0002 | AIMS-SUP-* | AIMS-IMP-0001, AIMS-IMP-0002 | AIMS-LIFE-DAT-001, AIMS-LIFE-CHG-001 | pending |
| AIMS-SOA-0006 | AIMS-RISK-0004 | CAPA and audit records | AIMS-IMP-0004 | AIMS-LIFE-MON-001, AIMS-LIFE-INC-001 | AIMS-FIND-0001 / AIMS-CAPA-0001 |

## Readiness gap review

| Gap ID | Category | Gap | Blocker | Disposition |
|---|---|---|---|---|
| AIMS-GAP-0001 | Owner / approval | Most owners, approvers and review dates remain pending | CEO assignment and governance approval | 委託 gap assessment |
| AIMS-GAP-0002 | Legal text | EN 18286 licensed text is not acquired or mapped | company-licensed authoritative copy and Compliance Counsel | 委託 gap assessment |
| AIMS-GAP-0003 | Evidence | Many controls have schema but no replayable runtime evidence | lifecycle/tabletop/audit execution | 進入認證準備前補證 |
| AIMS-GAP-0004 | Independent review | Auditor, gray, harper, CEO and adversarial review evidence pending | reviewer appointment and exact-commit review | 委託 gap assessment |

Allowed dispositions are `aligned`, `委託 gap assessment`, and `進入認證準備`. This draft uses only gap-assessment and readiness language; it does not claim certification, conformity, presumption of conformity or CE readiness.
