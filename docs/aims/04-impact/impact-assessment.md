# AIMS AI 影響評估草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-IMPACT-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 CPO 指派／待 CEO、CPO、CISO 與 Compliance Counsel 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立 TrustForge AI impact assessment 草案／not-applicable（初版） |
| Repository path | `docs/aims/04-impact/impact-assessment.md` |

本文件回應 #1244 的 impact assessment acceptance criteria。所有 row 都是 draft assessment input，不得作為控制有效、風險接受或法規適用性的證據。

| Impact ID | Related risk | Scenario | Individual | Group | Society | Misuse / foreseeable abuse | Human oversight | Status | Evidence URI |
|---|---|---|---|---|---|---|---|---|---|
| AIMS-IMP-0001 | AIMS-RISK-0001 | Market-analysis output overweights price evidence and underrepresents news/on-chain/regulatory signals | Potential financial decision harm if customers treat output as advice | Crypto market participants using same output pattern | Reduced trust in automated market analysis | Output may be reused as investment recommendation despite product limits | reviewer must inspect source-kind distribution before publication | 僅計劃 | `https://github.com/cancleeric/trustforge/issues/1340` |
| AIMS-IMP-0002 | AIMS-RISK-0002 | Unsupported EU AI Act conformity or CE-like claim appears in docs, UI or sales material | Misled EU users or customers | Customer compliance teams may rely on unsupported statement | Distorts regulatory trust signals | Marketing or reseller may quote draft material as approved | CPO/Compliance Counsel approval required before external claim | 部分實作 | `docs/aims/03-eu-ai-act/applicability-and-classification.md` |
| AIMS-IMP-0003 | AIMS-RISK-0003 | Human reviewer lacks clear stop/escalation authority during incident or drift event | Incorrect or unsafe analysis may be published | Customers may receive unsupported recommendations | Weakens accountability for AI-assisted analysis | Operator may bypass manual review under time pressure | escalation and stop criteria are required in lifecycle controls | 僅計劃 | `docs/aims/06-lifecycle/lifecycle-control-matrix.md` |
| AIMS-IMP-0004 | AIMS-RISK-0004 | CAPA is closed without root cause or effectiveness review | Repeated incident affects same customer | Repeated defect affects a customer segment | Weakens governance assurance | Readiness exercise may be misrepresented as independent audit | CEO/independent auditor closure approval required | 僅計劃 | `docs/aims/10-capa/capa-and-management-review.md` |

## Required evidence for future approval

- intended-purpose statement approved by CPO and Compliance Counsel
- product, marketing, contract and deployment facts used to bound foreseeable misuse
- exact source snapshots or run IDs for market-analysis examples
- reviewer authority matrix for intervention, escalation, stop and release approval
- trace from impact row to risk, lifecycle control, supplier/source card, audit finding and CAPA when applicable

## Non-claims

This assessment does not establish EU AI Act classification, Article 50 applicability, high-risk status, conformity assessment readiness, certification readiness or residual-risk acceptance.
