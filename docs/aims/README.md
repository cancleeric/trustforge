# TrustForge AIMS 治理文件

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-INDEX-001 |
| 版本 | 0.1-draft |
| 狀態 | 草案／未核准 |
| Owner | 待 CEO 指派 |
| 核准者 | 待 CEO 核准 |
| 核准紀錄 | pending（草案不得以 commit／merge 代替核准） |
| 生效日 | not-applicable（草案） |
| Review date | 待核准時設定 |
| Next review date | 待核准時設定 |
| 分類 | internal-draft |
| 變更摘要 | 建立 AIMS GOV 文件索引與狀態語意 |
| 取代文件 | not-applicable（初版） |
| Repository path | `docs/aims/README.md` |

> 本目錄是 ISO/IEC 42001 AIMS 改善工作的內部草案，不是認證、符合性或控制有效性聲明。
> 除非文件附有可驗證的核准紀錄，所有內容都視為未核准。

## GOV 最小交付

- [AIMS 範圍](01-scope/scope.md)：邊界、納入與排除項目。
- [組織情境與利害關係人](01-scope/context-and-stakeholders.md)：議題與需求登錄。
- [AI 政策](02-policy/ai-policy.md)：治理原則草案。
- [角色、RACI 與風險接受](02-policy/roles-raci-and-risk-acceptance.md)：責任介面及殘餘風險權限。
- [文件控制與 evidence manifest](02-policy/document-control.md)：最低 metadata、狀態及證據欄位。
- [AI 系統／資產清冊](02-policy/ai-system-inventory.md)：最低欄位與 TrustForge 初始登錄。
- [EU AI Act／EN 18286 overlay](03-eu-ai-act/README.md)：適用性、分類與四向矩陣；EN bibliographic／publication／official status、合法全文、產品／契約／部署事實及合規核准均待 authoritative evidence。#1265／PR-A 不代表父 issue #1264 全部完成。
- [支援控制](05-support/README.md)：能力、訓練、文件生命週期與內外溝通控制草案。
- [生命週期控制矩陣](06-lifecycle/lifecycle-control-matrix.md)：設計、資料、開發、驗證、發布、運行、監測、事件、變更與退役控制草案。
- [供應商與來源卡](07-suppliers/supplier-and-source-cards.md)：資料源、模型、工具、credential boundary 與 review 狀態草案。
- [量測與監控](08-measurement/kpi-and-monitoring-register.md)：KPI、公式、來源、baseline、target 與缺值規則草案。
- [內部稽核](09-audit/audit-programme-and-report.md)：audit programme、finding 欄位與獨立性要求草案。
- [CAPA 與管理審查](10-capa/capa-register-and-management-review.md)：CAPA register、management review pack 與不宣稱有效性的結論邊界草案。

## 共通狀態語意

| 狀態 | 語意 |
|---|---|
| 已實作 | 有可定位證據，且已完成適用的核准與有效性驗證 |
| 部分實作 | 有部分可定位事實，但介面、核准或有效性驗證尚未完成 |
| 僅計劃 | 尚無實作證據，不得推論控制存在 |
| 不適用 | 已記錄範圍判定及理由，且由有權者核准 |

## Extended draft work packages

- [風險方法與登錄](03-risk/risk-methodology-and-register.md)：risk taxonomy、分級、升級門檻、接受權限與 draft register。
- [影響評估](04-impact/impact-assessment.md)：個人、群體、社會、可預見濫用與人工監督草案。
- [AIMS objectives](05-objectives/objectives.md)：公式、來源、baseline、target、owner、頻率與缺值規則草案。
- [生命週期控制矩陣](06-lifecycle/lifecycle-control-matrix.md)：設計、資料取得、開發、驗證、發布、運行、監測、事件、變更與退役控制。
- [供應商與來源卡](07-suppliers/supplier-and-source-cards.md)：模型、雲端、市場資料、新聞、on-chain 與 regulatory source cards。
- [量測與監測](08-measurement/kpi-and-monitoring.md)：KPI、monitoring event 與缺值處理。
- [內稽計畫](09-audit/audit-programme.md)：audit programme、auditor independence 與 finding schema。
- [CAPA 與管理審查](10-capa/capa-and-management-review.md)：CAPA register、closure rules 與 management review pack。
- [SoA 與 readiness](soa/statement-of-applicability.md)：SoA、traceability 與 independent gap review skeleton。

本版新增的 RISK、LIFE、MEASURE/CAPA、SoA 與 EN 18286 overlay 文件皆為草案。
它們只建立 reviewer 可檢查的欄位、狀態語意與 traceability 骨架，不代表控制已實作、
稽核已完成、殘餘風險已接受、CE 標示、presumption of conformity、EU AI Act conformity
或 ISO/IEC 42001 認證。涉及法規、資安、產品定位或殘餘高風險的判定，仍需 CEO、CPO、
CISO、Compliance Counsel 與獨立 reviewer 在 exact commit 上核准。

競賽符合性與 AIMS 改善是不同追溯軸；前者不得作為 ISO/IEC 42001 認證證據。
