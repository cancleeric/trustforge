# EN 18286 / EU AI Act QMS Overlay 草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-EU-OVERLAY-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 Compliance Counsel 指派／待 CEO、CPO、CISO、Compliance Counsel 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立 EN 18286 / EU AI Act QMS overlay Phase 0-1 草案／not-applicable（初版） |
| Repository path | `docs/aims/03-eu-ai-act/en-18286-qms-overlay.md` |

本文件回應 #1264，並連結 #1244、#1243、#1245、#1246。它只完成 Phase 0-1 骨架：來源限制、intended purpose、operator role、classification 與四向矩陣欄位。取得公司合法授權的 EN 18286 正式全文並由 Compliance Counsel 核准後，才可做條款級 Phase 2。

## Source and reliance limits

| Source | Current state | Permitted reliance | Prohibited use |
|---|---|---|---|
| `docs/plans/ANALYSIS-EN-18286-EU-AI-ACT-GAP-2026-08-01.md` | internal analysis draft | issue planning and gap framing | legal conclusion, conformity claim |
| EUR-Lex EU AI Act references in the analysis plan | preliminary article mapping only | article-level planning after exact-version recheck | final applicability decision without counsel |
| EN 18286 text | awaiting licensed text | none for clause-level mapping | reconstructing clauses from summaries, committing standard text |
| AIMS docs in this repo | draft / unapproved | readiness evidence skeleton | certification, CE, presumption or EU AI Act conformity |

## Intended-purpose statement

| Field | Draft statement | Owner | Approval |
|---|---|---|---|
| Product | TrustForge Hermes AI-assisted crypto market analysis and evidence review tooling | pending CPO | pending |
| Intended users | HurricaneSoft internal operators and authorized customers; exact EU deployment facts pending | pending CPO/Compliance Counsel | pending |
| Intended use | produce traceable analysis artifacts with evidence URI, uncertainty and reviewer oversight | pending CPO | pending |
| Not intended use | investment advice, autonomous trading, credit/insurance/employment/education/law-enforcement decisions, biometric identification or safety-component use | pending Compliance Counsel | pending |
| External claims | no CE, certification, presumption of conformity or EU AI Act conformity claim | pending CEO/Compliance Counsel | pending |

## Operator role assessment

| Role | Preliminary state | Evidence needed | Status |
|---|---|---|---|
| Provider | pending | EU placing-on-market facts, product contract model, substantial modification control | Not assessed |
| Deployer | pending | customer deployment responsibility, operational control and use context | Not assessed |
| Authorised representative | pending | provider establishment facts and Article 22 applicability | Not assessed |
| Importer | pending | EU supply chain and product import facts | Not assessed |
| Distributor | pending | reseller, white-label and distribution facts | Not assessed |

## Applicability and reclassification triggers

| Area | Trigger | Required action | Status |
|---|---|---|---|
| Article 6 / Annex I | product becomes safety component or is used with covered product pathway | Compliance Counsel classification review before release | Not assessed |
| Annex III | intended purpose enters listed high-risk use case | CPO + Compliance Counsel + CEO approval before external use | Not assessed |
| Article 4 | users/operators need AI literacy obligations evaluated | define training evidence and owner | Not assessed |
| Article 50 | system output or interaction triggers transparency obligations | separate feature/output/deployment assessment | Not assessed |
| Article 25 | customer or HurricaneSoft substantial modification changes role/classification | change workflow and provider-transition assessment | Not assessed |
| Prohibited practices | use case resembles prohibited manipulation, exploitation, social scoring, biometric or predictive policing categories | stop release and legal escalation | Not assessed |

## Four-way matrix skeleton

| EN 18286 | EU AI Act | ISO/IEC 42001 / AIMS area | TrustForge evidence | State |
|---|---|---|---|---|
| awaiting licensed text | Articles 9-17 high-risk provider duties | Risk, lifecycle, data/source, technical docs, logging, oversight, robustness | `docs/aims/03-risk/`, `docs/aims/06-lifecycle/`, `docs/aims/07-suppliers/` | awaiting licensed text |
| awaiting licensed text | Article 4 AI literacy | Roles, competence and training records | owner/training evidence pending | awaiting licensed text |
| awaiting licensed text | Article 50 transparency | Intended purpose, output labeling, user instructions | `docs/aims/03-eu-ai-act/applicability-and-classification.md` | awaiting licensed text |
| awaiting licensed text | Articles 72-73 post-market monitoring and serious incidents | Monitoring, incident, CAPA, management review | `docs/aims/08-measurement/`, `docs/aims/10-capa/` | awaiting licensed text |
| awaiting licensed text | Conformity assessment, registration and CE | SoA and readiness gap review | `docs/aims/soa/statement-of-applicability.md` | awaiting licensed text |

## Work items before Phase 2

- acquire company-licensed authoritative EN 18286 text; do not commit the text to repo
- record exact version, citation and access owner
- obtain Compliance Counsel approval for intended purpose, operator roles and classification
- update crosswalk only with clause IDs permitted by license and counsel guidance
- complete CPO, CISO, Compliance Counsel, CEO and independent adversarial review on exact commit
