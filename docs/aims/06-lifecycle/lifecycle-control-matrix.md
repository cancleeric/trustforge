# AIMS 生命週期控制矩陣草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-LIFE-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 AIMS Manager 指派／待 CEO、CPO、CISO 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立 AI lifecycle control matrix、oversight 與 tabletop 草案／not-applicable（初版） |
| Repository path | `docs/aims/06-lifecycle/lifecycle-control-matrix.md` |

本文件回應 #1243。所有控制皆為 draft control definition；除非 evidence URI 指向已核准且可重演的證據，狀態不得高於 `部分實作`。

| Control ID | Stage | Owner | Trigger | Input | Activity | Output | Evidence URI | Exception path | Status |
|---|---|---|---|---|---|---|---|---|---|
| AIMS-LIFE-DES-001 | Design | pending product owner | new feature, changed intended purpose, EU-facing deployment | requirements, intended-purpose draft, risk register | confirm purpose, prohibited uses, foreseeable misuse and oversight needs | design review record | pending | escalate to CPO/Compliance Counsel if purpose changes | 僅計劃 |
| AIMS-LIFE-DAT-001 | Data acquisition | pending data owner | new source, source contract change, stale-data incident | source card, license, trust score, retention rule | verify source kind, permitted use, freshness and exclusion conditions | approved source card | `docs/aims/07-suppliers/supplier-and-source-cards.md` | mark source `unknown/todo` and exclude from claims if unverifiable | 僅計劃 |
| AIMS-LIFE-DEV-001 | Development | pending engineering owner | code or prompt change affecting analysis behavior | issue, test plan, risk links | implement with tests and source-kind regression where applicable | PR and test evidence | pending PR URI | block release if evidence assembly regresses | 僅計劃 |
| AIMS-LIFE-VAL-001 | Validation | pending QA owner | release candidate or risk-triggered test | test inventory, representative snapshots | verify sparse abstain and rich multi-source behavior | validation report | pending | open CAPA for P0/P1 failure | 僅計劃 |
| AIMS-LIFE-REL-001 | Release | pending release owner | develop-to-main or production release | approvals, tests, SoA impacts | confirm no unsupported conformity claims and required reviewers | release checklist | pending | hold release; escalate missing security/legal approval | 僅計劃 |
| AIMS-LIFE-OPS-001 | Operation | pending operations owner | scheduled analysis, formal run, incident | run ID, snapshot metadata, monitoring signals | monitor freshness, source distribution and evidence completeness | operations log | pending | incident workflow if output lacks evidence or crosses threshold | 僅計劃 |
| AIMS-LIFE-MON-001 | Monitoring | pending AIMS Manager | KPI cadence or alert | KPI sources, audit findings, CAPA | update objectives and management-review pack | KPI report | `docs/aims/08-measurement/kpi-and-monitoring.md` | missing data counts as gap unless approved not-applicable | 僅計劃 |
| AIMS-LIFE-INC-001 | Incident | pending CISO/CPO | P0/P1 incident, legal/security claim concern | incident record, risk, asset, affected output | containment, triage, owner assignment, customer/legal escalation decision | incident record and CAPA link | pending | CEO escalation for unresolved P0 | 僅計劃 |
| AIMS-LIFE-CHG-001 | Change | pending engineering owner | model, data, supplier, purpose or high-risk trigger change | change request, risk and impact links | classify change, require reviewer set, update SoA | change decision | pending | Compliance Counsel review for EU role/classification changes | 僅計劃 |
| AIMS-LIFE-RET-001 | Retirement | pending product owner | feature/source/model retirement | asset record, customer impact, retention obligations | stop use, archive evidence, update docs and customers if needed | retirement record | pending | CISO/legal review if evidence retention affected | 僅計劃 |

## Human oversight

| Oversight point | Intervention authority | Escalation | Stop condition | Review condition |
|---|---|---|---|---|
| Pre-release analysis behavior change | product owner, QA owner | CPO/CISO for P1+ risk | missing tests, unsupported claims, unresolved P0/P1 | PR review and validation evidence |
| Formal market-analysis output | designated reviewer | CPO for product claim, CISO for security incident | source-kind distribution absent when rich snapshot exists; evidence URI missing | run report and snapshot metadata |
| EU AI Act or conformity-related statement | CPO and Compliance Counsel | CEO | no approved intended purpose, role or classification record | exact-commit legal/product approval |
| Incident or CAPA closure | CISO/CPO/CEO based on risk type | CEO for overdue P0 | no root cause, correction, corrective action or effectiveness review | CAPA closure approval |

## Tabletop replay

| Tabletop ID | Scenario | Expected | Actual | Deviation | Evidence URI | Status |
|---|---|---|---|---|---|---|
| AIMS-TT-0001 | Formal BTC analysis has rich snapshot but report shows narrow source kinds | reviewer blocks release, opens risk/CAPA, links run ID and source-kind report | pending exercise | pending | pending run ID | 僅計劃 |

The tabletop is intentionally not marked complete. A future run must record expected behavior, actual behavior, deviation, timestamps and exact evidence URI from a replayable TrustForge formal analysis.
