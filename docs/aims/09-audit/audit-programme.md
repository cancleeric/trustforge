# AIMS 內稽計畫草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-AUDIT-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 independent auditor 指派／待 CEO 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立 audit programme、finding schema 與獨立性規則草案／not-applicable（初版） |
| Repository path | `docs/aims/09-audit/audit-programme.md` |

若無獨立 auditor，本文件只能作為 readiness exercise，不得宣稱 internal audit completed。Auditor 不得稽核自己設計或實作的控制。

## Programme

| Cycle ID | Scope | Criteria | Sampling | Method | Schedule | Auditor | Independence record | Status |
|---|---|---|---|---|---|---|---|---|
| AIMS-AUD-2026-DRAFT | GOV/RISK/LIFE/MEASURE/CAPA/SoA draft readiness | repo-local AIMS metadata, owner/status/evidence URI, unsupported claims prohibition | sample all P0/P1 risks, all P0 CAPA, and at least 20% draft controls | document review and replay of evidence URI | pending CEO approval | pending | pending; no independent auditor appointed | 僅計劃 |

## Finding schema

| 欄位 | 說明 |
|---|---|
| Finding ID | stable ID |
| Source | audit cycle, control ID, risk ID or evidence URI |
| Evidence | reviewer-repeatable URI |
| Severity | P0/P1/P2/P3 |
| Owner | named accountable owner |
| Due date | required for all findings |
| Status | open, contained, corrected, effectiveness-review, closed, rejected-with-approval |
| CAPA link | required for P0/P1 and repeated findings |

## Draft finding

| Finding ID | Evidence | Severity | Owner | Due date | Status | CAPA link |
|---|---|---|---|---|---|---|
| AIMS-FIND-0001 | `docs/aims/README.md` plus draft documents added for #1244/#1243/#1245/#1246/#1264 | P2 | pending AIMS Manager | pending | open readiness exercise | `docs/aims/10-capa/capa-and-management-review.md#draft-capa-register` |
