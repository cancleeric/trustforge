# AIMS 供應商與來源卡草案

| 欄位 | 值 |
|---|---|
| 文件 ID | AIMS-SUP-001 |
| 版本／狀態 | 0.1-draft／草案、未核准 |
| Owner／核准者 | 待 supplier owner 指派／待 CEO、CISO、Compliance Counsel 核准 |
| 核准紀錄／生效日 | pending／not-applicable（草案） |
| Review / next review | 待核准時設定／待核准時設定 |
| 分類 | internal-draft |
| 變更摘要／取代文件 | 建立 data/model/vendor/source cards 草案／not-applicable（初版） |
| Repository path | `docs/aims/07-suppliers/supplier-and-source-cards.md` |

Cards 僅記錄可查證資料。未知資料必須標 `unknown` 或 `todo`，不得由 README、issue 或口頭描述推論合約權利、資料授權、SLA、安全認證或控制有效性。

## Card schema

| 欄位 | 說明 |
|---|---|
| Card ID | stable ID |
| Kind | model、cloud、market-data、news、on-chain、regulatory、internal |
| Provider/source | legal/provider name when verified; otherwise unknown |
| Purpose | TrustForge use case |
| Permitted use evidence | contract, license, public terms or pending |
| Data handled | data classes and PII status when verified |
| Change trigger | contract, API, model, dataset, trust score or region change |
| Risk links | risk IDs |
| Lifecycle controls | control IDs |
| Evidence URI | reviewer-repeatable source |
| Status | 已實作／部分實作／僅計劃／不適用 |

## Draft cards

| Card ID | Kind | Provider/source | Purpose | Permitted use evidence | Data handled | Change trigger | Risk links | Lifecycle controls | Evidence URI | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| AIMS-SUP-MODEL-0001 | model | AWS Bedrock provider exact model pending | AI-assisted market analysis and review support | pending contract/version evidence | prompts, analysis context; PII status pending | model ID/version/region/contract change | AIMS-RISK-0002, AIMS-RISK-0003 | AIMS-LIFE-CHG-001 | pending | 僅計劃 |
| AIMS-SUP-DATA-0001 | market-data | defillama-price observed in issue evidence; provider terms pending | price context for BTC analysis | pending | market price data | API/source schema/trust score change | AIMS-RISK-0001 | AIMS-LIFE-DAT-001 | `https://github.com/cancleeric/trustforge/issues/1340` | 部分實作 |
| AIMS-SUP-DATA-0002 | news | exact news sources pending | market news context | pending | public news metadata/content; license pending | source addition/removal/license change | AIMS-RISK-0001 | AIMS-LIFE-DAT-001 | pending | 僅計劃 |
| AIMS-SUP-DATA-0003 | on-chain | exact on-chain providers pending | blockchain signal context | pending | public blockchain-derived metrics; terms pending | provider/schema/trust score change | AIMS-RISK-0001 | AIMS-LIFE-DAT-001 | pending | 僅計劃 |
| AIMS-SUP-DATA-0004 | regulatory | exact regulatory sentiment sources pending | regulatory context | pending | public regulatory text/metadata; license pending | source jurisdiction or interpretation change | AIMS-RISK-0001, AIMS-RISK-0002 | AIMS-LIFE-DAT-001 | pending | 僅計劃 |

## Required controls before approval

- legal right to use each source for TrustForge intended purpose
- owner and review cadence for each material provider/source
- change notification or monitoring trigger
- incident escalation path for source outage, trust-score drop or license concern
- evidence URI that a stranger can replay without private credentials unless access control is documented
