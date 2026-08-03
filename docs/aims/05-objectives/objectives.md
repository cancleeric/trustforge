# AIMS Objectives 草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-OBJ-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 CEO 指派／待 CEO 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立 AIMS objectives 與缺值規則草案／not-applicable（初版） |
| Repository path | `docs/aims/05-objectives/objectives.md` |

Objectives 只能使用可重演來源計算。若 baseline 或 target 尚未由 owner 核准，必須標 `pending` 或 `unknown`，不得用期望值冒充量測結果。

| Objective ID | Objective | Formula | Source | Baseline | Target | Owner | Frequency | Missing-data rule | Status |
|---|---|---|---|---|---|---|---|---|---|
| AIMS-OBJ-0001 | Evidence URI completeness | controls with reviewer-repeatable evidence URI / total controls | SoA and control matrices | pending | pending CEO approval | pending AIMS Manager | monthly | exclude only controls marked not-applicable with approver; otherwise count missing as gap | 僅計劃 |
| AIMS-OBJ-0002 | P0 overdue CAPA count | count of open P0 CAPA past due date | CAPA register | pending | 0 | pending CEO/CISO | weekly | unknown due date on P0 counts as overdue | 僅計劃 |
| AIMS-OBJ-0003 | Source-kind reporting coverage | reports exposing source-kind distribution / reports using multi-source snapshots | analysis report evidence | pending | pending CPO approval | pending product owner | monthly | sparse-data abstain cases tracked separately | 僅計劃 |
| AIMS-OBJ-0004 | Independent audit coverage | sampled controls audited by independent reviewer / controls in audit scope | audit programme | pending | pending CEO approval | pending independent auditor | per audit cycle | self-audited controls count as not covered | 僅計劃 |

Future approval must bind each objective to an owner, exact source query, baseline date, target date and management-review decision.
