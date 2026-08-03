# AIMS 風險方法與登錄草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-RISK-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 CEO 指派／待 CEO、CPO、CISO 與 Compliance Counsel 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立風險方法、taxonomy、register 與處置介面草案／not-applicable（初版） |
| Repository path | `docs/aims/03-risk/risk-methodology-and-register.md` |

本文件回應 #1244。它只建立 TrustForge AIMS 風險工作的受控草案，不表示任何風險已被接受、控制已有效，或 EU AI Act / EN 18286 條款已完成法律判定。

## 分級方法

| 欄位 | 草案規則 | 狀態 |
|---|---|---|
| Likelihood | 1 rare、2 unlikely、3 possible、4 likely、5 frequent；評分來源必須連 evidence URI | 僅計劃 |
| Impact | 1 negligible、2 minor、3 moderate、4 major、5 severe；需分別描述 business、customer、individual、regulatory、security impact | 僅計劃 |
| Inherent risk | `likelihood * impact`，未套用處置前評估 | 僅計劃 |
| Residual risk | 套用已驗證控制後重評；草案控制不得降低分數 | 僅計劃 |
| P0 | residual score >= 20，或任何未緩解 severe individual/regulatory/security impact | 僅計劃 |
| P1 | residual score 12-19，或有明確客戶、法規、資安 exposure | 僅計劃 |
| P2 | residual score 6-11 | 僅計劃 |
| P3 | residual score 1-5 | 僅計劃 |

## 升級、cadence 與接受權限

| 條件 | 處置 | 接受權限 | Cadence |
|---|---|---|---|
| P0 | 立即 containment、CEO/CISO/CPO escalation、CAPA 開案 | CEO + 對應 owner；不得無期限 accepted | 每日直到降級或關閉 |
| P1 | 指派 owner、期限、處置方案與 residual decision | CEO 或 delegated risk owner；Security/Legal 類需 CISO/Compliance Counsel | 每週 |
| P2 | 納入 treatment plan 或 backlog，明確期限 | delegated risk owner | 每月 |
| P3 | 監測或接受；需理由與 review date | delegated risk owner | 每季 |

任何 accepted risk 都必須有 owner、期限、review date 與可撤銷條件。P0/P1 不得用 `accepted indefinitely`、`won't fix` 或缺 owner 的方式結案。

## Taxonomy

| 代碼 | 類別 | 範例來源 | 受影響對象 |
|---|---|---|---|
| RISK-MKT | 市場分析與錯誤決策 | 價格、新聞、on-chain、regulatory sentiment 解讀偏差 | 客戶、投資決策者 |
| RISK-DATA | 資料品質與來源可靠度 | stale snapshot、低信任來源、source-kind imbalance | 客戶、分析 reviewer |
| RISK-MODEL | 模型與 AI agent 行為 | hallucination、unsupported claim、overconfident summary | 客戶、內部操作人員 |
| RISK-SEC | 安全與憑證 | secret exposure、stale credential cache、unauthorized admin action | HurricaneSoft、客戶 |
| RISK-LEGAL | 法規與聲明 | EU role misclassification、unsupported conformity claim | HurricaneSoft、客戶、EU users |
| RISK-OPS | 運維與事件 | failed job、missing evidence URI、broken release gate | HurricaneSoft |
| RISK-SUP | 供應商與第三方 | Bedrock、資料 provider、雲端服務變更 | HurricaneSoft、客戶 |

## Draft risk register

| Risk ID | Source | Scenario | Affected parties | Owner | Due date | Inherent | Controls | Treatment | Residual | Status | Evidence URI |
|---|---|---|---|---|---|---|---|---|---|---|---|
| AIMS-RISK-0001 | #1340 | Rich BTC snapshots may collapse into narrow price-only evidence, hiding diverse source kinds | 客戶、分析 reviewer | pending | pending | 4 x 4 = 16 | Claim extraction tests and source-kind report are proposed; no effectiveness evidence in this draft | mitigate; code issue remains separate | not scored; no verified control | 僅計劃 | `https://github.com/cancleeric/trustforge/issues/1340` |
| AIMS-RISK-0002 | #1264 | EU operator role or risk classification may be asserted before legal approval | HurricaneSoft、EU users、客戶 | pending Compliance Counsel | pending | 3 x 5 = 15 | EU overlay requires `pending` status and prohibits conformity claims | avoid unsupported claims; legal review required | not scored; no approval | 部分實作 | `docs/aims/03-eu-ai-act/` |
| AIMS-RISK-0003 | #1243 | Human oversight intervention and stop criteria are not consistently evidenced through lifecycle stages | 客戶、內部 operator | pending AIMS Manager | pending | 3 x 4 = 12 | Lifecycle matrix and tabletop are proposed | mitigate through LIFE work package | not scored; draft only | 僅計劃 | `docs/aims/06-lifecycle/lifecycle-control-matrix.md` |
| AIMS-RISK-0004 | #1245 | Audit or CAPA may be declared complete without independent auditor or closure approval | HurricaneSoft、客戶 | pending CEO | pending | 3 x 4 = 12 | Audit/CAPA schema prohibits self-audit and fake closure | readiness exercise only until auditor appointed | not scored; draft only | 僅計劃 | `docs/aims/09-audit/audit-programme.md` |

## Traceability requirements

- unique ID, source, affected parties, owner, due date, inherent risk, residual risk, controls, treatment, status and evidence URI
- links to impacted asset, impact assessment row, lifecycle control, supplier card when applicable, audit finding or CAPA when applicable
- explicit owner and deadline for every P0/P1 item
- accepted-risk approver, review date and revocation condition when risk is accepted

## Review blockers

- AIMS-GOV scope, RACI, asset fields and risk-acceptance interface remain unapproved.
- Compliance Counsel has not approved EU intended purpose, operator roles or risk classification.
- Harper/gray/security/product review and independent adversarial review are still pending.
